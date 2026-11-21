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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response as RawResponse
from pydantic import BaseModel, Field

from .. import __version__, advisor
from ..credits import CreditSource
from ..inventory import mcp_catalog, resolve_tool_id
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
    billing: str | None = None  # plans_only | plans_first | any
    direct_only: bool = False
    images: list[str] = Field(default_factory=list)  # data URLs


class GatewayIn(BaseModel):
    servers: list[str] = Field(default_factory=list)
    task: str | None = None


class GatewayCallIn(BaseModel):
    name: str
    arguments: dict = Field(default_factory=dict)


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
    accept_agreements: bool = False
    auth_route: str | None = None
    credential_envelope: dict | None = None
    auth_handle: str | None = None


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
        engine.capabilities.recover()
        await app.state.recover_capability_voice()
        engine.studio.recover()
        for task in engine.store.recent_tasks(limit=10000):
            if task["status"] in ("queued", "running") and (task.get("plan") or {}).get("kind") == "installation":
                engine.store.save_task(task["id"], "failed", error="Hub restarted before installation status was received. Rescan the target before retrying; its installer may still be running.")
        try:
            await engine.refresh_local_inventory()
        except Exception:
            log.exception("initial inventory failed")
        sched = asyncio.create_task(engine.scheduler())
        beacon = None
        if cfg.server.get("discovery", True) and cfg.server.get("host", "0.0.0.0") not in ("127.0.0.1", "localhost"):
            from ..discovery import start_responder

            beacon = await start_responder(cfg.node_name, int(cfg.server.get("port", 8765)))
        yield
        if beacon:
            beacon.close()
        sched.cancel()
        await engine.close()

    app = FastAPI(title="Switchboard", version=__version__, lifespan=lifespan)

    @app.middleware("http")
    async def local_guard(request: Request, call_next):
        # Token-less access is allowed from this machine only, and only for its own pages: block other
        # websites (cross-site requests) and DNS-rebinding tricks from using it.
        from ..localproxy import local_request_ok

        host = request.client.host if request.client else ""
        if host in LOCAL_HOSTS and host != "testclient" and not request.headers.get("authorization") \
                and not local_request_ok(request):
            return JSONResponse({"detail": "Refused: cross-site request"}, status_code=403)
        return await call_next(request)

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
    static = resources.files("switchboard").joinpath("web", "static")
    types = {".css": "text/css", ".js": "text/javascript", ".png": "image/png", ".svg": "image/svg+xml",
             ".webmanifest": "application/manifest+json", ".html": "text/html"}

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return static.joinpath("index.html").read_text(encoding="utf-8")

    @app.get("/static/{name}")
    async def static_file(name: str):
        if "/" in name or "\\" in name or name.startswith("."):
            raise HTTPException(404)
        f = static.joinpath(name)
        if not f.is_file():
            raise HTTPException(404)
        ext = "." + name.rsplit(".", 1)[-1]
        return RawResponse(f.read_bytes(), media_type=types.get(ext, "application/octet-stream"),
                           headers={"Cache-Control": "no-cache"})

    @app.get("/manifest.webmanifest")
    async def manifest():
        return RawResponse(static.joinpath("manifest.webmanifest").read_bytes(), media_type="application/manifest+json")

    @app.get("/sw.js")
    async def service_worker():
        # Served from the root so it can control the whole dashboard.
        return RawResponse(static.joinpath("sw.js").read_bytes(), media_type="text/javascript",
                           headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True, "node": cfg.node_name}

    # ---- status, models -----------------------------------------------------------------
    @app.get("/api/status", dependencies=[Depends(auth)])
    async def status() -> dict:
        avail = engine.available_providers(refresh=True)
        return {
            "node": cfg.node_name, "role": cfg.role, "output_dir": str(cfg.output_dir), "version": __version__,
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
                                  images=len(body.images), billing=body.billing, direct_only=body.direct_only)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return p.to_dict()

    @app.post("/api/tasks", dependencies=[Depends(auth)])
    async def create_task(body: TaskIn):
        try:
            p = Plan.from_dict(body.plan) if body.plan else await engine.plan(
                body.prompt, body.provider, body.model, body.effort, body.mode, body.tools, images=len(body.images),
                billing=body.billing,direct_only=body.direct_only)
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

    from .studio_api import install_studio_routes
    install_studio_routes(app, engine, auth)
    from .companion_api import install_companion_routes
    install_companion_routes(app, engine, auth)

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
            node = engine.resolve_node(body.node)
            tool = resolve_tool_id(body.tool)
            if not tool:
                raise ValueError("Unknown app. Choose one from Install apps.")
            if body.approved:
                return JSONResponse({"task_id": engine.start_install(node, tool, body.accept_agreements, body.auth_route, body.auth_handle),
                                     "status": "queued"}, status_code=202)
            result = await engine.nodes.submit(node, "install_tool", {"tool": tool, "approved": False}, timeout=60)
            result["auth_routes"] = engine.install_routes(node)
            return result
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        except Exception as exc:
            raise HTTPException(502, str(exc))

    @app.post("/api/tools/install/auth", dependencies=[Depends(auth)])
    async def install_auth(body: InstallIn) -> dict:
        try:
            node = engine.resolve_node(body.node)
            route = engine.install_route(node, body.auth_route or "")
            return await engine.nodes.submit(route["controller"], "admin_install_check",
                                             {"profile": route["profile"], "target_node": node, "tool": body.tool,
                                              "credential_envelope": body.credential_envelope}, timeout=60)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        except Exception as exc:
            raise HTTPException(502, str(exc))

    @app.get("/api/mcp", dependencies=[Depends(auth)])
    async def mcp() -> dict:
        return {"locations": engine.nodes.mcp_locations(),
                "catalog": {k: {kk: v.get(kk) for kk in ("description", "platforms", "setup", "repo", "requires")}
                            for k, v in mcp_catalog().items()}}

    # ---- tool gateway for plan CLIs ---------------------------------------------------------
    @app.post("/api/gateway", dependencies=[Depends(auth)])
    async def gateway_open(body: GatewayIn) -> dict:
        try:
            gid, tb = await engine.create_gateway(body.servers, body.task)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {"id": gid, "tools": [{"name": t.name, "description": t.description, "schema": t.schema} for t in tb.specs()]}

    @app.post("/api/gateway/{gid:path}/call", dependencies=[Depends(auth)])
    async def gateway_call(gid: str, body: GatewayCallIn) -> dict:
        entry = engine.gateways.get(gid)
        if not entry:
            raise HTTPException(404, "gateway session expired")
        text, is_error = await entry[0].call(body.name, body.arguments)
        return {"text": text, "is_error": is_error}

    @app.get("/api/plans", dependencies=[Depends(auth)])
    async def plans() -> list[dict]:
        return engine.plans_status()

    @app.delete("/api/plans/{provider}/limit", dependencies=[Depends(auth)])
    async def clear_limit(provider: str) -> dict:
        engine.store.clear_limit(provider)
        return {"ok": True}

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

    from .manage import register as register_manage
    from .pairing import register as register_pairing

    register_manage(app, engine, auth)
    register_pairing(app, engine, auth)
    from .capability_api import register as register_capabilities
    register_capabilities(app,engine,auth)
    return app
