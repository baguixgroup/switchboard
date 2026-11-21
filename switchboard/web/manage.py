"""Dashboard management endpoints, so nothing needs the terminal after installation:
API keys, preferences, permissions, notifications, MCP servers per machine, model sync,
plan sign-in, connecting new machines, task files, and a compact summary for the tray icon."""
from __future__ import annotations

import shlex
import socket
import time
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import notify
from ..config import write_env_value
from ..inventory import mcp_catalog
from ..outputs import safe_join

KEY_INFO: dict[str, tuple[str, str]] = {
    "ANTHROPIC_API_KEY": ("Anthropic (Claude API)", "https://console.anthropic.com/settings/keys"),
    "OPENAI_API_KEY": ("OpenAI", "https://platform.openai.com/api-keys"),
    "DEEPSEEK_API_KEY": ("DeepSeek", "https://platform.deepseek.com/api_keys"),
    "XAI_API_KEY": ("xAI (Grok API)", "https://console.x.ai"),
    "GEMINI_API_KEY": ("Google Gemini API", "https://aistudio.google.com/apikey"),
    "GITHUB_TOKEN": ("GitHub Models (free daily allowance)", "https://github.com/settings/personal-access-tokens"),
    "OPENROUTER_API_KEY": ("OpenRouter", "https://openrouter.ai/settings/keys"),
    "MISTRAL_API_KEY": ("Mistral", "https://console.mistral.ai/api-keys"),
    "HOSTINGER_API_TOKEN": ("Hostinger (for the Hostinger app)", "https://hpanel.hostinger.com/profile/api"),
    "NTFY_TOKEN": ("ntfy (only for a protected topic)", "https://docs.ntfy.sh"),
}
SETTINGS_SCHEMA: dict[str, dict[str, type | tuple]] = {
    "router": {"billing": ("plans_only", "plans_first", "any"), "mode": ("balanced", "best", "cheapest"),
               "classifier": ("heuristic", "llm"), "auto_downgrade_effort": bool, "confirm_above_usd": float,
               "failover": bool},
    "permissions": {"device_actions": ("none", "read_only", "all"), "server_actions": ("none", "read_only", "all"),
                    "install_tools": bool, "cli_agents_can_edit": bool},
    "notify": {"ntfy_topic": str, "ntfy_server": str, "check_every_minutes": int},
}
LOGIN_HINTS = {
    "gemini-cli": "Gemini CLI signs in on its first run: open a terminal on {where}, type gemini, and choose "
                  "'Sign in with Google'.",
}


class KeyIn(BaseModel):
    name: str
    value: str


class SettingsIn(BaseModel):
    router: dict | None = None
    permissions: dict | None = None
    notify: dict | None = None


class McpAddIn(BaseModel):
    node: str
    name: str
    command: str | None = None  # full command line, e.g. "uvx blender-mcp"
    url: str | None = None
    env: dict[str, str] = Field(default_factory=dict)


class SyncIn(BaseModel):
    write: bool = False


class LoginIn(BaseModel):
    node: str | None = None


class FileIn(BaseModel):
    name: str
    b64: str


class OpenIn(BaseModel):
    node: str


class WritingResultIn(BaseModel):
    text: str = Field(min_length=1, max_length=200_000)


def _lan_addresses() -> list[str]:
    ips: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # no packets are sent; picks the LAN interface
            ips.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except OSError:
        pass
    return ips


def _remove_env_value(path, key: str) -> None:
    if not path.exists():
        return
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.split("=", 1)[0].strip() != key]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _clean_settings(section: str, values: dict) -> dict:
    schema = SETTINGS_SCHEMA[section]
    out: dict[str, Any] = {}
    for k, v in values.items():
        rule = schema.get(k)
        if rule is None:
            raise HTTPException(400, f"unknown setting {section}.{k}")
        if isinstance(rule, tuple):
            if v not in rule:
                raise HTTPException(400, f"{section}.{k} must be one of {rule}")
            out[k] = v
        elif rule is bool:
            out[k] = bool(v)
        else:
            try:
                out[k] = rule(v)
            except (TypeError, ValueError):
                raise HTTPException(400, f"{section}.{k} must be a {rule.__name__}")
    return out


def register(app: FastAPI, engine, auth) -> None:
    cfg = engine.config
    dep = [Depends(auth)]

    # ---- API keys (values are never sent back) ---------------------------------------------
    def key_rows() -> list[dict]:
        used: dict[str, list[str]] = {}
        for name, p in (cfg.providers or {}).items():
            env = (p or {}).get("api_key_env")
            if env and not (p or {}).get("disabled"):
                used.setdefault(env, []).append(name)
        for service in cfg.raw.get('capability_services',[]):
            env=service.get('secret_env')
            if env:used.setdefault(env,[]).append(service.get('id','service'))
        names = list(dict.fromkeys(list(used) + list(KEY_INFO)))
        return [{"name": n, "label": KEY_INFO.get(n, (n, ""))[0], "url": KEY_INFO.get(n, ("", ""))[1],
                 "set": bool(cfg.secret(n)), "used_by": used.get(n, [])} for n in names]

    @app.get("/api/keys", dependencies=dep)
    async def keys() -> list[dict]:
        return key_rows()

    @app.put("/api/keys", dependencies=dep)
    async def set_key(body: KeyIn) -> dict:
        allowed = {r["name"] for r in key_rows()}
        value = body.value.strip()
        if body.name not in allowed:
            raise HTTPException(400, "unknown key name")
        if not value or "\n" in value:
            raise HTTPException(400, "empty or invalid value")
        write_env_value(cfg.home / "secrets.env", body.name, value)
        cfg.secrets[body.name] = value
        engine.available_providers(refresh=True)
        return {"ok": True}

    @app.delete("/api/keys/{name}", dependencies=dep)
    async def delete_key(name: str) -> dict:
        if name not in {r["name"] for r in key_rows()}:
            raise HTTPException(400, "unknown key name")
        _remove_env_value(cfg.home / "secrets.env", name)
        cfg.secrets.pop(name, None)
        engine.available_providers(refresh=True)
        return {"ok": True}

    # ---- preferences, permissions, notifications ------------------------------------------
    @app.get("/api/settings", dependencies=dep)
    async def get_settings() -> dict:
        return {
            "router": {k: cfg.router.get(k) for k in SETTINGS_SCHEMA["router"]},
            "permissions": {k: cfg.permissions.get(k) for k in SETTINGS_SCHEMA["permissions"]},
            "notify": {k: cfg.notify.get(k) for k in SETTINGS_SCHEMA["notify"]},
            "node": {"name": cfg.node_name, "role": cfg.role}, "output_dir": str(cfg.output_dir),
        }

    @app.put("/api/settings", dependencies=dep)
    async def put_settings(body: SettingsIn) -> dict:
        for section in ("router", "permissions", "notify"):
            values = getattr(body, section)
            if values:
                merged = dict(cfg.user_raw.get(section) or {})
                merged.update(_clean_settings(section, values))
                cfg.set_section(section, merged)
        engine.router.cfg = cfg.router
        return await get_settings()

    @app.post("/api/notify/test", dependencies=dep)
    async def notify_test() -> dict:
        if not cfg.notify.get("ntfy_topic"):
            raise HTTPException(400, "Set a notification topic first.")
        ok = await notify.send(cfg, "Switchboard test", "Notifications are working.", "info")
        return {"ok": ok}

    # ---- connecting machines ----------------------------------------------------------------
    @app.get("/api/connect", dependencies=dep)
    async def connect() -> dict:
        port = int(cfg.server.get("port", 8765))
        urls = [f"http://{ip}:{port}" for ip in _lan_addresses()] or [f"http://<this-machine-ip>:{port}"]
        hub = urls[0]
        token = cfg.token
        from .pairing import available_downloads

        return {
            "hub_urls": urls, "token": token, "downloads": available_downloads(cfg),
            "releases_url": cfg.server.get("downloads_url", ""),
            "steps": {
                "windows": [
                    "Download the project (git clone or ZIP) and open PowerShell in its folder.",
                    f"powershell -ExecutionPolicy Bypass -File scripts\\install-windows.ps1 -Role agent -HubUrl {hub} -Token {token}",
                ],
                "macos": [
                    "Download the project (git clone or ZIP) and open Terminal in its folder.",
                    f"./scripts/install-macos.sh agent {hub} {token}",
                ],
                "pi": [
                    "Download the project (git clone or ZIP) and open a terminal in its folder.",
                    f"./scripts/install-pi.sh agent {hub} {token}",
                ],
            },
        }

    # ---- models -----------------------------------------------------------------------------
    @app.post("/api/models/sync", dependencies=dep)
    async def models_sync(body: SyncIn) -> dict:
        from ..models_sync import sync

        report = await sync(engine, write=body.write)
        if body.write:
            engine.reload_catalog()
        return report

    # ---- MCP servers on every machine ---------------------------------------------------------
    @app.get("/api/mcp/overview", dependencies=dep)
    async def mcp_overview() -> dict:
        catalog = []
        for name, e in mcp_catalog().items():
            catalog.append({"id": name, "description": e.get("description", ""), "platforms": e.get("platforms"),
                            "setup": e.get("setup"), "repo": e.get("repo"), "requires": e.get("requires", []),
                            "needs_setup": not (e.get("command") or e.get("url")),
                            "command": " ".join([e["command"], *map(str, e.get("args", []))]) if e.get("command") else None,
                            "env": list((e.get("env") or {}).keys())})
        servers, nodes = [], []
        for n in engine.nodes.list():
            nodes.append({"node": n["node"], "platform": n.get("platform"), "online": n["online"]})
            for s, st in (n.get("mcp") or {}).items():
                servers.append({"node": n["node"], "name": s, "state": st.get("state"), "tools": st.get("tools", 0),
                                "error": st.get("error"), "transport": st.get("transport"),
                                "tool_names": [t["name"] for t in st.get("tool_list", [])][:40]})
        return {"catalog": catalog, "servers": servers, "nodes": nodes}

    async def node_job(node: str, kind: str, payload: dict, timeout: float = 180) -> dict:
        try:
            inv = await engine.nodes.submit(node, kind, payload, timeout=timeout)
        except Exception as exc:
            raise HTTPException(502, f"{node}: {exc}")
        if isinstance(inv, dict) and inv.get("node"):
            engine.nodes.heartbeat(inv)
        return inv

    @app.post("/api/mcp/servers", dependencies=dep)
    async def mcp_add(body: McpAddIn) -> dict:
        entry = (mcp_catalog().get(body.name) or {})
        if body.command:
            parts = shlex.split(body.command, posix=True)
            new = {"command": parts[0], "args": parts[1:]}
        elif body.url:
            new = {"url": body.url}
        elif entry.get("command") or entry.get("url"):
            new = {k: entry[k] for k in ("command", "args", "env", "url", "headers") if k in entry}
        else:
            raise HTTPException(400, "This app needs a one-time setup first; then paste its server command.")
        if body.env:
            new["env"] = dict(new.get("env") or {}, **body.env)
        if not body.name.replace("-", "").replace("_", "").isalnum():
            raise HTTPException(400, "name may only contain letters, digits, - and _")
        inv = await node_job(body.node, "mcp_add", {"name": body.name, "entry": new})
        st = (inv.get("mcp") or {}).get(body.name, {})
        return {"ok": st.get("state") != "error", "state": st.get("state"), "error": st.get("error"),
                "tools": st.get("tools", 0)}

    @app.delete("/api/mcp/servers/{node}/{name}", dependencies=dep)
    async def mcp_remove(node: str, name: str) -> dict:
        await node_job(node, "mcp_remove", {"name": name})
        return {"ok": True}

    @app.post("/api/mcp/servers/{node}/{name}/restart", dependencies=dep)
    async def mcp_restart(node: str, name: str) -> dict:
        inv = await node_job(node, "mcp_restart", {"name": name})
        st = (inv.get("mcp") or {}).get(name, {})
        return {"ok": st.get("state") != "error", "state": st.get("state"), "error": st.get("error")}

    # ---- plan sign-in ----------------------------------------------------------------------------
    @app.post("/api/plans/{provider}/login", dependencies=dep)
    async def plan_login(provider: str, body: LoginIn) -> dict:
        p = engine.providers.get(provider)
        if not p or p.kind != "cli":
            raise HTTPException(404, "not a plan app")
        node = body.node or engine.cli_location(provider)
        if not node:
            raise HTTPException(400, "Install this plan app on one of your machines first.")
        if not p.cfg.get("login_args"):
            hint = LOGIN_HINTS.get(provider, "Open a terminal on {where} and run the app once to sign in.")
            return {"ok": False, "manual": True, "message": hint.format(where=node)}
        try:
            res = await engine.nodes.submit(node, "cli_login", {"provider": provider, "cfg": p.cfg}, timeout=60)
        except Exception as exc:
            raise HTTPException(502, f"{node}: {exc}")
        return dict(res, node=node)

    # ---- task files --------------------------------------------------------------------------------
    def task_dir(task_id: str):
        t = engine.store.get_task(task_id)
        if not t or not t.get("output_path"):
            raise HTTPException(404, "no output for this task")
        from pathlib import Path
        return Path(t["output_path"])

    @app.get("/api/tasks/{task_id}/files", dependencies=dep)
    async def task_files(task_id: str) -> list[dict]:
        d = task_dir(task_id)
        if not d.exists():
            return []
        out = []
        for f in sorted(d.rglob("*")):
            if f.is_file() and not f.name.startswith(".") and f.name not in ("meta.json",):
                rel = f.relative_to(d).as_posix()
                if rel.startswith(".gemini/"):
                    continue
                kind = "image" if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp") else "file"
                out.append({"path": rel, "size": f.stat().st_size, "kind": kind})
        return out

    @app.post("/api/tasks/{task_id}/files", dependencies=dep)
    async def add_task_file(task_id: str, body: FileIn) -> dict:
        import base64 as b64mod

        try:
            data = b64mod.b64decode(body.b64.split(",", 1)[-1], validate=False)
        except ValueError:
            raise HTTPException(400, "file data is not valid")
        if len(data) > 25_000_000:
            raise HTTPException(400, "Files up to 25 MB can be added.")
        try:
            return engine.attach_file(task_id, body.name, data)
        except ValueError as exc:
            raise HTTPException(404, str(exc))

    @app.post("/api/tasks/{task_id}/handoff/open", dependencies=dep)
    async def handoff_open(task_id: str, body: OpenIn) -> dict:
        try:
            return await engine.handoff_open(task_id, body.node)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        except Exception as exc:
            raise HTTPException(502, f"{body.node}: {exc}")

    @app.post("/api/tasks/{task_id}/handoff/text", dependencies=dep)
    async def writing_result(task_id: str, body: WritingResultIn) -> dict:
        try:
            return engine.complete_writing(task_id, body.text)
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @app.get("/api/tasks/{task_id}/file", dependencies=dep)
    async def task_file(task_id: str, path: str):
        d = task_dir(task_id)
        try:
            f = safe_join(d, path)
        except ValueError:
            raise HTTPException(400, "bad path")
        if not f.is_file():
            raise HTTPException(404, "not found")
        return FileResponse(f, filename=f.name)

    # ---- compact summary for the tray icon -------------------------------------------------------------
    @app.get("/api/summary", dependencies=dep)
    async def summary() -> dict:
        engine.ledger.reload()
        alerts = engine.ledger.alerts()
        nodes = engine.nodes.list()
        counts = {s: sum(1 for a in alerts if a.severity == s) for s in ("critical", "warning", "info")}
        return {"hub": cfg.node_name, "nodes_online": sum(1 for n in nodes if n["online"]), "nodes_total": len(nodes),
                "alerts": [{"severity": a.severity, "title": a.title, "key": a.key} for a in alerts[:10]],
                "counts": counts, "plans": engine.plans_status(), "running_tasks": len(engine.running),
                "time": time.time()}
