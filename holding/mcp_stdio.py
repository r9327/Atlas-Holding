"""Bounded MCP 2026-07-28 JSON-RPC server for the local stdio hub.

Only trusted, read-only in-process handlers belong here. Network/plugin
execution remains behind ``PluginGate`` and a separately bounded executor.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import BinaryIO, Callable


PROTOCOL_VERSION = "2026-07-28"
MAX_MESSAGE_BYTES = 1024 * 1024
MAX_RESULT_BYTES = 2 * 1024 * 1024
MAX_TOOL_TEXT_BYTES = 256 * 1024
_TOOL_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


class McpProtocolError(ValueError):
    pass


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise McpProtocolError("Duplicate JSON object key")
        result[key] = value
    return result


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Callable[[dict], str]

    def __post_init__(self) -> None:
        if (type(self.name) is not str or not _TOOL_NAME.fullmatch(self.name)
                or type(self.description) is not str
                or not self.description or len(self.description) > 1000
                or type(self.input_schema) is not dict
                or self.input_schema.get("type") != "object"
                or not callable(self.handler)):
            raise ValueError("Invalid bounded MCP tool definition")
        # Copying prevents callers from mutating the advertised schema later.
        try:
            encoded = json.dumps(
                self.input_schema, ensure_ascii=True, allow_nan=False,
                separators=(",", ":"), sort_keys=True,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("Tool schema must be finite JSON") from exc
        if len(encoded) > 64 * 1024:
            raise ValueError("Tool schema exceeds size limit")
        object.__setattr__(self, "input_schema", json.loads(encoded))


class McpRouter:
    def __init__(self, tools: tuple[Tool, ...], *, server_name: str,
                 server_version: str) -> None:
        if (type(tools) is not tuple or len(tools) > 256
                or any(type(tool) is not Tool for tool in tools)
                or len({tool.name for tool in tools}) != len(tools)
                or type(server_name) is not str or not _TOOL_NAME.fullmatch(server_name)
                or type(server_version) is not str
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}", server_version)):
            raise ValueError("Invalid MCP server identity or tool registry")
        # Clone definitions so later mutation through caller-owned Tool objects
        # cannot change the advertised registry.
        cloned = tuple(Tool(
            tool.name, tool.description, tool.input_schema, tool.handler,
        ) for tool in tools)
        definitions_size = sum(len(json.dumps({
            "name": tool.name, "description": tool.description,
            "inputSchema": tool.input_schema,
        }, ensure_ascii=True, separators=(",", ":"))) for tool in cloned)
        if definitions_size > 512 * 1024:
            raise ValueError("MCP tool registry exceeds size limit")
        self._tools = {tool.name: tool for tool in cloned}
        self._server_info = {"name": server_name, "version": server_version}

    def _meta(self) -> dict:
        return {"io.modelcontextprotocol/serverInfo": self._server_info}

    @staticmethod
    def _error(request_id, code: int, message: str, data=None) -> dict:
        error = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        return {"jsonrpc": "2.0", "id": request_id, "error": error}

    def _success(self, request_id, result: dict) -> dict:
        result.setdefault("_meta", self._meta())
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _request_meta(self, params: dict) -> tuple[dict | None, dict | None]:
        meta = params.get("_meta")
        if type(meta) is not dict:
            return None, self._error(None, -32602, "Missing MCP request metadata")
        version = meta.get("io.modelcontextprotocol/protocolVersion")
        capabilities = meta.get("io.modelcontextprotocol/clientCapabilities")
        if type(version) is not str or type(capabilities) is not dict:
            return None, self._error(None, -32602, "Invalid MCP request metadata")
        client = meta.get("io.modelcontextprotocol/clientInfo")
        if client is not None and (type(client) is not dict
                or type(client.get("name")) is not str
                or type(client.get("version")) is not str):
            return None, self._error(None, -32602, "Invalid MCP client identity")
        return meta, None

    def handle_line(self, raw: bytes) -> bytes | None:
        """Handle one newline-delimited request and return one compact response."""
        if type(raw) is not bytes or len(raw) > MAX_MESSAGE_BYTES + 1:
            return self._encode(self._error(None, -32700, "Message exceeds size limit"))
        if raw.endswith(b"\n"):
            raw = raw[:-1]
        if raw.endswith(b"\r"):
            raw = raw[:-1]
        if not raw or b"\n" in raw or b"\r" in raw:
            return self._encode(self._error(None, -32700, "Invalid stdio framing"))
        try:
            def reject_nonfinite(_value: str):
                raise McpProtocolError("Non-finite JSON value")
            request = json.loads(
                raw.decode("utf-8"), object_pairs_hook=_unique_object,
                parse_constant=reject_nonfinite,
            )
        except (UnicodeError, ValueError, McpProtocolError, RecursionError):
            # Deeply nested valid-looking input cannot crash the stdio hub;
            # NaN/Infinity are not standard JSON and may bypass tool guards.
            return self._encode(self._error(None, -32700, "Parse error"))
        if type(request) is not dict:
            return self._encode(self._error(None, -32600, "Invalid Request"))
        allowed = {"jsonrpc", "id", "method", "params"}
        if (not set(request) <= allowed or request.get("jsonrpc") != "2.0"
                or type(request.get("method")) is not str
                or type(request.get("params", {})) is not dict):
            return self._encode(self._error(request.get("id"), -32600, "Invalid Request"))
        notification = "id" not in request
        request_id = request.get("id")
        if not notification and (type(request_id) not in (str, int)
                or isinstance(request_id, bool)
                or isinstance(request_id, str) and len(request_id) > 128):
            return self._encode(self._error(None, -32600, "Invalid request id"))
        method = request["method"]
        params = request.get("params", {})
        if notification:
            # Modern stdio notifications never receive a response. Cancellation
            # is accepted; this synchronous read-only core has no in-flight work.
            return None
        _meta, meta_error = self._request_meta(params)
        if meta_error is not None:
            meta_error["id"] = request_id
            return self._encode(meta_error)
        version = _meta["io.modelcontextprotocol/protocolVersion"]
        if version != PROTOCOL_VERSION:
            return self._encode(self._error(
                request_id, -32022, "Unsupported protocol version",
                {"supported": [PROTOCOL_VERSION]},
            ))
        if method == "server/discover":
            if set(params) != {"_meta"}:
                return self._encode(self._error(
                    request_id, -32602, "Invalid discovery request",
                ))
            result = {
                "resultType": "complete",
                "supportedVersions": [PROTOCOL_VERSION],
                "capabilities": {"tools": {}},
                "instructions": "Read-only local Holding tools; policy-gated adapters only.",
                "ttlMs": 300_000,
                "cacheScope": "public",
            }
            return self._encode(self._success(request_id, result))
        if method == "tools/list":
            if set(params) - {"_meta", "cursor"} or params.get("cursor") not in (None, ""):
                return self._encode(self._error(request_id, -32602, "Unsupported cursor"))
            tools = [{
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.input_schema,
            } for tool in sorted(self._tools.values(), key=lambda item: item.name)]
            return self._encode(self._success(request_id, {
                "resultType": "complete", "tools": tools,
                "ttlMs": 300_000, "cacheScope": "public",
            }))
        if method == "tools/call":
            if set(params) != {"_meta", "name", "arguments"}:
                return self._encode(self._error(request_id, -32602, "Invalid tool call"))
            name, arguments = params.get("name"), params.get("arguments")
            if type(name) is not str or type(arguments) is not dict:
                return self._encode(self._error(request_id, -32602, "Invalid tool arguments"))
            tool = self._tools.get(name)
            if tool is None:
                return self._encode(self._error(request_id, -32602, "Unknown tool"))
            try:
                text = tool.handler(arguments)
                if type(text) is not str:
                    raise TypeError("Tool result must be text")
                if len(text.encode("utf-8")) > MAX_TOOL_TEXT_BYTES:
                    raise ValueError("Tool result exceeds size limit")
                result = {"resultType": "complete", "content": [
                    {"type": "text", "text": text},
                ], "isError": False}
            except Exception:
                result = {"resultType": "complete", "content": [
                    {"type": "text", "text": "Trusted local tool failed"},
                ], "isError": True}
            return self._encode(self._success(request_id, result))
        return self._encode(self._error(request_id, -32601, "Method not found"))

    @staticmethod
    def _encode(response: dict) -> bytes:
        encoded = json.dumps(
            response, ensure_ascii=True, separators=(",", ":"), sort_keys=True,
        ).encode("ascii") + b"\n"
        if len(encoded) <= MAX_RESULT_BYTES:
            return encoded
        fallback = {
            "jsonrpc": "2.0", "id": response.get("id"),
            "error": {"code": -32603, "message": "Response exceeds size limit"},
        }
        return json.dumps(
            fallback, ensure_ascii=True, separators=(",", ":"), sort_keys=True,
        ).encode("ascii") + b"\n"


def serve_stdio(router: McpRouter, stdin: BinaryIO, stdout: BinaryIO) -> int:
    """Serve until EOF. Stdout contains MCP frames only."""
    while True:
        raw = stdin.readline(MAX_MESSAGE_BYTES + 2)
        if raw == b"":
            return 0
        if len(raw) > MAX_MESSAGE_BYTES + 1 and not raw.endswith(b"\n"):
            while raw and not raw.endswith(b"\n"):
                raw = stdin.readline(MAX_MESSAGE_BYTES + 2)
            response = router.handle_line(b"x" * (MAX_MESSAGE_BYTES + 2))
        else:
            response = router.handle_line(raw)
        if response is not None:
            stdout.write(response)
            stdout.flush()
