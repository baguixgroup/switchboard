"""A provider-neutral set of tools offered to a model during one task."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

MAX_RESULT_CHARS = 50_000

ToolFn = Callable[[dict], Awaitable[Any]]


@dataclass
class ToolSpec:
    name: str
    description: str
    schema: dict
    origin: str


def safe_tool_name(raw: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", raw)[:64] or "tool"


def normalize_schema(schema: Any) -> dict:
    if not isinstance(schema, dict) or schema.get("type") != "object":
        return {"type": "object", "properties": {}}
    out = dict(schema)
    out.setdefault("properties", {})
    return out


class ToolBox:
    def __init__(self) -> None:
        self._tools: dict[str, tuple[ToolSpec, ToolFn]] = {}
        self.calls: list[dict] = []
        self.artifacts: list[tuple[str, bytes]] = []

    def __bool__(self) -> bool:
        return bool(self._tools)

    def __len__(self) -> int:
        return len(self._tools)

    def add(self, raw_name: str, description: str, schema: Any, fn: ToolFn, origin: str = "") -> str:
        name = safe_tool_name(raw_name)
        base, n = name, 2
        while name in self._tools:
            suffix = f"_{n}"
            name = base[: 64 - len(suffix)] + suffix
            n += 1
        self._tools[name] = (ToolSpec(name, (description or raw_name)[:1024], normalize_schema(schema), origin or raw_name), fn)
        return name

    def specs(self) -> list[ToolSpec]:
        return [spec for spec, _ in self._tools.values()]

    async def call(self, name: str, args: Any) -> tuple[str, bool]:
        if name not in self._tools:
            return f"Unknown tool: {name}", True
        spec, fn = self._tools[name]
        if not isinstance(args, dict):
            return "Tool arguments must be a JSON object.", True
        try:
            out = await fn(args)
            if isinstance(out, tuple):
                text, is_error = str(out[0]), bool(out[1])
            elif isinstance(out, (dict, list)):
                text, is_error = json.dumps(out, ensure_ascii=False, default=str), False
            else:
                text, is_error = str(out), False
        except Exception as exc:  # tool failures go back to the model, not up the stack
            text, is_error = f"Tool error: {type(exc).__name__}: {exc}", True
        if len(text) > MAX_RESULT_CHARS:
            text = text[:MAX_RESULT_CHARS] + f"\n...[truncated {len(text) - MAX_RESULT_CHARS} chars]"
        self.calls.append({"tool": spec.origin, "args": json.dumps(args, default=str)[:500], "error": is_error,
                           "result_chars": len(text)})
        return text, is_error
