"""Runnable, offline-only MCP stdio entrypoint."""
from __future__ import annotations

import json
import sys

from .mcp_stdio import McpRouter, Tool, serve_stdio


def _health(arguments: dict) -> str:
    if arguments:
        raise ValueError("holding.health accepts no arguments")
    return json.dumps({
        "status": "READY_LOCAL",
        "network": False,
        "executes_plugins": False,
        "policy_gate": True,
    }, sort_keys=True, separators=(",", ":"))


def router() -> McpRouter:
    return McpRouter((Tool(
        "holding.health",
        "Report the local read-only MCP policy runtime status.",
        {"type": "object", "additionalProperties": False},
        _health,
    ),), server_name="atlas-holding-local", server_version="0.1.0")


def main() -> int:
    return serve_stdio(router(), sys.stdin.buffer, sys.stdout.buffer)


if __name__ == "__main__":
    raise SystemExit(main())
