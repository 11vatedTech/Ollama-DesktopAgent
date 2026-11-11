"""Versioned transport contract for the optional MCP client gateway.

This module describes the behavior the host relies on for each supported
transport. It is deliberately not a certification badge: the integration tests
in ``tests/test_mcp_integration.py`` produce the evidence for these checks on
the current SDK and host platform.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

MCP_CONTRACT_VERSION = "mcp-transport-contract/1"


@dataclass(frozen=True, slots=True)
class MCPTransportContract:
    transport: str
    server_process_owner: str
    reconnect_mode: str
    required_checks: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["required_checks"] = list(self.required_checks)
        return payload


_COMMON_CHECKS = (
    "initialize",
    "list_tools",
    "call_tool",
    "cancellation",
    "timeout",
    "crash_detection",
    "reconnect",
    "concurrent_isolation",
    "repeated_lifecycle",
)

TRANSPORT_CONTRACTS: dict[str, MCPTransportContract] = {
    "stdio": MCPTransportContract(
        transport="stdio",
        server_process_owner="client",
        reconnect_mode="respawn_child_process",
        required_checks=(*_COMMON_CHECKS, "process_tree_cleanup"),
    ),
    "streamable_http": MCPTransportContract(
        transport="streamable_http",
        server_process_owner="operator",
        reconnect_mode="new_client_session",
        required_checks=(*_COMMON_CHECKS, "session_cleanup"),
    ),
}


def transport_contract(transport: str) -> MCPTransportContract:
    try:
        return TRANSPORT_CONTRACTS[transport]
    except KeyError as exc:
        raise ValueError(f"Unsupported MCP transport contract: {transport}") from exc
