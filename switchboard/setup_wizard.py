"""The setup wizard: choose hub or agent, find the hub, pair with a code, name this computer.

Browser version: a tiny local web server that opens in your browser (the packaged app shows it on
first launch, and the tray menu's "Change setup..." reopens it). It listens on 127.0.0.1 and every
call needs the random key in its address, so other websites and other users can't drive it.

Terminal version (`switchboard setup --text`, automatic over SSH or without a display): the same
questions as prompts. `--web` serves the browser version to other devices, for a headless Pi.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import secrets
import socket
import sys
import threading
import time
import webbrowser
from importlib import resources
from pathlib import Path

import httpx

from . import __version__, setup
from .paths import os_family, platform_tag

log = logging.getLogger("switchboard.setup")
PLATFORM_LABEL = {"windows": "Windows PC", "macos": "Mac", "pi": "Raspberry Pi", "linux": "Linux computer"}


def has_display() -> bool:
    if os_family() in ("windows", "macos"):
        return not os.environ.get("SSH_CONNECTION")
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")) and not os.environ.get("SSH_CONNECTION")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _lan_ip() -> str:
    with contextlib.suppress(OSError), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    return "127.0.0.1"


def wait_ready(url: str, timeout: float = 45) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with contextlib.suppress(httpx.HTTPError):
            if httpx.get(url + "/healthz", timeout=3).status_code == 200:
                return True
        time.sleep(1)
    return False


# ---- browser wizard -------------------------------------------------------------------------------
def build_app(key: str, home: Path | None = None, remote: bool = False, on_done=None, start_services: bool = True):
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import HTMLResponse, JSONResponse, Response
    from starlette.routing import Route

    from .discovery import find_hubs
    from .localproxy import LOCAL_NAMES

    page = resources.files("switchboard").joinpath("web", "static", "setup.html").read_text(encoding="utf-8")
    state = {"result": None}

    def allowed(request: Request) -> bool:
        host = (request.headers.get("host") or "").rsplit(":", 1)[0].lower()
        if not remote and host not in LOCAL_NAMES and host != "testserver":
            return False  # DNS rebinding
        supplied = request.query_params.get("k") or request.headers.get("x-setup-key", "")
        return secrets.compare_digest(supplied, key)

    def guard(fn):
        async def wrapped(request: Request):
            if not allowed(request):
                return JSONResponse({"detail": "This setup link has expired. Open Switchboard again."}, status_code=403)
            return await fn(request)
        return wrapped

    async def index(request: Request):
        if not allowed(request):
            return HTMLResponse("<h3 style='font-family:system-ui'>This setup link has expired. Open Switchboard again.</h3>", 403)
        return HTMLResponse(page, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})

    async def info(request: Request):
        tag = platform_tag()
        return JSONResponse({"current": setup.current(home), "default_name": setup.default_name(),
                             "platform": tag, "platform_label": PLATFORM_LABEL.get(tag, "computer"),
                             "recommended": setup.recommended_role(), "version": __version__})

    async def discover(request: Request):
        hubs = await asyncio.to_thread(find_hubs, 1.5)
        return JSONResponse({"hubs": hubs})

    async def check(request: Request):
        body = await request.json()
        try:
            return JSONResponse(await asyncio.to_thread(setup.check_hub, body.get("url", "")))
        except setup.SetupError as e:
            return JSONResponse({"detail": str(e)}, status_code=400)

    async def finish(request: Request):
        b = await request.json()
        try:
            res = await asyncio.to_thread(setup.apply, b.get("role"), b.get("name"), b.get("hub_url"), b.get("token"),
                                          b.get("code"), home, start_services)
        except setup.SetupError as e:
            return JSONResponse({"detail": str(e)}, status_code=400)
        except Exception as e:  # noqa: BLE001 - show anything unexpected instead of a blank failure
            log.exception("setup failed")
            return JSONResponse({"detail": f"Setup failed: {e}"}, status_code=500)
        state["result"] = res
        return JSONResponse({k: v for k, v in res.items() if k != "written"})

    async def ready(request: Request):
        res = state["result"]
        if not res:
            return JSONResponse({"ready": False})
        ok = await asyncio.to_thread(wait_ready, res["dashboard_url"], 4)
        return JSONResponse({"ready": ok, "dashboard_url": res["dashboard_url"]})

    async def bye(request: Request):
        if on_done:
            on_done()
        return Response(status_code=204)

    routes = [Route("/", index), Route("/api/info", guard(info)), Route("/api/discover", guard(discover)),
              Route("/api/check", guard(check), methods=["POST"]), Route("/api/finish", guard(finish), methods=["POST"]),
              Route("/api/ready", guard(ready)), Route("/api/bye", guard(bye), methods=["POST"])]
    return Starlette(routes=routes)


def run_browser_wizard(home: Path | None = None, remote: bool = False, open_browser: bool = True,
                       port: int | None = None, idle_minutes: int = 30) -> None:
    import uvicorn

    key = secrets.token_urlsafe(16)
    server_box: dict = {}

    def done() -> None:
        # Let the page get its answer, then stop.
        threading.Timer(1.5, lambda: setattr(server_box["server"], "should_exit", True)).start()

    app = build_app(key, home, remote=remote, on_done=done)
    host = "0.0.0.0" if remote else "127.0.0.1"
    port = port or (8767 if remote else _free_port())
    shown = _lan_ip() if remote else "localhost"
    url = f"http://{shown}:{port}/?k={key}"
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_config=None, access_log=False))
    server_box["server"] = server
    threading.Timer(idle_minutes * 60, lambda: setattr(server, "should_exit", True)).start()
    print(f"Switchboard setup: {url}", flush=True)
    if remote:
        print("Open that address on your phone or computer (same network) to finish setting up this machine.", flush=True)
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    server.run()
    os._exit(0)  # the idle timer thread must not keep the process alive


# ---- terminal wizard ---------------------------------------------------------------------------------
def _ask(prompt: str, default: str = "") -> str:
    hint = f" [{default}]" if default else ""
    ans = input(f"{prompt}{hint}: ").strip()
    return ans or default


def run_text_wizard(home: Path | None = None, role: str | None = None, name: str | None = None,
                    hub_url: str | None = None, code: str | None = None, token: str | None = None,
                    start: bool = True) -> None:
    from .discovery import find_hubs

    interactive = sys.stdin.isatty() and not (role and (role == "hub" or hub_url and (code or token)))
    cur = setup.current(home)
    tag = platform_tag()
    print(f"Switchboard {__version__} setup on this {PLATFORM_LABEL.get(tag, 'computer')}\n")
    if cur.get("configured"):
        print(f"Currently set up as {cur['role']} '{cur['name']}'.")
        if interactive and _ask("Change it? (y/N)", "n").lower() not in ("y", "yes"):
            return
    hubs = [] if role == "hub" else find_hubs(1.5)
    if not role:
        rec = "agent" if hubs else setup.recommended_role()
        if hubs:
            print("Found on your network: " + ", ".join(f"{h['name']} ({h['url']})" for h in hubs))
        print("\n  hub   = the always-on brain (one per home; a Raspberry Pi is ideal)\n"
              "  agent = connect this computer to your hub\n")
        while role not in ("hub", "agent"):
            role = _ask("Set this computer up as hub or agent?", rec).lower()
    name = setup.clean_name(name or (_ask("Name for this computer", setup.default_name()) if interactive else None))
    if role == "agent":
        while True:
            if not hub_url:
                if hubs and interactive:
                    for i, h in enumerate(hubs, 1):
                        print(f"  {i}. {h['name']}  {h['url']}")
                    pick = _ask("Hub number, or type its address", "1")
                    hub_url = hubs[int(pick) - 1]["url"] if pick.isdigit() and 0 < int(pick) <= len(hubs) else pick
                elif interactive:
                    hub_url = _ask("Hub address (for example 192.168.1.50)")
                else:
                    raise SystemExit("No hub found on the network. Pass --hub http://<hub-ip>:8765")
            if not (code or token):
                if not interactive:
                    raise SystemExit("Pass --code (from the hub's dashboard: Machines > Connect a computer) or --token")
                entered = _ask("Pairing code from the hub's dashboard (Machines > Connect a computer), or its token")
                code, token = (entered, None) if entered.replace(" ", "").isdigit() else (None, entered)
            try:
                res = setup.apply("agent", name, hub_url, token, code, home, start)
                break
            except setup.SetupError as e:
                print(f"\n! {e}\n")
                if not interactive:
                    raise SystemExit(1) from None
                hub_url = code = token = None
    else:
        res = setup.apply("hub", name, home=home, start=start)
    print()
    for m in res.get("messages", []):
        print(m)
    if role == "hub":
        port = 8765
        print(f"\nThis computer is now your Switchboard hub '{name}'.")
        print(f"Dashboard: http://{_lan_ip()}:{port}  (on this computer: http://localhost:{port})")
        print("To add your PC and Mac: install the Switchboard app on them; it finds this hub by itself and asks for a\n"
              "pairing code, which the dashboard shows under Machines > Connect a computer.")
    else:
        print(f"\nThis computer is now connected to your hub as '{name}'. Dashboard: {res['dashboard_url']}")
