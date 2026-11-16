"""Switchboard as an MCP server, so Claude Desktop, Claude Code, VS Code, Cursor, Codex... can use it.

It is a thin client of the hub's HTTP API, so every app shares the same router, credits and history.
"""
from __future__ import annotations

import asyncio
import json
import time

import httpx


def build_server(config):
    from mcp.server.mcpserver import MCPServer

    server = MCPServer(
        name="switchboard",
        instructions=(
            "Switchboard routes tasks to the best AI model for the job across the user's providers and "
            "subscriptions, spends expiring credits first, can use tools on all the user's machines (Unity, "
            "Photoshop, Blender, phones, servers) and saves results to their output folder. Use plan_task to "
            "preview cost and model; use ask to run."
        ),
    )
    headers = {"Authorization": f"Bearer {config.token}"} if config.token else {}

    async def api(method: str, path: str, body: dict | None = None, timeout: float = 60) -> dict:
        async with httpx.AsyncClient(base_url=config.hub_url, headers=headers, timeout=timeout) as http:
            r = await http.request(method, path, json=body)
            if r.status_code not in (200, 409):
                raise RuntimeError(f"hub {r.status_code}: {r.text[:500]}")
            return r.json()

    @server.tool()
    async def ask(prompt: str, model: str = "", effort: str = "", mode: str = "", tools: str = "auto",
                  confirm_expensive: bool = False, wait_seconds: int = 900) -> str:
        """Run a task through Switchboard. Leave model/effort empty for automatic selection.
        model: 'provider:id' or id (e.g. 'claude-opus-5-5'); effort: low|medium|high|xhigh|max;
        mode: balanced|best|cheapest; tools: 'auto', 'none', or comma-separated MCP server names."""
        body = {"prompt": prompt, "model": model or None, "effort": effort or None, "mode": mode or None,
                "tools": "none" if tools == "none" else ([t.strip() for t in tools.split(",")] if tools != "auto" else "auto"),
                "confirm": confirm_expensive}
        res = await api("POST", "/api/tasks", body)
        if res.get("needs_confirmation"):
            p = res["plan"]
            return (f"Estimated ${p['effective_cost_usd']:.4f} with {p['model_label']} exceeds the confirm limit. "
                    f"Call ask again with confirm_expensive=true to run it.")
        task_id = res["task_id"]
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            t = await api("GET", f"/api/tasks/{task_id}")
            if t["status"] in ("done", "failed"):
                head = f"[{(t.get('plan') or {}).get('executed', '')} · ${t.get('cost_usd') or 0:.4f} · saved to {t.get('output_path')}]"
                return f"{head}\n\n{t.get('result') if t['status'] == 'done' else 'FAILED: ' + str(t.get('error'))}"
            await asyncio.sleep(2)
        return f"Still running (task {task_id}). Check the Switchboard dashboard."

    @server.tool()
    async def plan_task(prompt: str, model: str = "", effort: str = "", mode: str = "") -> str:
        """Preview which model, effort, credit source and tools Switchboard would use, with estimated cost."""
        p = await api("POST", "/api/plan", {"prompt": prompt, "model": model or None, "effort": effort or None,
                                             "mode": mode or None})
        return json.dumps({k: p[k] for k in ("provider", "model_label", "effort", "est_cost_usd", "effective_cost_usd",
                                             "source_label", "reasons", "warnings", "tools", "alternatives",
                                             "suggestions")}, indent=2)

    @server.tool()
    async def credits_and_alerts() -> str:
        """Credit balances, free-tier quotas, subscriptions, and alerts about credit that is about to be lost."""
        c = await api("GET", "/api/credits")
        return json.dumps({"alerts": c["alerts"], "sources": c["sources"]}, indent=2)

    @server.tool()
    async def machines_and_devices() -> str:
        """Connected machines, their phones/consoles/servers, installed tools and MCP servers."""
        nodes = await api("GET", "/api/nodes")
        return json.dumps([{k: n.get(k) for k in ("node", "platform", "online", "devices", "mcp", "output_dir")}
                           for n in nodes], indent=2, default=str)

    return server


async def serve_stdio(config) -> None:
    await build_server(config).run_stdio_async()
