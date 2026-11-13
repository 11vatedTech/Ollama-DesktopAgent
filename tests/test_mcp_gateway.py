from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from ollama_workstation.agents.mcp_gateway import (
    MCPConfigurationError,
    MCPServerSpec,
    MCPToolGateway,
)
from ollama_workstation.agents.tools import ToolError


@dataclass
class FakeTool:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class FakePage:
    tools: list[FakeTool]
    next_cursor: str | None = None


@dataclass
class FakeResult:
    content: list[Any]
    structured_content: dict[str, Any]
    is_error: bool = False


class FakeMCPClient:
    def __init__(self, spec: MCPServerSpec) -> None:
        self.spec = spec
        self.closed = False
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.tools = [
            FakeTool(
                "read_note",
                "Read a note by its identifier.",
                {
                    "type": "object",
                    "properties": {"note_id": {"type": "string"}},
                    "required": ["note_id"],
                    "additionalProperties": False,
                },
            ),
            FakeTool(
                "delete_all",
                "Destructive tool that is intentionally not granted.",
                {"type": "object", "properties": {}, "additionalProperties": False},
            ),
        ]

    async def __aenter__(self) -> FakeMCPClient:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.closed = True

    async def list_tools(self, cursor: str | None = None) -> FakePage:
        return FakePage(self.tools)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> FakeResult:
        self.calls.append((name, arguments))
        return FakeResult(
            content=[SimpleNamespace(type="text", text="Loaded API_TOKEN=secret-mcp-value")],
            structured_content={"api_token": "structured-secret", "note": "safe"},
        )


def test_mcp_server_spec_requires_explicit_exact_grants() -> None:
    spec = MCPServerSpec.from_dict({
        "name": "notes",
        "transport": "stdio",
        "command": "python",
        "args": ["-m", "notes_server"],
        "approved_tools": ["read_note"],
    })
    assert spec.enabled is False
    assert spec.approved_tools == ("read_note",)
    with pytest.raises(MCPConfigurationError, match="Wildcard"):
        MCPServerSpec.from_dict({
            "name": "notes", "transport": "stdio", "command": "python",
            "approved_tools": ["*"],
        })


def test_mcp_http_transport_requires_tls_for_remote_servers() -> None:
    with pytest.raises(MCPConfigurationError, match="Unencrypted HTTP"):
        MCPServerSpec.from_dict({
            "name": "remote", "transport": "streamable_http",
            "enabled": True, "url": "http://example.com/mcp",
        })
    spec = MCPServerSpec.from_dict({
        "name": "local", "transport": "streamable_http",
        "enabled": True, "url": "http://127.0.0.1:8765/mcp",
    })
    assert spec.url.startswith("http://127.0.0.1")


def test_mcp_gateway_exposes_only_approved_and_schema_validated_tools() -> None:
    clients: list[FakeMCPClient] = []

    def factory(spec: MCPServerSpec) -> FakeMCPClient:
        client = FakeMCPClient(spec)
        clients.append(client)
        return client

    config = [{
        "name": "notes", "transport": "stdio", "enabled": True,
        "command": "python", "approved_tools": ["read_note"],
    }]
    with MCPToolGateway(config, client_factory=factory) as gateway:
        definitions = gateway.definitions()
        assert [x["function"]["name"] for x in definitions] == ["mcp__notes__read_note"]
        with pytest.raises(ToolError, match="Invalid arguments"):
            gateway.invoke("mcp__notes__read_note", {})
        with pytest.raises(ToolError, match="Invalid arguments"):
            gateway.invoke("mcp__notes__read_note", {"note_id": "1", "extra": True})
        with pytest.raises(ToolError, match="not granted"):
            gateway.invoke("mcp__notes__delete_all", {})
        result = gateway.invoke("mcp__notes__read_note", {"note_id": "42"})
        assert result["is_error"] is False
        assert "secret-mcp-value" not in str(result)
        assert "structured-secret" not in str(result)
        assert result["structured_content"]["api_token"] == "[REDACTED]"
        assert clients[0].calls == [("read_note", {"note_id": "42"})]
    assert clients[0].closed is True


def test_mcp_tool_call_honors_cancellation_event() -> None:
    class SlowClient(FakeMCPClient):
        async def call_tool(self, name: str, arguments: dict[str, Any]) -> FakeResult:
            await __import__("asyncio").sleep(5)
            return FakeResult([], {})

    cancel = threading.Event()
    gateway = MCPToolGateway(
        [{
            "name": "slow", "transport": "stdio", "enabled": True,
            "command": "python", "approved_tools": ["read_note"],
            "call_timeout_seconds": 10,
        }],
        cancel_event=cancel,
        client_factory=lambda spec: SlowClient(spec),
    )
    gateway.__enter__()
    errors: list[BaseException] = []

    def invoke() -> None:
        try:
            gateway.invoke("mcp__slow__read_note", {"note_id": "42"})
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=invoke)
    worker.start()
    time.sleep(0.2)
    cancel.set()
    worker.join(timeout=2)
    gateway.close()
    assert not worker.is_alive()
    assert errors
    assert "cancelled" in str(errors[0]).lower()
