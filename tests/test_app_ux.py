"""Dashboard management API, local relay security, installable-app files, tray logic, start-at-login choices."""
import asyncio
import sys

import pytest
import uvicorn
import yaml
from fastapi.testclient import TestClient

from switchboard import service, tray
from switchboard.agent import run_agent
from switchboard.config import Config
from switchboard.engine import Engine
from switchboard.localproxy import build_proxy_app
from switchboard.web.app import create_app

from .conftest import free_port, make_home

H = {"Authorization": "Bearer test-token"}
REMOTE = ("203.0.113.9", 5000)


def remote_client(cfg):
    return TestClient(create_app(Engine(cfg)), client=REMOTE, headers=H)


def test_api_keys_are_saved_but_never_returned(tmp_path):
    cfg = Config(make_home(tmp_path / "h", providers={"mock": {"kind": "mock"},
                                                      "openai": {"kind": "openai_compat", "api_key_env": "OPENAI_API_KEY"}}))
    with remote_client(cfg) as c:
        rows = {r["name"]: r for r in c.get("/api/keys").json()}
        assert rows["OPENAI_API_KEY"]["set"] is False and "openai" in rows["OPENAI_API_KEY"]["used_by"]
        assert c.put("/api/keys", json={"name": "OPENAI_API_KEY", "value": "sk-secret-123"}).status_code == 200
        body = c.get("/api/keys").text
        assert "sk-secret-123" not in body and '"set":true' in body.replace(" ", "")
        assert "OPENAI_API_KEY=sk-secret-123" in (tmp_path / "h" / "secrets.env").read_text()
        assert "openai" in {p["name"] for p in c.get("/api/status").json()["providers"] if p["available"]}
        assert c.put("/api/keys", json={"name": "PATH", "value": "x"}).status_code == 400
        assert c.put("/api/keys", json={"name": "OPENAI_API_KEY", "value": "a\nb"}).status_code == 400
        assert c.delete("/api/keys/OPENAI_API_KEY").status_code == 200
        assert "OPENAI_API_KEY" not in (tmp_path / "h" / "secrets.env").read_text()


def test_settings_validate_and_persist(tmp_path):
    cfg = Config(make_home(tmp_path / "h"))
    with remote_client(cfg) as c:
        r = c.put("/api/settings", json={"router": {"billing": "plans_only", "confirm_above_usd": "1.25"},
                                         "permissions": {"cli_agents_can_edit": True}})
        assert r.status_code == 200 and r.json()["router"]["billing"] == "plans_only"
        saved = yaml.safe_load((tmp_path / "h" / "config.yaml").read_text())
        assert saved["router"]["billing"] == "plans_only" and saved["router"]["confirm_above_usd"] == 1.25
        assert saved["permissions"]["cli_agents_can_edit"] is True
        assert c.put("/api/settings", json={"router": {"billing": "free-for-all"}}).status_code == 400
        assert c.put("/api/settings", json={"router": {"rm -rf": 1}}).status_code == 400
        assert c.post("/api/notify/test").status_code == 400  # no topic yet
        conn = c.get("/api/connect").json()
        assert conn["token"] == "test-token" and "install-windows.ps1" in conn["steps"]["windows"][1]


def test_task_files_and_traversal(tmp_path):
    cfg = Config(make_home(tmp_path / "h"))
    with remote_client(cfg) as c:
        tid = c.post("/api/tasks", json={"prompt": "show me some code please"}).json()["task_id"]
        for _ in range(50):
            if c.get(f"/api/tasks/{tid}").json()["status"] == "done":
                break
            asyncio.run(asyncio.sleep(0.1))
        files = {f["path"] for f in c.get(f"/api/tasks/{tid}/files").json()}
        assert "files/hello.py" in files
        assert "hello from switchboard" in c.get(f"/api/tasks/{tid}/file", params={"path": "files/hello.py"}).text
        assert c.get(f"/api/tasks/{tid}/file", params={"path": "../../secrets.env"}).status_code == 400


def test_static_and_installable_app_files(tmp_path):
    cfg = Config(make_home(tmp_path / "h"))
    with remote_client(cfg) as c:
        assert "manifest" in c.get("/").text
        m = c.get("/manifest.webmanifest")
        assert m.status_code == 200 and m.json()["display"] == "standalone"
        sw = c.get("/sw.js")
        assert sw.status_code == 200 and sw.headers["service-worker-allowed"] == "/"
        assert c.get("/static/app.js").status_code == 200 and c.get("/static/icon-512.png").headers["content-type"] == "image/png"
        assert c.get("/static/..%2Fapp.py").status_code == 404


def test_hub_blocks_cross_site_requests_from_localhost(tmp_path):
    cfg = Config(make_home(tmp_path / "h"))
    with TestClient(create_app(Engine(cfg)), client=("127.0.0.1", 1234)) as c:
        assert c.get("/api/status").status_code == 200  # same machine, no token needed
        assert c.post("/api/plan", json={"prompt": "hi"}, headers={"Origin": "https://evil.example"}).status_code == 403
        assert c.get("/api/status", headers={"Host": "evil.example"}).status_code == 403  # DNS rebinding


def test_local_relay_refuses_other_sites(tmp_path):
    cfg = Config(make_home(tmp_path / "a", role="agent", name="pc", hub_url=f"http://127.0.0.1:{free_port()}"))
    with TestClient(build_proxy_app(cfg)) as c:
        assert c.get("/api/status", headers={"Origin": "https://evil.example"}).status_code == 403
        assert c.get("/api/status", headers={"Host": "evil.example"}).status_code == 403
        assert c.get("/api/agent/jobs?node=pc").status_code == 404
        r = c.get("/api/status")  # hub not running in this test
        assert r.status_code == 502 and "not reachable" in r.json()["detail"]
        assert "Can't reach your Tazrivo hub" in c.get("/").text


async def test_add_restart_remove_app_on_another_machine(tmp_path, echo_server_cfg):
    port = free_port()
    hub = Config(make_home(tmp_path / "hub", name="pi", port=port))
    agent = Config(make_home(tmp_path / "agent", role="agent", name="win-pc", hub_url=f"http://127.0.0.1:{port}"))
    engine = Engine(hub)
    server = uvicorn.Server(uvicorn.Config(create_app(engine), host="127.0.0.1", port=port, log_config=None))
    st = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    at = asyncio.create_task(run_agent(agent))
    try:
        for _ in range(100):
            if engine.nodes.online("win-pc") and "win-pc" in engine.nodes.nodes:
                break
            await asyncio.sleep(0.2)
        import httpx

        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", headers=H, timeout=60) as c:
            cmd = f'"{sys.executable}" "{echo_server_cfg["args"][0]}"'
            r = (await c.post("/api/mcp/servers", json={"node": "win-pc", "name": "echo", "command": cmd})).json()
            assert r["ok"] and r["tools"] == 2
            assert "echo" in yaml.safe_load((tmp_path / "agent" / "config.yaml").read_text())["mcp_servers"]
            assert engine.nodes.mcp_locations().get("echo") == ["win-pc"]
            ov = (await c.get("/api/mcp/overview")).json()
            assert any(s["name"] == "echo" and s["node"] == "win-pc" for s in ov["servers"])
            assert (await c.post("/api/mcp/servers/win-pc/echo/restart")).json()["ok"]
            assert (await c.delete("/api/mcp/servers/win-pc/echo")).status_code == 200
            assert "echo" not in (yaml.safe_load((tmp_path / "agent" / "config.yaml").read_text()).get("mcp_servers") or {})
            bad = await c.post("/api/mcp/servers", json={"node": "win-pc", "name": "unity"})
            assert bad.status_code == 400 and "one-time setup" in bad.json()["detail"]
    finally:
        at.cancel()
        server.should_exit = True
        await asyncio.gather(at, st, return_exceptions=True)


def test_tray_state_and_icon():
    s = tray.TrayState()
    assert s.status == "off" and "not reachable" in s.tooltip
    s.summary = {"hub": "pi", "nodes_online": 2, "nodes_total": 3, "running_tasks": 1,
                 "counts": {"critical": 1, "warning": 0}, "alerts": [{"key": "a", "severity": "critical", "title": "Use $5"}],
                 "plans": [{"label": "Claude", "status": "limit reached", "resets": 1900000000}, {"label": "Codex", "status": "ready"}]}
    assert s.status == "crit" and s.tooltip == "Tazrivo: 1 alert" and "2/3 machines" in s.status_line
    assert s.new_alerts() == []  # nothing announced right after start-up
    s.summary["alerts"].append({"key": "b", "severity": "warning", "title": "Free limit resets soon"})
    assert [a["key"] for a in s.new_alerts()] == ["b"] and s.new_alerts() == []
    assert any("resets" in line for line in s.plan_lines()) and "Codex: ready" in s.plan_lines()
    img = tray.make_icon(64, "ok")
    assert img.size == (64, 64) and img.getpixel((32, 4))[3] > 0


@pytest.mark.parametrize("fam,has_tray,role,expected", [
    ("windows", True, "agent", ["tray"]), ("macos", True, "hub", ["hub", "tray"]),
    ("windows", False, "agent", ["agent"]), ("linux", True, "agent", ["agent"]), ("linux", True, "hub", ["hub"]),
])
def test_start_at_login_plan(monkeypatch, fam, has_tray, role, expected):
    monkeypatch.setattr(service, "os_family", lambda: fam)
    monkeypatch.setattr(service, "tray_available", lambda: has_tray)
    assert service._plan(role) == expected
