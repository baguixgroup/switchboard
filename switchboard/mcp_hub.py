"""MCP client side: keeps connections to the MCP servers configured on this machine.

Each server lives in its own asyncio task that owns the connection (MCP transports must be
opened and closed in the same task); calls are passed to it through a queue.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

log = logging.getLogger("switchboard.mcp")


def result_to_payload(res: Any) -> dict:
    """Convert a CallToolResult into a JSON-safe dict (also used for results sent across nodes)."""
    texts: list[str] = []
    images: list[dict] = []
    for c in getattr(res, "content", None) or []:
        t = getattr(c, "type", None)
        if t == "text":
            texts.append(c.text)
        elif t == "image":
            images.append({"mime": getattr(c, "mime_type", None) or getattr(c, "mimeType", "image/png"), "data": c.data})
        elif t == "resource":
            r = getattr(c, "resource", None)
            texts.append(getattr(r, "text", None) or f"[resource {getattr(r, 'uri', '')}]")
        else:
            texts.append(f"[{t} content]")
    structured = getattr(res, "structured_content", None) or getattr(res, "structuredContent", None)
    if not texts and structured is not None:
        texts.append(json.dumps(structured, default=str))
    if images:
        texts.append(f"[{len(images)} image(s) returned and saved to the task output folder]")
    is_error = bool(getattr(res, "is_error", None) or getattr(res, "isError", False))
    return {"text": "\n".join(texts), "is_error": is_error, "images": images}


def _tool_dict(t: Any) -> dict:
    schema = getattr(t, "input_schema", None) or getattr(t, "inputSchema", None) or {}
    if hasattr(schema, "model_dump"):
        schema = schema.model_dump()
    return {"name": t.name, "description": getattr(t, "description", "") or "", "schema": schema}


class _Worker:
    def __init__(self, name: str, cfg: dict):
        self.name = name
        self.cfg = cfg
        self.queue: asyncio.Queue = asyncio.Queue()
        self.ready = asyncio.Event()
        self.tools: list[dict] = []
        self.error: str | None = None
        self.task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return bool(self.task and not self.task.done() and self.error is None)

    async def _serve(self, session) -> None:
        tools, cursor = [], None
        while True:
            res = await (session.list_tools(cursor=cursor) if cursor else session.list_tools())
            tools += list(res.tools)
            cursor = getattr(res, "next_cursor", None) or getattr(res, "nextCursor", None)
            if not cursor:
                break
        self.tools = [_tool_dict(t) for t in tools]
        self.ready.set()
        while True:
            item = await self.queue.get()
            if item is None:
                return
            tool, args, fut = item
            try:
                res = await session.call_tool(tool, args)
                if not fut.done():
                    fut.set_result(result_to_payload(res))
            except Exception as exc:
                if not fut.done():
                    fut.set_exception(exc)

    async def run(self) -> None:
        from mcp import Client, StdioServerParameters

        try:
            if self.cfg.get("url"):
                headers = {k: v for k, v in (self.cfg.get("headers") or {}).items() if v and not v.endswith(" ")}
                if headers:
                    import httpx2
                    from mcp import ClientSession
                    from mcp.client.streamable_http import streamable_http_client

                    async with httpx2.AsyncClient(headers=headers, timeout=60) as http:
                        async with streamable_http_client(self.cfg["url"], http_client=http) as streams:
                            async with ClientSession(streams[0], streams[1]) as session:
                                await session.initialize()
                                await self._serve(session)
                else:
                    async with Client(self.cfg["url"], read_timeout_seconds=300) as client:
                        await self._serve(client)
            else:
                params = StdioServerParameters(command=self.cfg["command"], args=[str(a) for a in self.cfg.get("args") or []],
                                               env=self.cfg.get("env") or None, cwd=self.cfg.get("cwd"))
                async with Client(params, read_timeout_seconds=300) as client:
                    await self._serve(client)
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            log.warning("MCP server %s failed: %s", self.name, self.error)
        finally:
            self.ready.set()
            while not self.queue.empty():
                item = self.queue.get_nowait()
                if item and not item[2].done():
                    item[2].set_exception(RuntimeError(f"MCP server {self.name} stopped: {self.error}"))


class MCPHub:
    def __init__(self, config):
        self.config = config
        self.workers: dict[str, _Worker] = {}

    def configured(self) -> dict[str, dict]:
        out = {}
        for name, cfg in (self.config.mcp_servers or {}).items():
            if not cfg or cfg.get("disabled") or not (cfg.get("command") or cfg.get("url")):
                continue
            out[name] = self.config.substitute(cfg)
        return out

    async def ensure(self, name: str, timeout: float = 90) -> _Worker:
        w = self.workers.get(name)
        if w is None or (w.task and w.task.done()):
            cfg = self.configured().get(name)
            if cfg is None:
                raise KeyError(f"MCP server '{name}' is not configured on this machine")
            w = _Worker(name, cfg)
            self.workers[name] = w
            w.task = asyncio.create_task(w.run(), name=f"mcp:{name}")
        try:
            await asyncio.wait_for(w.ready.wait(), timeout)
        except asyncio.TimeoutError:
            w.error = f"timed out after {timeout:.0f}s while starting"
        return w

    async def tools(self, name: str) -> list[dict]:
        w = await self.ensure(name)
        if w.error:
            raise RuntimeError(f"MCP server {name}: {w.error}")
        return w.tools

    async def all_tools(self, start: bool = True) -> dict[str, list[dict]]:
        names = list(self.configured())
        if start:
            await asyncio.gather(*(self.ensure(n) for n in names), return_exceptions=True)
        return {n: self.workers[n].tools for n in names if n in self.workers and not self.workers[n].error}

    async def call(self, server: str, tool: str, args: dict, timeout: float = 600) -> dict:
        w = await self.ensure(server)
        if w.error:
            raise RuntimeError(f"MCP server {server}: {w.error}")
        fut = asyncio.get_running_loop().create_future()
        await w.queue.put((tool, args, fut))
        return await asyncio.wait_for(fut, timeout)

    def status(self) -> dict[str, dict]:
        out = {}
        for name, cfg in self.configured().items():
            w = self.workers.get(name)
            out[name] = {
                "transport": "http" if cfg.get("url") else "stdio",
                "state": "error" if w and w.error else "running" if w and w.running else "idle",
                "error": w.error if w else None,
                "tools": len(w.tools) if w else 0,
            }
        return out

    async def close(self) -> None:
        for w in self.workers.values():
            if w.task and not w.task.done():
                await w.queue.put(None)
        tasks = [w.task for w in self.workers.values() if w.task]
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=5)
            for t in pending:
                t.cancel()
        self.workers.clear()
