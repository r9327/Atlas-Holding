import io
import json
import unittest

from holding.mcp_entry import router
from holding.mcp_stdio import (
    MAX_MESSAGE_BYTES,
    McpRouter,
    PROTOCOL_VERSION,
    Tool,
    serve_stdio,
)


def request(method, params=None, request_id=1):
    body = {
        "jsonrpc": "2.0", "id": request_id, "method": method,
        "params": params or {},
    }
    body["params"]["_meta"] = {
        "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "1"},
    }
    return json.dumps(body, separators=(",", ":")).encode() + b"\n"


def response(server, raw):
    value = server.handle_line(raw)
    return None if value is None else json.loads(value)


class McpStdioTests(unittest.TestCase):
    def setUp(self):
        self.server = router()

    def test_modern_discovery_advertises_exact_version_and_tools(self):
        result = response(self.server, request("server/discover", request_id="d"))
        self.assertEqual(result["id"], "d")
        body = result["result"]
        self.assertEqual(body["supportedVersions"], ["2026-07-28"])
        self.assertEqual(body["capabilities"], {"tools": {}})
        self.assertEqual(
            body["_meta"]["io.modelcontextprotocol/serverInfo"]["name"],
            "atlas-holding-local",
        )

    def test_unsupported_protocol_reports_supported_versions(self):
        raw = json.loads(request("server/discover"))
        raw["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"] = "2099-01-01"
        result = response(self.server, json.dumps(raw).encode() + b"\n")
        self.assertEqual(result["error"]["code"], -32022)
        self.assertEqual(result["error"]["data"]["supported"], [PROTOCOL_VERSION])

    def test_tools_are_deterministic_and_health_is_read_only(self):
        listed = response(self.server, request("tools/list"))["result"]["tools"]
        self.assertEqual([item["name"] for item in listed], ["holding.health"])
        called = response(self.server, request("tools/call", {
            "name": "holding.health", "arguments": {},
        }, 2))["result"]
        self.assertFalse(called["isError"])
        payload = json.loads(called["content"][0]["text"])
        self.assertEqual(payload["status"], "READY_LOCAL")
        self.assertFalse(payload["network"])
        self.assertFalse(payload["executes_plugins"])

    def test_router_copies_caller_owned_tool_schema(self):
        schema = {"type": "object", "properties": {}}
        tool = Tool("safe", "safe tool", schema, lambda _arguments: "ok")
        server = McpRouter((tool,), server_name="bounded", server_version="1")
        tool.input_schema["properties"]["injected"] = {"type": "string"}
        listed = response(server, request("tools/list"))["result"]["tools"]
        self.assertEqual(listed[0]["inputSchema"]["properties"], {})

    def test_tool_arguments_are_validated_before_any_handler_effect(self):
        calls = []
        def handler(args):
            calls.append(dict(args))
            return "safe"
        server = McpRouter((Tool(
            "read.only", "Safely checks bounded inputs",
            {
                "type": "object", "additionalProperties": False,
                "required": ["query"], "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 16},
                    "limit": {"type": "integer", "enum": [1, 2, 3]},
                    "tags": {"type": "array", "maxItems": 2,
                             "items": {"type": "string", "maxLength": 8}},
                },
            }, handler,
        ),), server_name="bounded", server_version="1")
        invalid = [
            {}, {"query": ""}, {"query": "x" * 17},
            {"query": 55}, {"query": "ok", "limit": True},
            {"query": "ok", "limit": 4},
            {"query": "ok", "tags": ["a", "b", "c"]},
            {"query": "ok", "unexpected": "dangerous"},
            {"query": "ok", "tags": ["a", 2]},
        ]
        for arguments in invalid:
            with self.subTest(arguments=arguments):
                error = response(server, request("tools/call", {
                    "name": "read.only", "arguments": arguments,
                }))["error"]
                self.assertEqual(error["code"], -32602)
                self.assertEqual(calls, [])
        valid = response(server, request("tools/call", {
            "name": "read.only", "arguments": {
                "query": "dofus", "limit": 2, "tags": ["pvm"],
            },
        }))["result"]
        self.assertFalse(valid["isError"])
        self.assertEqual(calls, [{"query": "dofus", "limit": 2, "tags": ["pvm"]}])

    def test_tool_schema_rejects_unimplemented_or_unsafe_claims(self):
        bad = [
            {"type": "object", "$ref": "https://host/schema"},
            {"type": "object", "properties": {"x": {"type": "number"}},
             "required": [["x"]]},
            {"type": "object", "properties": {"x": {"type": "string"}},
             "required": ["unknown"]},
            {"type": "object", "properties": {"x": {"type": "string"}},
             "required": ["x", "x"]},
            {"type": "array"},
            {"type": "array", "items": {"type": "object"}, "maxItems": 5000},
            {"type": "object", "properties": {
                "injection": {"type": "string", "pattern": ".*"}
            }},
        ]
        for schema in bad:
            with self.subTest(schema=schema), self.assertRaises(ValueError):
                Tool("invalid", "Invalid", schema, lambda _args: "never")

    def test_non_finite_or_oversized_schema_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "finite JSON"):
            Tool("bad", "bad schema", {"type": "object", "x": float("nan")}, str)
        with self.assertRaisesRegex(ValueError, "size limit"):
            Tool("bad", "bad schema", {"type": "object", "x": "z" * 70000}, str)

    def test_tool_failure_is_a_tool_result_without_exception_details(self):
        def failing(_arguments):
            raise RuntimeError("hidden private agent credential")
        server = McpRouter((Tool(
            "test.failure", "Safe failing tool",
            {"type": "object", "additionalProperties": False}, failing,
        ),), server_name="bounded", server_version="1")
        failed = response(server, request("tools/call", {
            "name": "test.failure", "arguments": {},
        }))["result"]
        self.assertTrue(failed["isError"])
        self.assertEqual(failed["content"][0]["text"], "Trusted local tool failed")
        self.assertNotIn("credential", json.dumps(failed))

    def test_unknown_tool_and_method_are_protocol_errors(self):
        missing = response(self.server, request("tools/call", {
            "name": "missing", "arguments": {},
        }))
        self.assertEqual(missing["error"]["code"], -32602)
        method = response(self.server, request("unknown/method"))
        self.assertEqual(method["error"]["code"], -32601)

    def test_missing_metadata_duplicate_keys_and_embedded_newline_fail_closed(self):
        missing = response(self.server, b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n')
        self.assertEqual(missing["error"]["code"], -32602)
        duplicate = response(
            self.server,
            b'{"jsonrpc":"2.0","id":1,"id":2,"method":"tools/list"}\n',
        )
        self.assertEqual(duplicate["error"]["code"], -32700)
        framed = response(self.server, b'{}\n{}\n')
        self.assertEqual(framed["error"]["code"], -32700)

    def test_nonfinite_json_and_nested_payloads_fail_without_killing_server(self):
        nonfinite = request("tools/call", {
            "name": "holding.health", "arguments": {},
        }).replace(b'"arguments":{}', b'"arguments":{"x":NaN}')
        self.assertEqual(
            response(self.server, nonfinite)["error"]["code"], -32700)
        deeply_nested = b"[" * 2500 + b"0" + b"]" * 2500 + b"\n"
        # Python JSON decoder depth limits vary by runtime. Whether decoding
        # fails (-32700) or succeeds with a non-object (-32600), reject safely.
        self.assertIn(
            response(self.server, deeply_nested)["error"]["code"],
            (-32700, -32600))
        self.assertEqual(
            response(self.server, request("tools/list"))["result"]["tools"][0]["name"],
            "holding.health",
        )

    def test_notifications_never_receive_a_response(self):
        note = json.loads(request("notifications/cancelled"))
        del note["id"]
        self.assertIsNone(self.server.handle_line(json.dumps(note).encode() + b"\n"))

    def test_result_size_and_handler_exceptions_are_bounded(self):
        large = McpRouter((Tool(
            "large", "large result", {"type": "object"},
            lambda _arguments: "x" * (2 * 1024 * 1024 + 1),
        ),), server_name="bounded", server_version="1")
        result = response(large, request("tools/call", {
            "name": "large", "arguments": {},
        }))["result"]
        self.assertTrue(result["isError"])
        self.assertNotIn("size", result["content"][0]["text"].lower())

    def test_stdio_serves_one_json_message_per_line_and_exits_on_eof(self):
        stdin = io.BytesIO(request("server/discover") + request("tools/list", request_id=2))
        stdout = io.BytesIO()
        self.assertEqual(serve_stdio(self.server, stdin, stdout), 0)
        lines = stdout.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual([json.loads(line)["id"] for line in lines], [1, 2])

    def test_oversized_frame_is_rejected_and_next_frame_survives(self):
        stdin = io.BytesIO(b"x" * (MAX_MESSAGE_BYTES + 10) + b"\n" + request("tools/list"))
        stdout = io.BytesIO()
        serve_stdio(self.server, stdin, stdout)
        messages = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual(messages[0]["error"]["code"], -32700)
        self.assertEqual(messages[1]["id"], 1)


if __name__ == "__main__":
    unittest.main()
