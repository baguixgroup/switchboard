"""switchboard command line."""
from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import json
import logging
import logging.handlers
import mimetypes
import os
import re
import secrets
import shutil
import socket
import sys
import time
from pathlib import Path

import httpx

from . import __version__, paths
from .config import Config, default_text, write_env_value

from .setup import SECRETS_TEMPLATE  # noqa: E402,F401  (kept importable from here)


# ---------------------------------------------------------------------------------------------
def _setup_logging(cfg: Config, name: str, service: bool, level: int = logging.INFO) -> None:
    if service:
        if sys.stdout is None:
            sys.stdout = open(os.devnull, "w")
        if sys.stderr is None:
            sys.stderr = open(os.devnull, "w")
        (cfg.home / "data").mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = logging.handlers.RotatingFileHandler(cfg.home / "data" / f"{name}.log",
                                                                        maxBytes=5_000_000, backupCount=3,
                                                                        encoding="utf-8")
    else:
        handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=level, handlers=[handler], force=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _need_config(cfg: Config) -> None:
    if not cfg.exists:
        raise SystemExit(f"No config at {cfg.path}. Run `switchboard init` first.")


def _hub_up(cfg: Config) -> bool:
    try:
        return httpx.get(cfg.hub_url + "/healthz", timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


@contextlib.contextmanager
def hub_client(cfg: Config):
    headers = {"Authorization": f"Bearer {cfg.token}"} if cfg.token else {}
    if _hub_up(cfg):
        with httpx.Client(base_url=cfg.hub_url, headers=headers, timeout=120) as c:
            yield c
    elif cfg.role == "hub":
        print("(hub service is not running: using a temporary in-process hub)", file=sys.stderr)
        from fastapi.testclient import TestClient

        from .engine import Engine
        from .web.app import create_app

        with TestClient(create_app(Engine(cfg)), headers=headers) as c:
            yield c
    else:
        raise SystemExit(f"Cannot reach the hub at {cfg.hub_url}. Is it running? (node.hub_url in {cfg.path})")


def _json(r) -> dict | list:
    if r.status_code >= 400 and r.status_code != 409:
        try:
            detail = r.json().get("detail")
        except ValueError:
            detail = r.text
        raise SystemExit(f"Error {r.status_code}: {detail}")
    return r.json()


def _money(x) -> str:
    if x is None:
        return "-"
    return "<$0.0001" if 0 < x < 0.0001 else f"${x:.4f}"


def _print_plan(p: dict) -> None:
    eff = f" · effort {p['effort']}" if p.get("effort") else ""
    tools = f" · tools {', '.join(p['tools'])}" if p.get("tools") else ""
    print(f"→ {p['model_label']} ({p['provider']}){eff}{tools}")
    print(f"  est. {_money(p['est_cost_usd'])} (effective {_money(p['effective_cost_usd'])}) · paid by {p['source_label']}")
    print(f"  why: {'; '.join(p['reasons'])}")
    for w in p.get("warnings") or []:
        print(f"  ! {w}")
    if p.get("alternatives"):
        print("  alternatives: " + ", ".join(f"{a['label']} ({_money(a['effective_cost_usd'])})" for a in p["alternatives"][:3]))
    for s in p.get("suggestions") or []:
        if s["type"] == "mcp":
            print(f"  tip: {s['name']}: {s['why']} -> {s['command']}")
        else:
            opts = "; ".join(f"{i['node']}: {i['command']}" for i in s["install"][:3])
            print(f"  tip: install {s['name']} ({s['why']}) -> {opts}")


def _image_urls(files: list[str]) -> list[str]:
    out = []
    for f in files or []:
        mime = mimetypes.guess_type(f)[0] or "image/png"
        out.append(f"data:{mime};base64," + base64.b64encode(Path(f).read_bytes()).decode())
    return out


def _yes(question: str, default: bool = False) -> bool:
    if not sys.stdin.isatty():
        return default
    ans = input(f"{question} [{'Y/n' if default else 'y/N'}] ").strip().lower()
    return default if not ans else ans.startswith("y")


# ---------------------------------------------------------------------------------------------
def cmd_init(cfg: Config, a) -> None:
    from . import setup

    role = a.role or (cfg.role if cfg.exists else "hub")
    name = setup.clean_name(a.name) if (a.name or not cfg.exists) else cfg.node_name
    if cfg.exists and not a.force and not a.token:
        print(f"Config already exists: {cfg.path} (use --force to change it, or `switchboard setup`)")
        return
    res = setup.write_config(role, name, a.hub_url, a.token, cfg.home)
    for f in res["written"]:
        print(f"Wrote {f}")
    cfg = Config(cfg.home)
    print(f"\nNode '{name}' ({role}). Output folder: {cfg.output_dir}")
    if role == "hub":
        print(f"\nNext:\n  1. Add your API keys to {cfg.home / 'secrets.env'}\n  2. Add credits/free tiers: switchboard credits add --help"
              f"\n  3. Start it: switchboard serve   (or: switchboard service install --role hub)"
              f"\n  4. Open http://localhost:{cfg.server.get('port', 8765)} (everything else can be done there)"
              f"\n\nAgents need this token:  {cfg.token}")
    else:
        if res["token"] == setup.PLACEHOLDER:
            print(f"\n! Put the hub's token into {cfg.home / 'secrets.env'} (SWITCHBOARD_TOKEN=...)")
        print("\nNext:\n  switchboard doctor\n  switchboard agent   (or: switchboard service install --role agent)")


def cmd_setup(cfg: Config, a) -> None:
    """Set this computer up as hub or agent: browser wizard, terminal questions, or flags."""
    from . import setup_wizard

    flags = a.role or a.hub or a.code or a.token
    if a.web:
        setup_wizard.run_browser_wizard(cfg.home, remote=True, open_browser=False, port=a.port)
    elif a.text or flags or not setup_wizard.has_display():
        setup_wizard.run_text_wizard(cfg.home, a.role, a.name, a.hub, a.code, a.token, start=not a.no_start)
    else:
        setup_wizard.run_browser_wizard(cfg.home)


def cmd_launch(cfg: Config, a) -> None:
    """What opening the app does: the setup wizard the first time, the dashboard after that."""
    import webbrowser

    from . import setup, setup_wizard

    if not cfg.exists or cfg.role not in ("hub", "agent") or (cfg.role == "agent" and cfg.token in ("", setup.PLACEHOLDER)):
        if setup_wizard.has_display():
            setup_wizard.run_browser_wizard(cfg.home)
        else:
            setup_wizard.run_text_wizard(cfg.home)
        return
    url = setup.dashboard_url(cfg.role, cfg.home)
    if not setup_wizard.wait_ready(url, timeout=2):
        from . import service

        with contextlib.suppress(Exception):
            print(service.install(cfg.role))  # started at login normally; start it now if it isn't running
        setup_wizard.wait_ready(url, timeout=20)
    if setup_wizard.has_display():
        from .window import open_window

        open_window(url,page=getattr(a,'page',None))  # app window (Windows/macOS), else the browser
    else:
        print(f"Tazrivo is set up as {cfg.role} '{cfg.node_name}'. Dashboard: {url}")


def cmd_pet(cfg: Config, a) -> None:
    from .desktop_pet import control, run
    if a.action=='run' and not paths.is_frozen():run(cfg,a.panel)
    elif a.action=='run':print(json.dumps(control(cfg,'panel' if a.panel else 'show'),indent=2))
    else:print(json.dumps(control(cfg,a.action),indent=2))


def cmd_serve(cfg: Config, a) -> None:
    _need_config(cfg)
    _setup_logging(cfg, "hub", a.service)
    import uvicorn

    from .engine import Engine
    from .web.app import create_app

    if cfg.role != "hub":
        logging.warning("config says role=%s; serving as hub anyway", cfg.role)
    host = a.host or cfg.server.get("host", "0.0.0.0")
    port = int(a.port or cfg.server.get("port", 8765))
    if host not in ("127.0.0.1", "localhost") and not cfg.token:
        logging.warning("No SWITCHBOARD_TOKEN: remote machines will be refused until you set one in secrets.env")
    logging.info("Tazrivo hub '%s' on http://%s:%s  output: %s", cfg.node_name, host, port, cfg.output_dir)
    uvicorn.run(create_app(Engine(cfg)), host=host, port=port, log_config=None, access_log=False)


def cmd_agent(cfg: Config, a) -> None:
    _need_config(cfg)
    _setup_logging(cfg, "agent", a.service)
    from .agent import run_agent

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run_agent(cfg))


def cmd_tray(cfg: Config, a) -> None:
    _need_config(cfg)
    _setup_logging(cfg, "tray", a.service)
    from .tray import run_tray, tray_available

    if not tray_available():
        raise SystemExit("The tray icon needs extra packages: pip install \"switchboard-ai[tray]\"")
    run_tray(cfg)


def _plan_body(a, prompt: str) -> dict:
    tools = "auto"
    if a.tools:
        tools = "none" if a.tools == "none" else [t.strip() for t in a.tools.split(",") if t.strip()]
    return {"prompt": prompt, "provider": a.provider, "model": a.model, "effort": a.effort, "mode": a.mode,
            "tools": tools, "images": _image_urls(a.image), "billing": a.billing}


def _read_prompt(a) -> str:
    prompt = " ".join(a.prompt).strip()
    if not prompt or prompt == "-":
        prompt = sys.stdin.read().strip()
    if a.file:
        for f in a.file:
            prompt += f"\n\n--- {f} ---\n" + Path(f).read_text(encoding="utf-8", errors="replace")
    if not prompt:
        raise SystemExit("Empty prompt.")
    return prompt


def cmd_plan(cfg: Config, a) -> None:
    _need_config(cfg)
    with hub_client(cfg) as c:
        p = _json(c.post("/api/plan", json=_plan_body(a, _read_prompt(a))))
    print(json.dumps(p, indent=2) if a.json else "", end="")
    if not a.json:
        _print_plan(p)


def cmd_ask(cfg: Config, a) -> None:
    _need_config(cfg)
    prompt = _read_prompt(a)
    body = _plan_body(a, prompt)
    with hub_client(cfg) as c:
        plan = _json(c.post("/api/plan", json=body))
        if not a.quiet:
            _print_plan(plan)
        if any("overkill" in w for w in plan.get("warnings", [])) and not a.yes:
            if _yes("Use the recommended (lower) effort instead?", default=True):
                plan = _json(c.post("/api/plan", json=dict(body, effort=None)))
                if not a.quiet:
                    print(f"  -> effort {plan.get('effort')}, est. {_money(plan['effective_cost_usd'])}")
        if plan.get("needs_confirmation") and not a.yes:
            if not _yes(f"Estimated {_money(plan['effective_cost_usd'])}. Run it?"):
                raise SystemExit("Cancelled.")
        r = _json(c.post("/api/tasks", json=dict(body, plan=plan, confirm=True)))
        task_id = r["task_id"]
        if a.no_wait:
            print(f"Started task {task_id}")
            return
        spinner = "|/-\\"
        i = 0
        while True:
            t = _json(c.get(f"/api/tasks/{task_id}"))
            if t["status"] in ("done", "failed"):
                break
            if sys.stderr.isatty() and not a.quiet:
                print(f"\r  running {spinner[i % 4]}", end="", file=sys.stderr, flush=True)
            i += 1
            time.sleep(1)
        if sys.stderr.isatty() and not a.quiet:
            print("\r" + " " * 20 + "\r", end="", file=sys.stderr)
    if a.json:
        print(json.dumps(t, indent=2, default=str))
        return
    print(t["result"] if t["status"] == "done" else f"FAILED:\n{t['error']}")
    if not a.quiet:
        print(f"\n[{(t.get('plan') or {}).get('executed', '')} · {_money(t.get('cost_usd'))} · saved to {t.get('output_path')}]")


def cmd_credits(cfg: Config, a) -> None:
    _need_config(cfg)
    with hub_client(cfg) as c:
        if a.action == "refresh":
            print(json.dumps(_json(c.post("/api/credits/refresh")), indent=2))
            return
        if a.action == "add":
            entry = {"id": a.id, "provider": a.provider, "type": a.type, "amount": a.amount, "unit": a.unit,
                     "expires": a.expires, "renews": a.renews, "models": a.models.split(",") if a.models else None,
                     "monthly_budget": a.budget, "label": a.label, "auto_balance": a.auto_balance}
            if a.reset:
                entry["reset"] = {"every": a.reset, "tz": a.tz, "at": a.at, "day": a.day, "weekday": a.weekday}
            if not a.id or not a.provider:
                raise SystemExit("--id and --provider are required")
            _json(c.post("/api/credits/sources", json=entry))
            print(f"Saved {a.id}.")
            return
        if a.action == "remove":
            _json(c.delete(f"/api/credits/sources/{a.id}"))
            print(f"Removed {a.id}.")
            return
        data = _json(c.get("/api/credits"))
    if a.json:
        print(json.dumps(data, indent=2))
        return
    for al in data["alerts"]:
        print(f"[{al['severity'].upper()}] {al['title']}\n    {al['message']}")
    if data["alerts"]:
        print()
    print(f"{'source':24} {'provider':12} {'type':13} {'left':>14}  resets/expires")
    for s in data["sources"]:
        left = "tracking" if s["remaining"] is None else (_money(s["remaining"]) if s["unit"] == "usd" else f"{s['remaining']:g} {s['unit']}")
        when = s["expires"] or s["resets"] or ""
        flag = "" if s["available"] else "  (unavailable)"
        print(f"{s['label'][:24]:24} {s['provider'][:12]:12} {s['type']:13} {left:>14}  {when[:16]}{flag}")
    if data.get("plans"):
        print(f"\nPlans (billing: {data.get('billing')}):")
        for pl in data["plans"]:
            extra = f" until {time.strftime('%a %H:%M', time.localtime(pl['resets']))}" if pl.get("resets") else ""
            where = f" on {pl['where']}" if pl.get("where") else ""
            print(f"  {pl['provider']:14} {pl['status']}{extra}{where}")
    if data.get("untracked_providers"):
        print(f"\nPay-as-you-go, untracked: {', '.join(data['untracked_providers'])}")
    if not data["sources"]:
        print("\nNo credits tracked yet. Example:\n  switchboard credits add --id openai-promo --provider openai --type prepaid --amount 18 --expires 2026-12-31")


def cmd_alerts(cfg: Config, a) -> None:
    _need_config(cfg)
    with hub_client(cfg) as c:
        alerts = _json(c.get("/api/alerts"))
    if not alerts:
        print("No alerts.")
    for al in alerts:
        print(f"[{al['severity'].upper()}] {al['title']}\n    {al['message']}")


def cmd_models(cfg: Config, a) -> None:
    _need_config(cfg)
    if a.action == "sync":
        from .engine import Engine
        from .models_sync import sync

        rep = asyncio.run(sync(Engine(cfg), write=a.write))
        for name, st in rep["providers"].items():
            print(f"{name:12} {st['models']:4} models visible" + (f"  ERROR {st['error'][:120]}" if st["error"] else ""))
        if rep.get("price_error"):
            print(f"(could not fetch OpenRouter prices: {rep['price_error']})")
        for k in ("missing", "added"):
            if rep[k]:
                print(f"\n{k}: " + ", ".join(rep[k]))
        for r in rep["repriced"]:
            print(f"price change {r['model']}: {r['old']} -> {r['new']}")
        print("\nWritten to models.yaml." if a.write else "\nDry run. Add --write to save these changes to models.yaml.")
        return
    with hub_client(cfg) as c:
        models = _json(c.get("/api/models"))
    print(f"{'model':42} {'tier':9} {'$in/$out per 1M':>16}  efforts")
    for m in sorted(models, key=lambda m: (not m["available"], m["provider"], -m["q"])):
        if not m["available"] and not a.all:
            continue
        mark = "" if m["verified"] else " *"
        print(f"{(m['key'] + mark)[:42]:42} {m['tier']:9} {m['price'][0]:>7g}/{m['price'][1]:<8g}  {','.join(m['efforts']) or '-'}")
    print("\n* = seed entry, verify with `switchboard models sync`" + ("" if a.all else "  (--all shows models without keys)"))


def cmd_nodes(cfg: Config, a) -> None:
    _need_config(cfg)
    with hub_client(cfg) as c:
        nodes = _json(c.post("/api/nodes/refresh") if a.refresh else c.get("/api/nodes"))
    for n in nodes:
        print(f"{'●' if n['online'] else '○'} {n['node']:16} {n.get('platform', ''):8} {n.get('arch', ''):8} {n.get('output_dir', '')}")
        for d in n.get("devices") or []:
            print(f"     device  {d['id']:36} {d.get('name') or '':20} {d.get('state', '')}")
        for s, st in (n.get("mcp") or {}).items():
            print(f"     mcp     {s:36} {st.get('state')} ({st.get('tools')} tools){' ' + st['error'] if st.get('error') else ''}")


def cmd_device(cfg: Config, a) -> None:
    _need_config(cfg)
    args = dict(kv.split("=", 1) for kv in a.arg or [])
    with hub_client(cfg) as c:
        res = _json(c.post("/api/devices/action", json={"node": a.node or cfg.node_name, "device": a.device,
                                                          "action": a.action, "args": args}))
    print(res.get("output", ""))
    for f in res.get("files") or []:
        print(f"saved: {f}")
    if not res.get("ok"):
        sys.exit(1)


def cmd_tools(cfg: Config, a) -> None:
    if a.action == "list":
        from .inventory import detect_tools

        for tid, t in detect_tools().items():
            state = "installed" if t["installed"] else ("n/a: " + t["note"] if not t["supported"] else "missing")
            print(f"{tid:14} {t['name'][:44]:44} {state}")
        return
    _need_config(cfg)
    with hub_client(cfg) as c:
        if a.action == "advise":
            for t in _json(c.get("/api/advisor")):
                print(f"{t['name']}  ({', '.join(t['helps'])})")
                for i in t["install"]:
                    print(f"    {i['node']:14} {i['command']}")
            return
        if a.action == "install":
            node = a.node or cfg.node_name
            pre = _json(c.post("/api/tools/install", json={"node": node, "tool": a.tool, "approved": False}))
            if not pre.get("needs_approval"):
                print(pre.get("output") or pre)
                return
            if not (a.yes or _yes(f"Run on {node}: {pre['command']} ?")):
                raise SystemExit("Cancelled.")
            agreements = pre.get("agreements") or []
            for agreement in agreements:
                print(f"{agreement['label']}: {agreement['url']}")
            if agreements and not _yes("Accept these installation terms for this app?"):
                raise SystemExit("Cancelled.")
            res = _json(c.post("/api/tools/install", json={"node": node, "tool": a.tool,
                        "approved": True, "accept_agreements": bool(agreements)}))
            print(f"Installation job: {res['task_id']}", flush=True)
            import time
            while True:
                task = _json(c.get(f"/api/tasks/{res['task_id']}"))
                if task["status"] not in ("queued", "running"):
                    print(task.get("error") or task.get("result") or task["status"])
                    if task["status"] != "done":
                        raise SystemExit(1)
                    break
                time.sleep(1)


def cmd_mcp(cfg: Config, a) -> None:
    from .inventory import mcp_catalog

    if a.action == "serve":
        logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
        from .mcp_server import serve_stdio

        asyncio.run(serve_stdio(cfg))
        return
    if a.action == "gateway":
        logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
        from .mcp_server import serve_gateway

        servers = [x for x in (a.servers or "").split(",") if x]
        asyncio.run(serve_gateway(cfg, servers, a.task))
        return
    if a.action == "catalog":
        for name, e in mcp_catalog().items():
            plats = ",".join(e.get("platforms") or ["all"])
            print(f"{name:12} [{plats}] {e.get('description', '')}")
            if e.get("setup"):
                print(f"{'':12} setup: {e['setup']}")
        return
    _need_config(cfg)
    if a.action == "add":
        entry = mcp_catalog().get(a.name)
        servers = dict(cfg.user_raw.get("mcp_servers") or {})
        if a.command:
            new = {"command": a.command[0], "args": a.command[1:]}
        elif a.url:
            new = {"url": a.url}
        elif entry and (entry.get("command") or entry.get("url")):
            new = {k: entry[k] for k in ("command", "args", "env", "url", "headers") if k in entry}
        elif entry:
            print(f"{a.name} needs a one-time setup inside the app first:\n  {entry.get('setup')}\n  {entry.get('repo', '')}")
            print(f"\nThen: switchboard mcp add {a.name} --command <cmd> <args...>")
            return
        else:
            raise SystemExit(f"'{a.name}' is not in the catalog; pass --command or --url")
        servers[a.name] = new
        cfg.set_section("mcp_servers", servers)
        print(f"Added MCP server '{a.name}' to {cfg.path}: {json.dumps(new)}")
        if entry and entry.get("setup"):
            print(f"Setup note: {entry['setup']}")
        print("Restart the hub/agent on this machine to load it.")
        return
    if a.action == "remove":
        servers = dict(cfg.user_raw.get("mcp_servers") or {})
        servers.pop(a.name, None)
        cfg.set_section("mcp_servers", servers)
        print(f"Removed {a.name}.")
        return
    if a.action == "list":
        from .mcp_hub import MCPHub

        async def probe():
            hub = MCPHub(cfg)
            tools = await hub.all_tools()
            st = hub.status()
            await hub.close()
            return tools, st

        tools, st = asyncio.run(probe())
        if not st:
            print("No MCP servers configured on this machine. See `switchboard mcp catalog`.")
        for name, s in st.items():
            print(f"{name:14} {s['state']:8} {len(tools.get(name, []))} tools {s['error'] or ''}")
            for t in tools.get(name, [])[:50]:
                print(f"    - {t['name']}: {t['description'][:80]}")
        return
    if a.action == "export":
        from .mcp_export import export

        print(export(cfg, a.target, include_local=a.include_local, write=a.write))


def cmd_service(cfg: Config, a) -> None:
    _need_config(cfg)
    from . import service

    role = a.role or cfg.role
    print(service.install(role) if a.action == "install" else service.uninstall(role))


def cmd_doctor(cfg: Config, a) -> None:
    ok = lambda b: "ok " if b else "-- "  # noqa: E731
    print(f"Tazrivo {__version__}  python {sys.version.split()[0]}  platform {paths.platform_tag()}")
    print(f"{ok(cfg.exists)} config     {cfg.path}")
    if not cfg.exists:
        print("    run: switchboard init")
        return
    print(f"    node {cfg.node_name}  role {cfg.role}  hub {cfg.hub_url}")
    print(f"{ok(bool(cfg.token) and 'PASTE' not in cfg.token)} token      SWITCHBOARD_TOKEN in secrets.env")
    out = cfg.output_dir
    writable = os.access(out, os.W_OK)
    print(f"{ok(writable)} output     {out}")
    up = _hub_up(cfg)
    print(f"{ok(up)} hub        {cfg.hub_url} {'reachable' if up else 'not reachable'}")
    from .providers import build_providers

    print("providers:")
    for name, p in build_providers(cfg).items():
        detail = p.cfg.get("api_key_env") or p.cfg.get("command") or p.cfg.get("base_url", "")
        print(f"  {ok(p.available())}{name:12} {p.kind:14} {detail}")
    from .inventory import detect_tools

    found = [tid for tid, t in detect_tools().items() if t["installed"]]
    print(f"tools found: {', '.join(found) or 'none'}")
    print(f"mcp servers configured here: {', '.join(cfg.mcp_servers) or 'none'}")
    if cfg.notify.get("ntfy_topic"):
        print(f"notifications: ntfy topic {cfg.notify['ntfy_topic']}")
    else:
        print("notifications: off (set notify.ntfy_topic to get alerts on your phone)")


# ---------------------------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tazrivo", description="One control plane for your AI models, credits, tools and machines.")
    p.add_argument("--home", help="config folder (default: per-OS location, or $SWITCHBOARD_HOME)")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create config, credits and secrets files for this machine")
    s.add_argument("--role", choices=["hub", "agent"])
    s.add_argument("--name", help="unique node name (default: hostname)")
    s.add_argument("--hub-url", help="agents: http://<hub-ip>:8765")
    s.add_argument("--token", help="agents: the hub's SWITCHBOARD_TOKEN")
    s.add_argument("--force", action="store_true")

    s = sub.add_parser("setup", help="set this computer up as hub or agent (opens the setup wizard)")
    s.add_argument("--text", action="store_true", help="ask in the terminal instead of the browser")
    s.add_argument("--web", action="store_true", help="serve the wizard to other devices on the network (headless Pi)")
    s.add_argument("--port", type=int, help="port for --web (default 8767)")
    s.add_argument("--role", choices=["hub", "agent"])
    s.add_argument("--name", help="name for this computer (default: its hostname)")
    s.add_argument("--hub", help="agents: hub address, e.g. 192.168.1.50 (default: find it on the network)")
    s.add_argument("--code", help="agents: pairing code from the hub's dashboard (Machines > Connect a computer)")
    s.add_argument("--token", help="agents: the hub's token, instead of a pairing code")
    s.add_argument("--no-start", action="store_true", help="only write the config; don't register start-at-login")

    s=sub.add_parser("launch", help="open Tazrivo: the setup wizard the first time, then the dashboard")
    s.add_argument('--page',help='open a Tazrivo page, e.g. #laith')
    s=sub.add_parser('pet',help='show or hide Laith, your animated desktop companion')
    s.add_argument('action',choices=['show','hide','panel','pause','resume','stop','status','run'],default='show',nargs='?')
    s.add_argument('--panel',action='store_true',help=argparse.SUPPRESS)

    s = sub.add_parser("serve", help="run the hub (dashboard, API, router, scheduler)")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    s.add_argument("--service", action="store_true", help=argparse.SUPPRESS)

    s = sub.add_parser("agent", help="run this machine as an agent of the hub")
    s.add_argument("--service", action="store_true", help=argparse.SUPPRESS)

    s = sub.add_parser("tray", help="tray/menu-bar icon (on agents it also runs the agent)")
    s.add_argument("--service", action="store_true", help=argparse.SUPPRESS)

    for name, help_ in (("ask", "run a task"), ("plan", "show which model/effort/credit a task would use")):
        s = sub.add_parser(name, help=help_)
        s.add_argument("prompt", nargs="*", help="the prompt ('-' or nothing: read stdin)")
        s.add_argument("-p", "--provider")
        s.add_argument("-m", "--model", help="provider:id or id, e.g. claude-opus-5-5")
        s.add_argument("-e", "--effort", choices=["low", "medium", "high", "xhigh", "max"])
        s.add_argument("--mode", choices=["balanced", "best", "cheapest"])
        s.add_argument("--plans-only", dest="billing", action="store_const", const="plans_only",
                       help="never spend API credit: plans' daily limits, free tiers, local models only")
        s.add_argument("--billing", choices=["plans_only", "plans_first", "any"])
        s.add_argument("--tools", help="comma-separated MCP servers, or 'none' (default: auto)")
        s.add_argument("-i", "--image", action="append", help="attach an image (repeatable)")
        s.add_argument("-f", "--file", action="append", help="append a text file to the prompt (repeatable)")
        s.add_argument("--json", action="store_true")
        if name == "ask":
            s.add_argument("-y", "--yes", action="store_true", help="don't ask for confirmation")
            s.add_argument("-q", "--quiet", action="store_true", help="print only the answer")
            s.add_argument("--no-wait", action="store_true")

    s = sub.add_parser("credits", help="show, add, remove credits; refresh live balances")
    s.add_argument("action", nargs="?", choices=["show", "refresh", "add", "remove"], default="show")
    s.add_argument("--id")
    s.add_argument("--provider")
    s.add_argument("--type", choices=["prepaid", "free_tier", "subscription", "payg"], default="prepaid")
    s.add_argument("--amount", type=float, help="USD for prepaid; quota per period for free_tier/subscription")
    s.add_argument("--unit", choices=["usd", "requests", "tokens", "premium_requests"])
    s.add_argument("--expires", help="YYYY-MM-DD or ISO datetime")
    s.add_argument("--renews", help="subscription renewal date")
    s.add_argument("--reset", help="day | week | month | 5h | 1h ...")
    s.add_argument("--tz", default="UTC")
    s.add_argument("--at", default="00:00")
    s.add_argument("--day", type=int, default=1)
    s.add_argument("--weekday", default="mon")
    s.add_argument("--models", help="comma-separated id patterns this source covers, e.g. 'gemini-*flash*'")
    s.add_argument("--budget", type=float, help="payg monthly budget in USD")
    s.add_argument("--label")
    s.add_argument("--auto-balance", action="store_true", help="read the balance live (DeepSeek, OpenRouter)")
    s.add_argument("--json", action="store_true")

    sub.add_parser("alerts", help="credits about to expire, free quotas about to reset, budgets")

    s = sub.add_parser("models", help="list the catalog or sync it with your keys")
    s.add_argument("action", nargs="?", choices=["list", "sync"], default="list")
    s.add_argument("--all", action="store_true")
    s.add_argument("--write", action="store_true")

    s = sub.add_parser("nodes", help="machines, their devices and MCP servers")
    s.add_argument("--refresh", action="store_true")

    s = sub.add_parser("device", help="act on a device: screenshot, logs, install, launch, open_url, check, deploy, run")
    s.add_argument("action")
    s.add_argument("device", help="e.g. android:R58M123, sim:<udid>, server:hostinger")
    s.add_argument("--node", help="machine the device is attached to (default: this one)")
    s.add_argument("--arg", action="append", help="key=value, e.g. path=build/app.apk")

    s = sub.add_parser("tools", help="detect, recommend and install tools")
    s.add_argument("action", choices=["list", "advise", "install"])
    s.add_argument("tool", nargs="?")
    s.add_argument("--node")
    s.add_argument("-y", "--yes", action="store_true")

    s = sub.add_parser("mcp", help="MCP servers: serve Tazrivo, catalog, add/remove/list, export to other apps")
    s.add_argument("action", choices=["serve", "gateway", "catalog", "add", "remove", "list", "export"])
    s.add_argument("--servers", help=argparse.SUPPRESS)
    s.add_argument("--task", help=argparse.SUPPRESS)
    s.add_argument("name", nargs="?")
    s.add_argument("--command", nargs=argparse.REMAINDER, help="custom server command and args (put last)")
    s.add_argument("--url")
    s.add_argument("--target", default="claude-desktop",
                   choices=["claude-desktop", "claude-code", "vscode", "cursor", "codex", "gemini-cli"])
    s.add_argument("--include-local", action="store_true", help="also export this machine's MCP servers")
    s.add_argument("--write", action="store_true", help="merge into the app's config file (keeps a .bak)")

    s = sub.add_parser("service", help="start automatically at login (Task Scheduler / launchd / systemd)")
    s.add_argument("action", choices=["install", "uninstall"])
    s.add_argument("--role", choices=["hub", "agent"])

    sub.add_parser("doctor", help="check this machine's setup")
    return p


COMMANDS = {"init": cmd_init, "setup": cmd_setup, "launch": cmd_launch, "pet": cmd_pet, "serve": cmd_serve, "agent": cmd_agent, "tray": cmd_tray, "ask": cmd_ask, "plan": cmd_plan,
            "credits": cmd_credits, "alerts": cmd_alerts, "models": cmd_models, "nodes": cmd_nodes,
            "device": cmd_device, "tools": cmd_tools, "mcp": cmd_mcp, "service": cmd_service, "doctor": cmd_doctor}


def main(argv: list[str] | None = None) -> None:
    from .runtime import refresh_path

    refresh_path()
    if argv is None:
        argv = sys.argv[1:]
    # Opening the packaged app (double-click, Start menu) passes no arguments, or a macOS -psn_ one.
    argv = [x for x in argv if not x.startswith("-psn_")]
    if paths.is_frozen() and not argv:
        argv = ["launch"]
    args = build_parser().parse_args(argv)
    if args.home:
        os.environ["SWITCHBOARD_HOME"] = str(Path(args.home).expanduser())
    cfg = Config()
    COMMANDS[args.cmd](cfg, args)


if __name__ == "__main__":
    main()
