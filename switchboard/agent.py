"""Agent: runs on each Windows/Mac/Pi machine, reports inventory and executes jobs for the hub.

Connections are outbound only (agent -> hub), so agents need no open ports or firewall rules.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

import httpx

from .executor import Executor
from .inventory import build_inventory
from .mcp_hub import MCPHub

log = logging.getLogger("switchboard.agent")
INVENTORY_EVERY = 300
HEARTBEAT_EVERY = 30


async def run_agent(config) -> None:
    if not config.token:
        raise SystemExit("SWITCHBOARD_TOKEN is not set in secrets.env (copy it from the hub).")
    mcp = MCPHub(config)
    executor = Executor(config, mcp)
    state = {"inv": await build_inventory(config, mcp), "inv_ts": time.time()}
    headers = {"Authorization": f"Bearer {config.token}", "Content-Type": "application/json"}
    log.info("agent %s -> hub %s", config.node_name, config.hub_url)

    async with httpx.AsyncClient(base_url=config.hub_url, headers=headers,
                                 timeout=httpx.Timeout(75, connect=10)) as http:

        async def post(path: str, body: dict) -> httpx.Response:
            return await http.post(path, content=json.dumps(body, default=str))

        async def heartbeat() -> None:
            try:
                if time.time() - state["inv_ts"] > INVENTORY_EVERY:
                    state["inv"] = await build_inventory(config, mcp)
                    state["inv_ts"] = time.time()
                r = await post("/api/agent/heartbeat", {"inventory": state["inv"]})
                if r.status_code in (401, 409):
                    log.error("hub refused heartbeat: %s", r.text)
            except httpx.HTTPError as exc:
                log.warning("hub unreachable: %s", exc)

        async def heartbeat_loop() -> None:
            while True:
                await heartbeat()
                await asyncio.sleep(HEARTBEAT_EVERY)

        async def handle(job: dict) -> None:
            try:
                result = await executor.execute(job["kind"], job.get("payload") or {})
                body = {"result": result}
                if job["kind"] == "inventory":
                    state["inv"], state["inv_ts"] = result, time.time()
            except Exception as exc:
                log.warning("job %s (%s) failed: %s", job.get("id"), job.get("kind"), exc)
                body = {"error": f"{type(exc).__name__}: {exc}"}
            for attempt in range(3):
                try:
                    await post(f"/api/agent/jobs/{job['id']}", body)
                    return
                except httpx.HTTPError:
                    await asyncio.sleep(2 ** attempt)

        hb = asyncio.create_task(heartbeat_loop())
        jobs: set[asyncio.Task] = set()
        backoff = 1.0
        try:
            while True:
                try:
                    r = await http.get("/api/agent/jobs", params={"node": config.node_name, "wait": 25})
                    if r.status_code == 200:
                        job = r.json()
                        await http.post(f"/api/agent/jobs/{job['id']}/ack")
                        t = asyncio.create_task(handle(job))
                        jobs.add(t)
                        t.add_done_callback(jobs.discard)
                        backoff = 1.0
                        continue
                    if r.status_code == 204:
                        backoff = 1.0
                        continue
                    if r.status_code == 428:  # hub restarted: register again right away
                        await heartbeat()
                        continue
                    log.warning("hub returned %s: %s", r.status_code, r.text[:200])
                except httpx.HTTPError as exc:
                    log.debug("poll failed: %s", exc)
                await asyncio.sleep(backoff)
                backoff = min(60.0, backoff * 2)
        finally:
            hb.cancel()
            for t in jobs:
                t.cancel()
            await mcp.close()
