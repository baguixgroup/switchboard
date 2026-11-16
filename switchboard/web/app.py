"""Hub HTTP API + dashboard."""
from __future__ import annotations

import asyncio
import base64
import hmac
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager
from importlib import resources
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from .. import advisor
from ..credits import CreditSource
from ..inventory import mcp_catalog
from ..router import Plan

log = logging.getLogger("switchboard.web")
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


class PlanIn(BaseModel):
    prompt: str
    provider: str | None = None
    model: str | None = None
    effort: str | None = None
    mode: str | None = None
    tools: Any = "auto"
    images: list[str] = Field(default_factory=list)  # data URLs


class TaskIn(PlanIn):
    plan: dict | None = None
    confirm: bool = False
    history: list[dict] = Field(default_factory=list)


class HeartbeatIn(BaseModel):
    inventory: dict


class JobResultIn(BaseModel):
    result: Any = None
    error: str | None = None


class DeviceActionIn(BaseModel):
    node: str
    device: str
    action: str
    args: dict = Field(default_factory=dict)


class InstallIn(BaseModel):
    node: str
    tool: str
    approved: bool = False


class CreditIn(BaseModel):
    id: str
    provider: str
    type: str = "prepaid"
    label: str | None = None
    amount: float | None = None
    unit: str | None = None
    expires: str | None = None
    renews: str | None = None
    reset: dict | None = None
    models: list[str] | None = None
    monthly_budget: float | None = None
    auto_balance: bool = False


def _strip_heavy(node: dict) -> dict:
    n = dict(node)
    n["mcp"] = {k: {kk: vv for kk, vv in v.items() if kk != "tool_list"} | {"tool_names": [t["name"] for t in v.get("tool_list", [])]}
                for k, v in (node.get("mcp") or {}).items()}
    return n


def create_app(engine) -> FastAPI:
    cfg = engine.config

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            await engine.refresh_local_inventory()
        except Exception:
            log.exception("initial inventory failed")
        sched = asyncio.create_task(engine.scheduler())
        yield
        sched.cancel()
        await engine.close()

    app = FastAPI(title="Switchboard", version="0.1.0", lifespan=lifespan)

    def auth(request: Request) -> None:
        host = request.client.host if request.client else ""
        if host in LOCAL_HOSTS and not cfg.server.get("require_token_locally"):
            return
        token = cfg.token
        supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip() \
            or request.cookies.get("sb_token", "") or request.query_params.get("token", "")
        if not token or not supplied or not hmac.compare_digest(supplied, token):
            raise HTTPException(401, "Missing or wrong Switchboard token")

    # ---- dashboard ------------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return resources.files("switchboard").joinpath("web", "static", "index.html").read_text(encoding="utf-8")

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True, "node": cfg.node_name}

    # ---- status, models -----------------------------------------------------------------
    @app.get("/api/status", dependencies=[Depends(auth)])
    async def status() -> dict:
        avail = engine.available_providers(refresh=True)
        return {
            "node": cfg.node_name, "role": cfg.role, "output_dir": str(cfg.output_dir),
            "providers": [dict(p.describe(), available=n in avail) for n, p in engine.providers.items()],
            "nodes_online": sum(1 for n in engine.nodes.list() if n["online"]),
            "running_tasks": len(engine.running), "router": cfg.router,
        }

    @app.get("/api/models", dependencies=[Depends(auth)])
    async def models() -> list[dict]:
        avail = engine.available_providers()
        return [dict(m.to_dict(), available=m.provider in avail) for m in engine.catalog.all()]

    # ---- planning & tasks --------------------------------------------------------------
    def _save_images(data_urls: list[str]) -> list:
        paths = []
        up = cfg.db_path.parent / "uploads"
        up.mkdir(exist_ok=True)
        for url in data_urls[:8]:
            m = re.match(r"data:image/(\w+);base64,(.*)", url, re.S)
            if not m:
                continue
            p = up / f"{uuid.uuid4().hex}.{m.group(1).replace('jpeg', 'jpg')}"
            p.write_bytes(base64.b64decode(m.group(2)))
            paths.append(p)
        return paths

    @app.post("/api/plan", dependencies=[Depends(auth)])
    async def plan(body: PlanIn) -> dict:
        try:
            p = await engine.plan(body.prompt, body.provider, body.model, body.effort, body.mode, body.tools,
                                  images=len(body.images))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return p.to_dict()

    @app.post("/api/tasks", dependencies=[Depends(auth)])
    async def create_task(body: TaskIn):
        try:
            p = Plan.from_dict(body.plan) if body.plan else await engine.plan(
                body.prompt, body.provider, body.model, body.effort, body.mode, body.tools, images=len(body.images))
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(400, str(exc))
        if p.needs_confirmation and not body.confirm:
            return JSONResponse({"needs_confirmation": True, "plan": p.to_dict()}, status_code=409)
        task_id = engine.start_task(body.prompt, p, _save_images(body.images), body.history)
        return {"task_id": task_id, "plan": p.to_dict()}

    @app.get("/api/tasks", dependencies=[Depends(auth)])
    async def tasks(limit: int = 30) -> list[dict]:
        return engine.store.recent_tasks(limit)

    @app.get("/api/tasks/{task_id}", dependencies=[Depends(auth)])
    async def task(task_id: str) -> dict:
        t = engine.store.get_task(task_id)
        if not t:
            raise HTTPException(404, "no such task")
        return t

    # ---- credits ---------------------------------------------------------------------------
    @app.get("/api/credits", dependencies=[Depends(auth)])
    async def credits() -> dict:
        return engine.credits()

    @app.get("/api/alerts", dependencies=[Depends(auth)])
    async def alerts() -> list[dict]:
        engine.ledger.reload()
        return [a.to_dict() for a in engine.ledger.alerts()]

    @app.post("/api/credits/refresh", dependencies=[Depends(auth)])
    async def credits_refresh() -> dict:
        return await engine.refresh_balances()

    @app.post("/api/credits/sources", dependencies=[Depends(auth)])
    async def credits_add(body: CreditIn) -> dict:
        entry = {k: v for k, v in body.model_dump().items() if v not in (None, [], False)}
        if body.type == "prepaid" and body.amount is not None:
            entry["added"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        try:
            CreditSource.parse(entry)
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, f"invalid credit source: {exc}")
        data = cfg.credits_raw()
        sources = [s for s in (data.get("sources") or []) if s.get("id") != body.id] + [entry]
        data["sources"] = sources
        cfg.save_credits(data)
        engine.ledger.reload()
        return {"ok": True, "sources": len(sources)}

    @app.delete("/api/credits/sources/{source_id}", dependencies=[Depends(auth)])
    async def credits_delete(source_id: str) -> dict:
        data = cfg.credits_raw()
        data["sources"] = [s for s in (data.get("sources") or []) if s.get("id") != source_id]
        cfg.save_credits(data)
        engine.ledger.reload()
        return {"ok": True}

    @app.get("/api/usage", dependencies=[Depends(auth)])
    async def usage(days: int = 30) -> list[dict]:
        return engine.store.usage_summary(time.time() - days * 86400)

    # ---- machines, devices, tools ----------------------------------------------------------
    @app.get("/api/nodes", dependencies=[Depends(auth)])
    async def nodes() -> list[dict]:
        return [_strip_heavy(n) for n in engine.nodes.list()]

    @app.post("/api/nodes/refresh", dependencies=[Depends(auth)])
    async def nodes_refresh() -> list[dict]:
        await engine.refresh_local_inventory()

        async def one(name: str):
            try:
                inv = await engine.nodes.submit(name, "inventory", {}, timeout=90)
                engine.nodes.heartbeat(inv)
            except Exception as exc:
                log.warning("inventory from %s failed: %s", name, exc)

        await asyncio.gather(*(one(n["node"]) for n in engine.nodes.list() if n["online"] and not n["local"]))
        return [_strip_heavy(n) for n in engine.nodes.list()]

    @app.post("/api/devices/action", dependencies=[Depends(auth)])
    async def device_action(body: DeviceActionIn) -> dict:
        try:
            return await engine.nodes.submit(body.node, "device_action",
                                             {"device": body.device, "action": body.action, "args": body.args})
        except Exception as exc:
            raise HTTPException(502, str(exc))

    @app.get("/api/advisor", dependencies=[Depends(auth)])
    async def advisor_all() -> list[dict]:
        return advisor.all_missing(engine.nodes.list())

    @app.post("/api/tools/install", dependencies=[Depends(auth)])
    async def install(body: InstallIn) -> dict:
        try:
            return await engine.nodes.submit(body.node, "install_tool", {"tool": body.tool, "approved": body.approved},
                                             timeout=1900)
        except Exception as exc:
            raise HTTPException(502, str(exc))

    @app.get("/api/mcp", dependencies=[Depends(auth)])
    async def mcp() -> dict:
        return {"locations": engine.nodes.mcp_locations(),
                "catalog": {k: {kk: v.get(kk) for kk in ("description", "platforms", "setup", "repo", "requires")}
                            for k, v in mcp_catalog().items()}}

    # ---- agents ----------------------------------------------------------------------------
    @app.post("/api/agent/heartbeat", dependencies=[Depends(auth)])
    async def heartbeat(body: HeartbeatIn) -> dict:
        if not body.inventory.get("node"):
            raise HTTPException(400, "inventory.node missing")
        if body.inventory["node"] == cfg.node_name:
            raise HTTPException(409, "an agent is using the hub's node name; give it a unique node.name")
        engine.nodes.heartbeat(body.inventory)
        return {"ok": True, "hub": cfg.node_name}

    @app.get("/api/agent/jobs", dependencies=[Depends(auth)])
    async def next_job(node: str, wait: float = 25):
        if node not in engine.nodes.nodes:
            return Response(status_code=428)  # hub restarted or never heard of this agent: heartbeat first
        engine.nodes.nodes[node]["last_seen"] = time.time()
        job = await engine.nodes.next_job(node, min(wait, 55))
        if job is None:
            return Response(status_code=204)
        return job.to_dict()

    @app.post("/api/agent/jobs/{job_id}/ack", dependencies=[Depends(auth)])
    async def job_ack(job_id: str) -> dict:
        return {"acked": engine.nodes.ack(job_id)}

    @app.post("/api/agent/jobs/{job_id}", dependencies=[Depends(auth)])
    async def job_result(job_id: str, body: JobResultIn) -> dict:
        return {"accepted": engine.nodes.complete(job_id, body.model_dump())}

    return app
