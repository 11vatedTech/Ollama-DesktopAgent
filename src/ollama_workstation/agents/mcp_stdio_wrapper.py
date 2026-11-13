"""Cross-platform stdio supervisor for host-owned MCP process-tree cleanup.

The SDK starts this module as its direct child. On Windows it owns a second
kill-on-close Job Object containing the real server. On POSIX it starts the
server in a dedicated process group and always terminates that group on exit,
including after a graceful server exit. This closes the SDK gap where a server
that exits within the graceful shutdown window leaves descendants behind.

The wrapper is a transparent byte proxy. It writes nothing to stdout except the
server's own protocol output.

Usage (constructed by MCPServerSpec.client_target):
    python -m ollama_workstation.agents.mcp_stdio_wrapper -- COMMAND [ARG...]
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from typing import Any

POSIX_TERMINATION_GRACE_SECONDS = 1.0
GROUP_POLL_INTERVAL = 0.02


def _create_job() -> Any | None:
    try:
        import win32job as _win32job
    except ImportError:
        return None
    # The published pywin32 stubs declare CreateJobObject as returning None.
    win32job: Any = _win32job
    try:
        job = win32job.CreateJobObject(None, "")
        info = win32job.QueryInformationJobObject(
            job, win32job.JobObjectExtendedLimitInformation
        )
        info["BasicLimitInformation"]["LimitFlags"] |= (
            win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        win32job.SetInformationJobObject(
            job, win32job.JobObjectExtendedLimitInformation, info
        )
        return job
    except Exception:
        return None


def _assign_to_job(job: Any, pid: int) -> None:
    try:
        import win32api
        import win32con
        import win32job

        handle = win32api.OpenProcess(
            win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE,
            False,
            pid,
        )
        if handle:
            try:
                win32job.AssignProcessToJobObject(job, handle)
            finally:
                win32api.CloseHandle(handle)
    except Exception:
        # The SDK's own job remains in place as the fallback.
        pass


def _close_job(job: Any | None) -> None:
    if job is None:
        return
    try:
        import win32api
        import win32job

        try:
            win32job.TerminateJobObject(job, 1)
        finally:
            win32api.CloseHandle(job)
    except Exception:
        pass


def _group_alive(pgid: int) -> bool:
    """Probe a POSIX process group; only ESRCH proves it is gone."""
    if sys.platform == "win32":
        return False
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_posix_group(pid: int) -> None:
    """Terminate the server's dedicated process group, including descendants."""
    if pid <= 0 or sys.platform == "win32":
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        pass
    deadline = time.monotonic() + POSIX_TERMINATION_GRACE_SECONDS
    while time.monotonic() < deadline:
        if not _group_alive(pid):
            return
        time.sleep(GROUP_POLL_INTERVAL)
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _stop_child(child: subprocess.Popen[bytes], job: Any | None) -> None:
    if os.name == "nt":
        _close_job(job)
        if job is None:
            try:
                child.terminate()
            except OSError:
                pass
        return
    _terminate_posix_group(child.pid)


def _install_shutdown_handlers() -> None:
    """Make SDK SIGTERM/SIGINT/SIGHUP shutdown pass through the cleanup path."""
    if os.name == "nt":
        return

    def _exit_through_finally(signum: int, _frame: Any) -> None:
        raise SystemExit(128 + signum)

    for name in ("SIGTERM", "SIGINT", "SIGHUP"):
        signum = getattr(signal, name, None)
        if signum is None:
            continue
        try:
            signal.signal(signum, _exit_through_finally)
        except (OSError, ValueError):
            pass


def _pump_stdin(child: subprocess.Popen[bytes]) -> None:
    assert child.stdin is not None
    try:
        while True:
            chunk = os.read(0, 65_536)
            if not chunk:
                break
            child.stdin.write(chunk)
            child.stdin.flush()
    except (BrokenPipeError, OSError):
        pass
    finally:
        try:
            child.stdin.close()
        except OSError:
            pass


def _pump_stdout(child: subprocess.Popen[bytes]) -> None:
    assert child.stdout is not None
    try:
        while True:
            chunk = os.read(child.stdout.fileno(), 65_536)
            if not chunk:
                break
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
    except (BrokenPipeError, OSError):
        pass


def main() -> int:
    if len(sys.argv) < 3 or sys.argv[1] != "--":
        print("mcp_stdio_wrapper: expected -- COMMAND [ARG...]", file=sys.stderr)
        return 2
    command = sys.argv[2:]
    job = _create_job()
    _install_shutdown_handlers()
    try:
        child = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            bufsize=0,
            env=os.environ.copy(),
            start_new_session=(os.name != "nt"),
        )
    except OSError as exc:
        _close_job(job)
        print(f"mcp_stdio_wrapper: failed to start server: {exc}", file=sys.stderr)
        return 127
    except BaseException:
        _close_job(job)
        raise
    if job is not None:
        _assign_to_job(job, child.pid)
    threading.Thread(target=_pump_stdin, args=(child,), daemon=True).start()
    stdout_pump = threading.Thread(target=_pump_stdout, args=(child,), daemon=True)
    stdout_pump.start()
    try:
        code = int(child.wait())
    finally:
        # The server may have exited cleanly while its descendants remain.
        _stop_child(child, job)
    # Drain remaining protocol output before the wrapper exits.
    stdout_pump.join(timeout=2.0)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
