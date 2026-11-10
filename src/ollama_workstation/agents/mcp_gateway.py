"""Least-privilege Model Context Protocol tool integration.

The optional official MCP Python SDK owns protocol and transport handling. This
module owns configuration validation, explicit tool grants, host-side argument
validation, result redaction, bounded calls and lifecycle management.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import shutil
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, SchemaError, ValidationError

from ollama_workstation.agents.tools import ToolError, WorkspaceTools


class MCPConfigurationError(ValueError):
    """Raised when an MCP server configuration is invalid or unsafe."""


class MCPConnectionError(RuntimeError):
    """Raised when an MCP server cannot be connected or queried."""


@dataclass(frozen=True, slots=True)
class MCPServerSpec:
    name: str
    transport: str
    enabled: bool = False
    command: str = ""
    args: tuple[str, ...] = ()
    url: str = ""
    env_refs: dict[str, str] = field(default_factory=dict)
    approved_tools: tuple[str, ...] = ()
    call_timeout_seconds: float = 45.0

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> MCPServerSpec:
        if not isinstance(payload, dict):
            raise MCPConfigurationError("Every MCP server entry must be a JSON object.")
        allowed = {
            "name", "transport", "enabled", "command", "args", "url",
            "env_refs", "approved_tools", "call_timeout_seconds",
        }
        extra = set(payload) - allowed
        if extra:
            raise MCPConfigurationError(
                "Unsupported MCP server configuration field(s): " + ", ".join(sorted(extra))
            )
        try:
            raw_args = payload.get("args", [])
            raw_env_refs = payload.get("env_refs", {})
            raw_approved = payload.get("approved_tools", [])
            if not isinstance(raw_args, list) or not all(isinstance(item, str) for item in raw_args):
                raise MCPConfigurationError("MCP args must be a JSON array of strings.")
            if not isinstance(raw_env_refs, dict):
                raise MCPConfigurationError("MCP env_refs must be a JSON object.")
            if not isinstance(raw_approved, list) or not all(isinstance(item, str) for item in raw_approved):
                raise MCPConfigurationError("MCP approved_tools must be a JSON array of exact tool-name strings.")
            spec = cls(
                name=str(payload.get("name") or ""),
                transport=str(payload.get("transport") or ""),
                enabled=payload.get("enabled", False),
                command=str(payload.get("command") or ""),
                args=tuple(raw_args),
                url=str(payload.get("url") or ""),
                env_refs=dict(raw_env_refs),
                approved_tools=tuple(raw_approved),
                call_timeout_seconds=float(payload.get("call_timeout_seconds", 45.0)),
            )
        except (TypeError, ValueError) as exc:
            raise MCPConfigurationError(f"Malformed MCP server configuration: {exc}") from exc
        spec.validate()
        return spec

    def validate(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", self.name):
            raise MCPConfigurationError(
                "MCP server name must start with a lowercase letter and contain only "
                "lowercase letters, digits, underscores or hyphens (maximum 32 characters)."
            )
        if self.transport not in {"stdio", "streamable_http"}:
            raise MCPConfigurationError("MCP transport must be 'stdio' or 'streamable_http'.")
        if not isinstance(self.enabled, bool):
            raise MCPConfigurationError("MCP enabled must be a boolean.")
        if not 3.0 <= self.call_timeout_seconds <= 300.0:
            raise MCPConfigurationError("MCP call timeout must be between 3 and 300 seconds.")
        if len(self.args) > 64 or any(not isinstance(arg, str) or len(arg) > 2_000 for arg in self.args):
            raise MCPConfigurationError("MCP stdio arguments must be at most 64 strings of 2,000 characters.")
        if len(set(self.approved_tools)) != len(self.approved_tools):
            raise MCPConfigurationError("MCP approved_tools cannot contain duplicates.")
        if any(not isinstance(name, str) or not name or len(name) > 128 for name in self.approved_tools):
            raise MCPConfigurationError("MCP approved tool names must be non-empty strings of at most 128 characters.")
        if "*" in self.approved_tools:
            raise MCPConfigurationError(
                "Wildcard MCP grants are not supported. Approve each tool by its exact server-side name."
            )
        if len(self.env_refs) > 32:
            raise MCPConfigurationError("At most 32 MCP environment references may be configured.")
        for key, source_name in self.env_refs.items():
            if not re.fullmatch(r"[A-Z_][A-Z0-9_]{0,127}", key):
                raise MCPConfigurationError(f"Invalid child-process environment key: {key!r}")
            if not isinstance(source_name, str) or not re.fullmatch(r"[A-Z_][A-Z0-9_]{0,127}", source_name):
                raise MCPConfigurationError(f"Invalid environment variable reference for {key!r}.")
        if self.transport == "stdio":
            if not self.command.strip():
                raise MCPConfigurationError("An MCP stdio server requires a command.")
            if self.url:
                raise MCPConfigurationError("A stdio MCP server must not define a URL.")
        else:
            if self.command or self.args or self.env_refs:
                raise MCPConfigurationError(
                    "A Streamable HTTP MCP server must use url only; command, args and env_refs are stdio-only."
                )
            self._validate_http_url(self.url)

    @staticmethod
    def _validate_http_url(value: str) -> None:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise MCPConfigurationError("MCP URL must be an absolute HTTP(S) URL.")
        if parsed.username or parsed.password or parsed.fragment:
            raise MCPConfigurationError("MCP URL must not contain embedded credentials or a fragment.")
        if parsed.scheme == "http":
            host = parsed.hostname.casefold()
            local_names = {"localhost", "localhost.localdomain"}
            is_loopback = host in local_names
            try:
                address = ipaddress.ip_address(host)
                is_loopback = address.is_loopback or address.is_private or address.is_link_local
            except ValueError:
                pass
            if not is_loopback:
                raise MCPConfigurationError(
                    "Unencrypted HTTP is allowed only for localhost or a private/loopback IP. "
                    "Use HTTPS for remote MCP servers."
                )

    def client_target(self) -> Any:
        """Build an SDK target while keeping secrets out of the saved configuration."""
        self.validate()
        try:
            from mcp import StdioServerParameters
        except ImportError as exc:
            raise MCPConnectionError(
                "MCP support is optional. Install this application with the 'mcp' extra."
            ) from exc
        if self.transport == "streamable_http":
            return self.url
        executable = shutil.which(self.command)
        if executable is None and Path(self.command).is_file():
            executable = str(Path(self.command).expanduser().resolve())
        if executable is None:
            raise MCPConfigurationError(
                f"MCP command {self.command!r} was not found on PATH or as an existing file."
            )
        child_env: dict[str, str] = {}
        for child_key, host_env_name in self.env_refs.items():
            value = os.environ.get(host_env_name)
            if value is None:
                raise MCPConfigurationError(
                    f"Host environment variable {host_env_name!r} is not set; "
                    "secret values are referenced by name and are never stored in settings."
                )
            child_env[child_key] = value
        return StdioServerParameters(
            command=executable,
            args=list(self.args),
            env=child_env or None,
        )


@dataclass(frozen=True, slots=True)
class MCPToolRoute:
    exposed_name: str
    server_name: str
    remote_name: str
    description: str
    input_schema: dict[str, Any]


class MCPServerConnection:
    """One persistent SDK client on a dedicated AnyIO/asyncio event-loop thread."""

    def __init__(
        self,
        spec: MCPServerSpec,
        *,
        client_factory: Callable[[MCPServerSpec], Any] | None = None,
        ready_timeout_seconds: float = 30.0,
    ) -> None:
        self.spec = spec
        self.client_factory = client_factory or self._default_client_factory
        self.ready_timeout_seconds = ready_timeout_seconds
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._serve_task: asyncio.Task[None] | None = None
        self._close_requested = threading.Event()
        self._client: Any = None
        self._stop_event: asyncio.Event | None = None
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self._tools: list[Any] = []
        self._closed = False

    @staticmethod
    def _default_client_factory(spec: MCPServerSpec) -> Any:
        try:
            from mcp import Client
        except ImportError as exc:
            raise MCPConnectionError(
                "MCP support is optional. Install this application with the 'mcp' extra."
            ) from exc
        return Client(spec.client_target())

    @property
    def tools(self) -> list[Any]:
        return list(self._tools)

    def start(self) -> list[Any]:
        if self._thread is not None:
            return self.tools
        self._thread = threading.Thread(
            target=self._thread_main, name=f"11vated-mcp-{self.spec.name}", daemon=True
        )
        self._thread.start()
        if not self._ready.wait(self.ready_timeout_seconds):
            self.close()
            raise MCPConnectionError(
                f"MCP server {self.spec.name!r} did not connect within "
                f"{self.ready_timeout_seconds:.0f} seconds."
            )
        if self._error is not None:
            raise MCPConnectionError(
                f"MCP server {self.spec.name!r} failed to connect: {self._error}"
            ) from self._error
        return self.tools

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            self._serve_task = loop.create_task(self._serve())
            if self._close_requested.is_set():
                self._serve_task.cancel()
            loop.run_until_complete(self._serve_task)
        except asyncio.CancelledError:
            pass
        except BaseException as exc:
            self._error = exc
            self._ready.set()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()

    async def _serve(self) -> None:
        try:
            client = self.client_factory(self.spec)
            async with client:
                self._client = client
                self._stop_event = asyncio.Event()
                tools: list[Any] = []
                cursor: str | None = None
                while True:
                    page = await client.list_tools(cursor=cursor)
                    tools.extend(page.tools)
                    cursor = getattr(page, "next_cursor", None)
                    if cursor is None:
                        break
                    if len(tools) > 500:
                        raise MCPConnectionError("MCP server returned more than 500 tools.")
                self._tools = tools
                self._ready.set()
                await self._stop_event.wait()
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            self._error = exc
            self._ready.set()
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        cancel_event: threading.Event | None = None,
    ) -> Any:
        if self._closed or self._loop is None or self._client is None:
            raise MCPConnectionError(f"MCP server {self.spec.name!r} is not connected.")
        coroutine = self._client.call_tool(name, arguments)
        future: Future[Any] = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        started = time.monotonic()
        while True:
            if cancel_event is not None and cancel_event.is_set():
                future.cancel()
                raise MCPConnectionError(f"MCP tool {self.spec.name}/{name} was cancelled.")
            remaining = self.spec.call_timeout_seconds - (time.monotonic() - started)
            if remaining <= 0:
                future.cancel()
                raise MCPConnectionError(
                    f"MCP tool {self.spec.name}/{name} exceeded "
                    f"{self.spec.call_timeout_seconds:.0f} seconds."
                )
            try:
                return future.result(timeout=min(0.2, remaining))
            except FutureTimeout:
                continue

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._close_requested.set()
        if self._loop is not None and self._thread is not None:
            if self._stop_event is not None:
                self._loop.call_soon_threadsafe(self._stop_event.set)
            if self._serve_task is not None:
                self._loop.call_soon_threadsafe(self._serve_task.cancel)
            self._thread.join(timeout=5.0)
            if self._thread.is_alive():
                self._loop.call_soon_threadsafe(self._loop.stop)
                self._thread.join(timeout=2.0)


class MCPToolGateway:
    """Connect only explicitly enabled servers and expose only exact approved tool names."""

    def __init__(
        self,
        server_configs: list[dict[str, Any]] | None = None,
        *,
        cancel_event: threading.Event | None = None,
        client_factory: Callable[[MCPServerSpec], Any] | None = None,
        result_limit_chars: int = 12_000,
    ) -> None:
        self.specs = [MCPServerSpec.from_dict(item) for item in (server_configs or [])]
        names = [spec.name for spec in self.specs]
        if len(set(names)) != len(names):
            raise MCPConfigurationError("MCP server names must be unique.")
        self.cancel_event = cancel_event
        self.client_factory = client_factory
        self.result_limit_chars = max(1_000, min(int(result_limit_chars), 64_000))
        self.connections: dict[str, MCPServerConnection] = {}
        self.routes: dict[str, tuple[MCPToolRoute, MCPServerConnection]] = {}
        self.skipped: dict[str, str] = {}

    def __enter__(self) -> MCPToolGateway:
        try:
            for spec in self.specs:
                if not spec.enabled:
                    self.skipped[spec.name] = "disabled"
                    continue
                if self.cancel_event is not None and self.cancel_event.is_set():
                    raise MCPConnectionError("MCP startup cancelled.")
                connection = MCPServerConnection(spec, client_factory=self.client_factory)
                connection.start()
                self.connections[spec.name] = connection
                approved = set(spec.approved_tools)
                for tool in connection.tools:
                    remote_name = str(getattr(tool, "name", ""))
                    if (
                        not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", remote_name)
                        or remote_name not in approved
                    ):
                        continue
                    input_schema = getattr(tool, "input_schema", None)
                    if not isinstance(input_schema, dict):
                        continue
                    if input_schema.get("type") != "object":
                        continue
                    try:
                        Draft202012Validator.check_schema(input_schema)
                    except SchemaError:
                        continue
                    exposed_name = f"mcp__{spec.name}__{remote_name}"
                    if exposed_name in self.routes:
                        raise MCPConfigurationError(f"Duplicate MCP exposed tool name: {exposed_name}")
                    route = MCPToolRoute(
                        exposed_name=exposed_name,
                        server_name=spec.name,
                        remote_name=remote_name,
                        description=str(getattr(tool, "description", "") or "")[:2_000],
                        input_schema=input_schema,
                    )
                    self.routes[exposed_name] = (route, connection)
        except Exception:
            self.close()
            raise
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def close(self) -> None:
        for connection in reversed(list(self.connections.values())):
            connection.close()
        self.connections.clear()
        self.routes.clear()

    def definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": route.exposed_name,
                    "description": (
                        f"MCP server '{route.server_name}' tool '{route.remote_name}'. "
                        f"{route.description}"
                    )[:3_000],
                    "parameters": route.input_schema,
                },
            }
            for route, _connection in self.routes.values()
        ]

    def invoke(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        route_entry = self.routes.get(name)
        if route_entry is None:
            raise ToolError(f"MCP tool is not granted or connected: {name}")
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise ToolError("MCP tool call cancelled before execution.")
        route, connection = route_entry
        if not isinstance(arguments, dict):
            raise ToolError("MCP arguments must be a JSON object.")
        try:
            Draft202012Validator(route.input_schema).validate(arguments)
        except ValidationError as exc:
            raise ToolError(f"Invalid arguments for MCP tool {route.remote_name}: {exc.message}") from exc
        try:
            result = connection.call_tool(route.remote_name, arguments, self.cancel_event)
        except MCPConnectionError as exc:
            raise ToolError(str(exc)) from exc
        content_items: list[dict[str, Any]] = []
        for block in getattr(result, "content", []) or []:
            block_type = str(getattr(block, "type", "unknown"))
            if block_type == "text":
                content_items.append({"type": "text", "text": str(getattr(block, "text", ""))})
            elif block_type == "image":
                content_items.append({
                    "type": "image",
                    "mime_type": str(getattr(block, "mimeType", "") or ""),
                    "note": "Image content is not forwarded through the text-only agent tool bridge.",
                })
            else:
                content_items.append({"type": block_type, "note": "Content block omitted by the host adapter."})
        payload = {
            "server": route.server_name,
            "tool": route.remote_name,
            "is_error": bool(getattr(result, "is_error", False)),
            "content": content_items,
            "structured_content": getattr(result, "structured_content", None),
        }
        safe = _redact_mcp_value(payload)
        encoded = json.dumps(safe, ensure_ascii=False, default=str)
        if len(encoded) > self.result_limit_chars:
            encoded = json.dumps({
                "truncated": True,
                "preview": encoded[:self.result_limit_chars],
                "original_characters": len(encoded),
            }, ensure_ascii=False)
        return json.loads(encoded)


def _redact_mcp_value(value: Any, key: str = "") -> Any:
    normalized_key = re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")
    if re.search(
        r"(?:^|_)(?:api_?key|api_?token|access_?key|access_?token|auth_?token|oauth_?token|session_?token|token|client_?secret|password|passwd|secret|credential|private_?key|authorization)(?:$|_)",
        normalized_key,
    ):
        return "[REDACTED]"
    if isinstance(value, str):
        return WorkspaceTools.redact_sensitive_text(value)[0][:8_000]
    if isinstance(value, dict):
        return {
            str(child_key)[:120]: _redact_mcp_value(child_value, str(child_key))
            for child_key, child_value in list(value.items())[:100]
        }
    if isinstance(value, (list, tuple)):
        return [_redact_mcp_value(item) for item in list(value)[:100]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if hasattr(value, "model_dump"):
        return _redact_mcp_value(value.model_dump(mode="json"))
    return str(value)[:2_000]


class CompositeToolRegistry:
    """Combine built-in workspace tools with explicitly granted MCP tools."""

    def __init__(self, workspace_tools: WorkspaceTools, mcp_gateway: MCPToolGateway) -> None:
        self.workspace_tools = workspace_tools
        self.mcp_gateway = mcp_gateway
        names = {
            entry["function"]["name"]
            for entry in workspace_tools.definitions() + mcp_gateway.definitions()
        }
        if len(names) != len(workspace_tools.definitions()) + len(mcp_gateway.definitions()):
            raise ToolError("Built-in and MCP tool names must be unique.")

    @property
    def workspace(self) -> Path:
        return self.workspace_tools.workspace

    def definitions(self) -> list[dict[str, Any]]:
        return self.workspace_tools.definitions() + self.mcp_gateway.definitions()

    def invoke(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name.startswith("mcp__"):
            return self.mcp_gateway.invoke(name, arguments)
        return self.workspace_tools.invoke(name, arguments)
