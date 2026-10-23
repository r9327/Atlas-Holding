"""Bounded, executable subset of JSON Schema for trusted local MCP tools.

Only explicitly supported validation keywords can be advertised. This prevents
a tool definition from claiming enforcement of rules the router ignores.
No dynamic schema references, executable code, remote refs or schema fetches.
"""
from __future__ import annotations

import math
import re


_TYPES = frozenset({"object", "array", "string", "integer", "number", "boolean", "null"})
_FIELDS = frozenset({
    "type", "properties", "required", "additionalProperties", "items",
    "minLength", "maxLength", "minItems", "maxItems", "enum",
})
_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
MAX_SCHEMA_DEPTH = 8


def validate_schema(schema: object, depth: int = 0) -> None:
    """Reject unsupported claims before advertising the tool to a client."""
    if (type(schema) is not dict or depth > MAX_SCHEMA_DEPTH
            or not set(schema) <= _FIELDS
            or type(schema.get("type")) is not str
            or schema["type"] not in _TYPES):
        raise ValueError("Unsupported bounded MCP input schema")
    kind = schema["type"]
    if "enum" in schema:
        values = schema["enum"]
        if type(values) is not list or not 1 <= len(values) <= 64:
            raise ValueError("Invalid MCP schema enum")
        if any(type(value) not in (str, int, float, bool, type(None))
               or type(value) is float and not math.isfinite(value)
               for value in values):
            raise ValueError("Invalid MCP schema enum values")
    if kind == "object":
        if set(schema) - {"type", "properties", "required", "additionalProperties", "enum"}:
            raise ValueError("Unsupported MCP object schema keyword")
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if (type(properties) is not dict or len(properties) > 64
                or type(required) is not list or len(required) > 64
                or any(type(name) is not str for name in required)
                or len(set(required)) != len(required)
                or any(type(name) is not str or not _NAME.fullmatch(name)
                       for name in properties)
                or any(name not in properties for name in required)
                or type(schema.get("additionalProperties", True)) is not bool):
            raise ValueError("Invalid MCP object schema")
        for sub in properties.values():
            validate_schema(sub, depth + 1)
    elif kind == "array":
        if set(schema) - {"type", "items", "minItems", "maxItems", "enum"}:
            raise ValueError("Unsupported MCP array schema keyword")
        if "items" not in schema:
            raise ValueError("MCP arrays must specify item schema")
        validate_schema(schema["items"], depth + 1)
        lo, hi = schema.get("minItems", 0), schema.get("maxItems", 1000)
        if type(lo) is not int or type(hi) is not int or not 0 <= lo <= hi <= 1000:
            raise ValueError("Invalid MCP array bounds")
    elif kind == "string":
        if set(schema) - {"type", "minLength", "maxLength", "enum"}:
            raise ValueError("Unsupported MCP string schema keyword")
        lo, hi = schema.get("minLength", 0), schema.get("maxLength", 65536)
        if type(lo) is not int or type(hi) is not int or not 0 <= lo <= hi <= 65536:
            raise ValueError("Invalid MCP string bounds")
    elif set(schema) - {"type", "enum"}:
        raise ValueError("Unsupported MCP primitive schema keyword")


def matches_schema(value: object, schema: dict, depth: int = 0) -> bool:
    """Pure bounded argument validation; the handler is not run on a miss."""
    if depth > MAX_SCHEMA_DEPTH:
        return False
    kind = schema["type"]
    types = {
        "object": dict, "array": list, "string": str,
        "integer": int, "number": (int, float), "boolean": bool,
        "null": type(None),
    }
    required = types[kind]
    if kind == "number":
        if type(value) not in (int, float) or (
                type(value) is float and not math.isfinite(value)):
            return False
    elif type(value) is not required:
        return False
    if "enum" in schema and not any(
            type(value) is type(candidate) and value == candidate
            for candidate in schema["enum"]):
        return False
    if kind == "object":
        properties = schema.get("properties", {})
        if (not set(schema.get("required", [])).issubset(value)
                or not schema.get("additionalProperties", True)
                and not set(value).issubset(properties)):
            return False
        return all(
            matches_schema(item, properties[key], depth + 1)
            for key, item in value.items() if key in properties
        )
    if kind == "array":
        return (schema.get("minItems", 0) <= len(value) <=
                schema.get("maxItems", 1000)
                and all(matches_schema(item, schema["items"], depth + 1)
                        for item in value))
    if kind == "string":
        return schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 65536)
    return True
