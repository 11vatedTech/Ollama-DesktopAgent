"""Real MCP SDK v2 server used by transport-certification tests.

The process is intentionally ordinary: it is launched over stdio by the SDK or
served over Streamable HTTP by uvicorn. It has no test-only client shim, so the
gateway exercises discovery, JSON-RPC framing, invocation requirements and
process/session shutdown over the actual transports.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from mcp.server import MCPServer

server = MCPServer("mcp-certification", version="1.0", log_level="ERROR")
_state: dict[str, Any] = {"partial": 0, "slow": 0, "calls": Counter()}


@server.tool()
def echo(text: str) -> str:
    """Return the supplied text unchanged."""
    return text


@server.tool()
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


@server.tool()
async def slow(seconds: float) -> str:
    """Wait before returning, for timeout and cancellation coverage."""
    _state["slow"] += 1
    await asyncio.sleep(seconds)
    return "slow-complete"


@server.tool()
def slow_count() -> int:
    """Report how many slow invocations this server started."""
    return int(_state["slow"])


@server.tool()
def fail_after_side_effect() -> str:
    """Perform a partial side effect and then fail."""
    _state["partial"] += 1
    raise RuntimeError("intentional partial failure")


@server.tool()
def side_effect_count() -> int:
    """Report how many partial side effects this server observed."""
    return int(_state["partial"])


@server.tool()
def crash() -> str:
    """Terminate the server process abruptly without protocol shutdown."""
    os._exit(23)


@server.tool()
def big_result(chars: int = 40_000) -> str:
    """Return a large text result for host-side bounding tests."""
    return "x" * max(0, min(chars, 200_000))


@server.tool()
def call_count(label: str) -> int:
    """Increment and return a per-label counter."""
    _state["calls"][label] += 1
    return int(_state["calls"][label])


@server.tool()
def pid() -> int:
    """Return this server process identifier."""
    return os.getpid()


@server.tool()
def env_present(name: str) -> bool:
    """Report whether a child-process environment variable is present."""
    return name in os.environ


@server.tool()
def return_secret() -> str:
    """Return a credential-shaped value for redaction tests."""
    return "API_TOKEN=sk-live-certification-secret"


@server.tool()
def spawn_grandchild() -> int:
    """Spawn a long-lived child so process-tree cleanup can be verified."""
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return child.pid

if os.environ.get("MCP_CERT_VARIANT") == "drift":
    @server.tool()
    def variant_value(value: str) -> str:
        """Return the supplied variant value."""
        return value
else:
    @server.tool()
    def variant_value(value: int) -> int:
        """Return the supplied variant value plus one."""
        return value + 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", choices=("stdio", "http"), default="stdio")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--pid-file", type=Path, default=None)
    args = parser.parse_args()
    if args.pid_file is not None:
        args.pid_file.write_text(str(os.getpid()), encoding="utf-8")

    if args.transport == "stdio":
        server.run("stdio")
        return

    server.run(
        "streamable-http",
        host="127.0.0.1",
        port=args.port,
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=False,
    )


if __name__ == "__main__":
    main()
