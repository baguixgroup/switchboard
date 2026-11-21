"""Local dashboard relay for agent machines: http://localhost:8766 -> the hub, with the token added.

Why: on your PC/Mac you just open localhost (no token prompt), and browsers treat localhost as a
secure origin, so the dashboard can be installed as an app. It listens on 127.0.0.1 only and
refuses requests from other websites (Host/Origin checks), so the token it adds can't be abused.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from urllib.parse import urlsplit

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Route

log = logging.getLogger("switchboard.localproxy")
LOCAL_NAMES = {"localhost", "127.0.0.1", "[::1]", "::1"}
PASS_REQ = {"content-type", "accept", "range", "if-none-match"}
PASS_RESP = {"content-type", "content-disposition", "cache-control", "service-worker-allowed", "etag", "content-range", "accept-ranges"}
OFFLINE = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Tazrivo</title><body style="font:16px system-ui;padding:40px;max-width:560px;margin:auto">
<h2>Can't reach your Tazrivo hub</h2><p>This computer is set to use the hub at <b>{hub}</b>, but it isn't
answering. Check that the hub machine is on and connected to your network, then reload this page.</p></body>"""


def local_request_ok(request: Request) -> bool:
    """Same-machine, same-site requests only (blocks DNS rebinding and cross-site requests)."""
    host = (request.headers.get("host") or "").rsplit(":", 1)[0].lower()
    if host not in LOCAL_NAMES and host != "testserver":
        return False
    origin = request.headers.get("origin")
    if origin and origin != "null":
        o = urlsplit(origin)
        if (o.hostname or "").lower() not in LOCAL_NAMES:
            return False
    return True


def build_proxy_app(config) -> Starlette:
    from .web.voice_api import VoiceAPI
    voice=VoiceAPI(config)
    hub = config.hub_url
    client = httpx.AsyncClient(base_url=hub, timeout=httpx.Timeout(300, connect=8))

    async def relay(request: Request) -> Response:
        if not local_request_ok(request):
            return JSONResponse({"detail": "Refused: requests must come from this computer's own dashboard."}, 403)
        path = request.url.path
        if path.startswith('/api/local/voice/'):
            return await voice.handle(request)
        if path == '/api/local/laith-desktop':
            from .desktop_pet import control,status
            try:
                if request.method=='GET':return JSONResponse(await asyncio.to_thread(status,config),headers={'Cache-Control':'no-store'})
                if request.method!='POST':return JSONResponse({'detail':'Method not allowed'},405)
                if request.headers.get('origin')=='null':return JSONResponse({'detail':'Use your local Tazrivo app.'},403)
                raw=await request.body()
                if len(raw)>256:raise ValueError('Request is too large.')
                import json
                action=json.loads(raw).get('action')
                if action not in ('show','hide','panel','pause','resume'):raise ValueError('Unknown desktop companion action.')
                return JSONResponse(await asyncio.to_thread(control,config,action),headers={'Cache-Control':'no-store'})
            except (ValueError,TypeError,AttributeError) as exc:return JSONResponse({'detail':str(exc)},400)
        if path == "/api/local/install-auth-key":
            from .install_auth import public_key
            if request.method != "GET":
                return JSONResponse({"detail": "Method not allowed"}, 405)
            return JSONResponse(public_key(config), headers={"Cache-Control": "no-store"})
        if path.startswith("/api/agent/"):
            return JSONResponse({"detail": "not available through the local relay"}, 404)
        headers = {k: v for k, v in request.headers.items() if k.lower() in PASS_REQ}
        if config.token:
            headers["Authorization"] = f"Bearer {config.token}"
        url = path + (f"?{request.url.query}" if request.url.query else "")
        try:
            if request.method=='GET' and path.startswith('/api/capabilities/jobs/') and path.endswith('/events'):
                upstream=await client.send(client.build_request('GET',url,headers=headers),stream=True)
                async def chunks():
                    try:
                        async for chunk in upstream.aiter_bytes():yield chunk
                    finally:await upstream.aclose()
                return StreamingResponse(chunks(),status_code=upstream.status_code,headers={k:v for k,v in upstream.headers.items() if k.lower() in PASS_RESP})
            r = await client.request(request.method, url, content=await request.body(), headers=headers)
        except httpx.HTTPError as exc:
            log.warning("hub unreachable: %s", exc)
            if path.startswith("/api/"):
                return JSONResponse({"detail": f"Hub {hub} is not reachable"}, 502)
            return HTMLResponse(OFFLINE.format(hub=hub), 502)
        return Response(r.content, status_code=r.status_code,
                        headers={k: v for k, v in r.headers.items() if k.lower() in PASS_RESP})

    @contextlib.asynccontextmanager
    async def lifespan(app):
        yield
        await client.aclose()

    methods = ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"]
    return Starlette(routes=[Route("/{path:path}", relay, methods=methods)], lifespan=lifespan)


async def serve_local_dashboard(config) -> None:
    """Run the relay until cancelled. Quietly skipped if the port is taken."""
    import uvicorn

    port = int(config.node.get("local_dashboard_port") or 0)
    if not port:
        return
    try:
        server = uvicorn.Server(uvicorn.Config(build_proxy_app(config), host="127.0.0.1", port=port, log_config=None,
                                               access_log=False))
        log.info("local dashboard: http://localhost:%s", port)
        await server.serve()
    except asyncio.CancelledError:
        raise
    except BaseException as exc:  # never take the agent down with it
        log.warning("local dashboard on port %s not started: %s", port, exc)
