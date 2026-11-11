"""Real-transport MCP certification tests.

These tests launch the official MCP SDK v2 server over stdio and Streamable
HTTP. No fake client is used: discovery, JSON-RPC framing, tool invocation,
timeouts, cancellation, transport death, reconnection, redaction and process
cleanup all cross the real transport boundary.
"""

from __future__ import annotations

import ctypes
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from ollama_workstation.agents.mcp_contract import MCP_CONTRACT_VERSION
from ollama_workstation.agents.mcp_gateway import (
    MCPConnectionError,
    MCPConnectionState,
    MCPToolGateway,
)
from ollama_workstation.agents.tools import ToolError

pytest.importorskip("mcp", reason="real MCP transport tests require the optional mcp extra")

FIXTURE = Path(__file__).parent / "fixtures" / "mcp_certification_server.py"
INVALID_FIXTURE = Path(__file__).parent / "fixtures" / "mcp_invalid_server.py"
ALL_TOOLS = (
    "echo",
    "add",
    "slow",
    "fail_after_side_effect",
    "side_effect_count",
    "crash",
    "big_result",
    "call_count",
    "pid",
    "env_present",
    "return_secret",
    "spawn_grandchild",
    "slow_count",
)

_KERNEL32: Any = None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _win32_kernel() -> Any:
    global _KERNEL32
    if _KERNEL32 is None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.GetExitCodeProcess.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ulong),
        ]
        kernel32.GetExitCodeProcess.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        _KERNEL32 = kernel32
    return _KERNEL32


def _process_alive(pid: int) -> bool:
    """Check liveness without terminating the process on Windows."""
    if pid <= 0:
        return False
    if os.name == "nt":
        kernel32 = _win32_kernel()
        handle = kernel32.OpenProcess(0x1000, 0, int(pid))  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        try:
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return bool(ok) and code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A zombie still answers signal 0 on POSIX but has already exited.
    try:
        state = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()[2]
        return state != "Z"
    except (OSError, IndexError):
        return True


def _force_kill(pid: int) -> None:
    if os.name == "nt":
        kernel32 = _win32_kernel()
        handle = kernel32.OpenProcess(0x0001, 0, int(pid))  # PROCESS_TERMINATE
        if handle:
            kernel32.TerminateProcess(handle, 1)
            kernel32.CloseHandle(handle)
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _wait_dead(pid: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _process_alive(pid):
            return
        time.sleep(0.05)
    _force_kill(pid)
    pytest.fail(f"process {pid} was still alive after {timeout:.1f}s")


def _terminate_tree(pid: int) -> None:
    """Stop an owned fixture process tree without relying on a name pattern."""
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=10,
            )
        except OSError:
            pass
    else:
        try:
            os.killpg(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            try:
                os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not _process_alive(pid):
            return
        time.sleep(0.05)
    _force_kill(pid)


def _stdio_config(
    *,
    name: str = "cert",
    tools: tuple[str, ...] = ALL_TOOLS,
    timeout: float = 10.0,
    env_refs: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    return [{
        "name": name,
        "transport": "stdio",
        "enabled": True,
        "command": sys.executable,
        "args": [str(FIXTURE), "--transport", "stdio"],
        "env_refs": dict(env_refs or {}),
        "approved_tools": list(tools),
        "call_timeout_seconds": timeout,
    }]


def _http_config(
    port: int,
    *,
    tools: tuple[str, ...] = ALL_TOOLS,
    timeout: float = 10.0,
) -> list[dict[str, Any]]:
    return [{
        "name": "remote",
        "transport": "streamable_http",
        "enabled": True,
        "url": f"http://127.0.0.1:{port}/mcp",
        "approved_tools": list(tools),
        "call_timeout_seconds": timeout,
    }]


def _text(result: dict[str, Any]) -> str:
    content = result.get("content")
    assert isinstance(content, list) and content, result
    return str(content[0].get("text", ""))


def _value(result: dict[str, Any]) -> Any:
    structured = result.get("structured_content")
    if isinstance(structured, dict) and "result" in structured:
        return structured["result"]
    return structured


def _call(gateway: MCPToolGateway, server: str, tool: str,
          arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    return gateway.invoke(f"mcp__{server}__{tool}", arguments or {})


class HTTPFixture:
    """Own a real uvicorn-hosted MCP server on a fixed loopback port."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.port = _free_port()
        self.process: subprocess.Popen[bytes] | None = None
        self.server_pid: int | None = None
        self.pid_file = root / f"http-{self.port}.pid"
        self._log_handle: Any = None
        self.log_path = root / f"http-{self.port}.log"

    def start(self, *, variant: str = "stable") -> HTTPFixture:
        if self.process is not None:
            raise RuntimeError("HTTP fixture is already running")
        self.pid_file.unlink(missing_ok=True)
        self.log_path = self.root / f"http-{self.port}-{time.time_ns()}.log"
        self._log_handle = self.log_path.open("wb")
        env = dict(os.environ)
        env["MCP_CERT_VARIANT"] = variant
        self.process = subprocess.Popen(
            [
                sys.executable,
                str(FIXTURE),
                "--transport",
                "http",
                "--port",
                str(self.port),
                "--pid-file",
                str(self.pid_file),
            ],
            stdin=subprocess.DEVNULL,
            stdout=self._log_handle,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=(os.name != "nt"),
        )
        try:
            self._wait_ready()
        except Exception:
            self.stop()
            raise
        return self

    def _wait_ready(self) -> None:
        assert self.process is not None
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise AssertionError(
                    f"MCP HTTP server exited with {self.process.returncode}:\n{self.logs()}"
                )
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.25):
                    if self.pid_file.exists():
                        self.server_pid = int(self.pid_file.read_text(encoding="utf-8"))
                        return
            except OSError:
                pass
            time.sleep(0.05)
        raise AssertionError(f"MCP HTTP server did not listen on {self.port}:\n{self.logs()}")

    def logs(self) -> str:
        try:
            return self.log_path.read_text(encoding="utf-8", errors="replace")[-8_000:]
        except OSError:
            return "<no log>"

    def stop(self) -> None:
        process = self.process
        self.process = None
        pids = {pid for pid in (process.pid if process is not None else None, self.server_pid) if pid}
        for pid in pids:
            _terminate_tree(pid)
        if process is not None:
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                _force_kill(process.pid)
                process.wait(timeout=8)
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None
        self.server_pid = None

    def __enter__(self) -> HTTPFixture:
        return self.start()

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.stop()


def test_stdio_initialize_discovery_invoke_and_shutdown() -> None:
    with MCPToolGateway(
        _stdio_config(tools=("echo", "add", "pid")),
        ready_timeout_seconds=20,
    ) as gateway:
        names = [entry["function"]["name"] for entry in gateway.definitions()]
        assert names == sorted(("mcp__cert__add", "mcp__cert__echo", "mcp__cert__pid"))
        assert _text(_call(gateway, "cert", "echo", {"text": "hello"})) == "hello"
        assert _value(_call(gateway, "cert", "add", {"a": 2, "b": 3})) == 5
        child_pid = int(_value(_call(gateway, "cert", "pid")))
        assert _process_alive(child_pid)
        report = gateway.diagnostics()
        assert report["contract_version"] == MCP_CONTRACT_VERSION
        server = report["servers"][0]
        assert server["state"] == "ready"
        assert server["exposed_tools"] == 3
        assert server["transport_contract"]["transport"] == "stdio"
        assert server["transport_contract"]["server_process_owner"] == "client"
    _wait_dead(child_pid)


def test_http_initialize_discovery_invoke_and_session_shutdown(tmp_path: Path) -> None:
    server = HTTPFixture(tmp_path).start()
    assert server.process is not None
    assert server.server_pid is not None
    server_pid = server.server_pid
    try:
        with MCPToolGateway(
            _http_config(server.port, tools=("echo", "pid")),
            ready_timeout_seconds=20,
        ) as gateway:
            assert _text(_call(gateway, "remote", "echo", {"text": "over-http"})) == "over-http"
            assert int(_value(_call(gateway, "remote", "pid"))) == server_pid
            assert gateway.diagnostics()["servers"][0]["state"] == "ready"
        assert _process_alive(server_pid), "closing the client must not stop an operator-owned server"
    finally:
        server.stop()
    _wait_dead(server_pid)


def test_process_tree_is_reaped_on_stdio_shutdown() -> None:
    with MCPToolGateway(
        _stdio_config(tools=("pid", "spawn_grandchild")),
        ready_timeout_seconds=20,
    ) as gateway:
        parent = int(_value(_call(gateway, "cert", "pid")))
        grandchild = int(_value(_call(gateway, "cert", "spawn_grandchild")))
        assert _process_alive(parent)
        assert _process_alive(grandchild)
    _wait_dead(parent)
    _wait_dead(grandchild)


def test_repeated_initialization_and_shutdown_has_no_orphans() -> None:
    pids: list[int] = []
    for _ in range(3):
        with MCPToolGateway(
            _stdio_config(tools=("pid",)), ready_timeout_seconds=20
        ) as gateway:
            pids.append(int(_value(_call(gateway, "cert", "pid"))))
    assert len(set(pids)) == 3
    for pid in pids:
        _wait_dead(pid)


def test_cancellation_interrupts_active_call_and_reaps_child() -> None:
    cancel = threading.Event()
    gateway = MCPToolGateway(
        _stdio_config(tools=("pid", "slow"), timeout=10),
        cancel_event=cancel,
        ready_timeout_seconds=20,
    )
    child_pid = 0
    try:
        gateway.__enter__()
        child_pid = int(_value(_call(gateway, "cert", "pid")))
        errors: list[BaseException] = []

        def invoke() -> None:
            try:
                _call(gateway, "cert", "slow", {"seconds": 30})
            except BaseException as exc:  # noqa: BLE001 - the worker must report, not escape
                errors.append(exc)

        worker = threading.Thread(target=invoke)
        worker.start()
        time.sleep(0.4)
        cancel.set()
        worker.join(timeout=5)
        assert not worker.is_alive(), "cancellation blocked on the MCP transport"
        assert errors
        assert "cancel" in str(errors[0]).lower()
    finally:
        gateway.close()
    _wait_dead(child_pid)


def test_client_shutdown_during_active_call_returns_promptly() -> None:
    gateway = MCPToolGateway(
        _stdio_config(tools=("pid", "slow"), timeout=10),
        ready_timeout_seconds=20,
    )
    child_pid = 0
    try:
        gateway.__enter__()
        child_pid = int(_value(_call(gateway, "cert", "pid")))
        errors: list[BaseException] = []

        def invoke() -> None:
            try:
                _call(gateway, "cert", "slow", {"seconds": 30})
            except BaseException as exc:  # noqa: BLE001 - the worker must report, not escape
                errors.append(exc)

        worker = threading.Thread(target=invoke)
        worker.start()
        time.sleep(0.4)
        started = time.monotonic()
        gateway.close()
        assert time.monotonic() - started < 6
        worker.join(timeout=5)
        assert not worker.is_alive(), "client shutdown left the in-flight call blocked"
        assert errors
        assert "closed" in str(errors[0]).lower() or "cancel" in str(errors[0]).lower()
    finally:
        gateway.close()
    _wait_dead(child_pid)


def test_timeout_is_bounded_and_never_replayed(tmp_path: Path) -> None:
    with HTTPFixture(tmp_path) as server:
        with MCPToolGateway(
            _http_config(server.port, tools=("slow", "slow_count"), timeout=3),
            ready_timeout_seconds=20,
        ) as gateway:
            assert _value(_call(gateway, "remote", "slow_count")) == 0
            started = time.monotonic()
            with pytest.raises(ToolError, match="exceeded"):
                _call(gateway, "remote", "slow", {"seconds": 30})
            assert time.monotonic() - started < 8
            assert gateway.diagnostics()["servers"][0]["state"] == "failed"
            # The timed-out invocation must not be resent. Reconnect only makes
            # the next, distinct request possible, and the server saw exactly
            # one slow invocation.
            assert _value(_call(gateway, "remote", "slow_count")) == 1
            report = gateway.diagnostics()["servers"][0]
            assert report["state"] == "ready"
            assert report["reconnects"] == 1


def test_stdio_crash_is_detected_and_explicit_reconnect_uses_a_new_process() -> None:
    with MCPToolGateway(
        _stdio_config(tools=("pid", "echo", "crash")),
        ready_timeout_seconds=20,
    ) as gateway:
        old_pid = int(_value(_call(gateway, "cert", "pid")))
        with pytest.raises(ToolError, match="not be retried"):
            _call(gateway, "cert", "crash")
        assert gateway.diagnostics()["servers"][0]["state"] == "failed"
        gateway.reconnect("cert")
        new_pid = int(_value(_call(gateway, "cert", "pid")))
        assert new_pid != old_pid
        assert _text(_call(gateway, "cert", "echo", {"text": "after-reconnect"})) == "after-reconnect"
        assert gateway.diagnostics()["servers"][0]["reconnects"] == 1
    _wait_dead(old_pid)
    _wait_dead(new_pid)


def test_http_transport_death_is_unknown_outcome_and_next_call_reconnects(
    tmp_path: Path,
) -> None:
    with HTTPFixture(tmp_path) as server:
        gateway = MCPToolGateway(
            _http_config(server.port, tools=("echo", "call_count"), timeout=3),
            ready_timeout_seconds=20,
        )
        try:
            gateway.__enter__()
            assert _value(_call(gateway, "remote", "call_count", {"label": "before"})) == 1
            server.stop()
            with pytest.raises(ToolError):
                _call(gateway, "remote", "echo", {"text": "while-dead"})
            assert gateway.diagnostics()["servers"][0]["state"] == "failed"
            server.start()
            assert _text(_call(gateway, "remote", "echo", {"text": "back"})) == "back"
            report = gateway.diagnostics()["servers"][0]
            assert report["state"] == "ready"
            assert report["reconnects"] == 1
        finally:
            gateway.close()
            server.stop()


def test_partial_tool_failure_preserves_error_and_side_effect() -> None:
    with MCPToolGateway(
        _stdio_config(tools=("fail_after_side_effect", "side_effect_count")),
        ready_timeout_seconds=20,
    ) as gateway:
        failed = _call(gateway, "cert", "fail_after_side_effect")
        assert failed["is_error"] is True
        assert _value(_call(gateway, "cert", "side_effect_count")) == 1
        assert gateway.diagnostics()["servers"][0]["state"] == "ready"


def test_large_results_are_bounded_and_real_secrets_are_redacted() -> None:
    with MCPToolGateway(
        _stdio_config(tools=("big_result", "return_secret")),
        ready_timeout_seconds=20,
        result_limit_chars=1_000,
    ) as gateway:
        large = _call(gateway, "cert", "big_result", {"chars": 40_000})
        assert large["truncated"] is True
        assert large["original_characters"] > 1_000
        secret_result = _call(gateway, "cert", "return_secret")
        encoded = json.dumps(secret_result)
        assert "sk-live-certification-secret" not in encoded
        assert "[REDACTED]" in encoded


def test_environment_references_are_explicit_and_unreferenced_host_values_do_not_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_CERT_TOKEN", "token-value-must-not-leak")
    monkeypatch.setenv("MCP_CERT_UNREFERENCED", "must-not-reach-child")
    with MCPToolGateway(
        _stdio_config(
            tools=("env_present",),
            env_refs={"MCP_CHILD_TOKEN": "MCP_CERT_TOKEN"},
        ),
        ready_timeout_seconds=20,
    ) as gateway:
        assert _value(_call(gateway, "cert", "env_present", {"name": "MCP_CHILD_TOKEN"})) is True
        assert _value(_call(gateway, "cert", "env_present", {"name": "MCP_CERT_UNREFERENCED"})) is False
        assert "token-value-must-not-leak" not in json.dumps(gateway.diagnostics())


def test_concurrent_calls_and_separate_stdio_servers_are_isolated() -> None:
    with MCPToolGateway(
        _stdio_config(name="first", tools=("echo", "call_count", "pid")),
        ready_timeout_seconds=20,
    ) as first, MCPToolGateway(
        _stdio_config(name="other", tools=("call_count", "pid")),
        ready_timeout_seconds=20,
    ) as second:
        with ThreadPoolExecutor(max_workers=6) as pool:
            texts = list(pool.map(
                lambda value: _text(_call(first, "first", "echo", {"text": value})),
                [str(index) for index in range(8)],
            ))
        assert sorted(texts) == sorted(str(index) for index in range(8))
        assert _value(_call(first, "first", "call_count", {"label": "same"})) == 1
        assert _value(_call(first, "first", "call_count", {"label": "same"})) == 2
        assert _value(_call(second, "other", "call_count", {"label": "same"})) == 1
        first_pid = int(_value(_call(first, "first", "pid")))
        second_pid = int(_value(_call(second, "other", "pid")))
        assert first_pid != second_pid
    _wait_dead(first_pid)
    _wait_dead(second_pid)


def test_duplicate_requests_are_forwarded_once_each() -> None:
    with MCPToolGateway(
        _stdio_config(tools=("call_count",)), ready_timeout_seconds=20
    ) as gateway:
        arguments = {"label": "duplicate"}
        assert _value(_call(gateway, "cert", "call_count", arguments)) == 1
        assert _value(_call(gateway, "cert", "call_count", arguments)) == 2


def test_invalid_protocol_fails_closed_and_reaps_the_child(tmp_path: Path) -> None:
    pid_file = tmp_path / "invalid-server.pid"
    config = [{
        "name": "invalid",
        "transport": "stdio",
        "enabled": True,
        "command": sys.executable,
        "args": [str(INVALID_FIXTURE), str(pid_file)],
        "approved_tools": ["echo"],
        "call_timeout_seconds": 3,
    }]
    with pytest.raises(MCPConnectionError, match="failed to connect|did not connect"):
        MCPToolGateway(config, ready_timeout_seconds=3).__enter__()
    assert pid_file.exists(), "the invalid server did not start, so cleanup was not exercised"
    _wait_dead(int(pid_file.read_text(encoding="utf-8")))


def test_reconnect_refuses_a_changed_tool_schema(tmp_path: Path) -> None:
    with HTTPFixture(tmp_path) as server:
        gateway = MCPToolGateway(
            _http_config(server.port, tools=("variant_value",), timeout=3),
            ready_timeout_seconds=20,
        )
        try:
            gateway.__enter__()
            assert _value(_call(gateway, "remote", "variant_value", {"value": 41})) == 42
            server.stop()
            with pytest.raises(ToolError):
                _call(gateway, "remote", "variant_value", {"value": 1})
            server.start(variant="drift")
            with pytest.raises(ToolError, match="manifest"):
                _call(gateway, "remote", "variant_value", {"value": "text"})
            assert gateway.definitions() == []
            assert gateway.diagnostics()["servers"][0]["state"] == "not_connected"
            assert not any(
                connection.state is MCPConnectionState.READY
                for connection in gateway.connections.values()
            )
        finally:
            gateway.close()
            server.stop()
