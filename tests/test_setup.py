"""Setup wizard, pairing codes, LAN discovery, and launching from the packaged app."""
import asyncio
import socket
import sys

import pytest
import uvicorn
from fastapi.testclient import TestClient

from switchboard import paths, setup
from switchboard.config import Config, read_env_file
from switchboard.discovery import find_hubs, start_responder
from switchboard.engine import Engine
from switchboard.setup_wizard import build_app
from switchboard.web.app import create_app
from switchboard.web.pairing import MAX_FAILS, Pairing

from .conftest import free_port, make_home

H = {"Authorization": "Bearer test-token"}
REMOTE = ("203.0.113.9", 5000)


# ---- pairing codes ----------------------------------------------------------------------------
def test_pairing_code_is_single_use_and_expires():
    now = [1000.0]
    p = Pairing(now=lambda: now[0])
    code = p.new_code()["code"]
    assert len(code) == 6 and code.isdigit()
    assert p.redeem(code[:3] + " " + code[3:])  # spaces are ignored
    assert not p.redeem(code)  # used up
    code = p.new_code()["code"]
    now[0] += 601
    assert not p.redeem(code) and p.current()["code"] is None


def test_pairing_code_cancelled_after_wrong_guesses():
    p = Pairing()
    code = p.new_code()["code"]
    wrong = f"{(int(code) + 1) % 1_000_000:06d}"
    for _ in range(MAX_FAILS):
        assert not p.redeem(wrong)
    assert not p.redeem(code)  # the right code no longer works either


def test_pair_endpoint_hands_out_token_only_for_a_valid_code(tmp_path):
    cfg = Config(make_home(tmp_path / "hub"))
    with TestClient(create_app(Engine(cfg)), client=REMOTE) as anon:
        assert anon.post("/api/pair/code").status_code == 401  # making a code needs the token
        assert anon.post("/api/pair", json={"code": "123456"}).status_code == 403
        code = anon.post("/api/pair/code", headers=H).json()["code"]
        r = anon.post("/api/pair", json={"code": code, "name": "mac"})
        assert r.status_code == 200 and r.json()["token"] == "test-token"
        assert anon.post("/api/pair", json={"code": code}).status_code == 403


def test_connect_lists_installers_the_hub_can_serve(tmp_path):
    cfg = Config(make_home(tmp_path / "hub"))
    d = cfg.home / "downloads"
    d.mkdir()
    (d / "Switchboard-0.3.0-arm64.deb").write_bytes(b"deb")
    (d / "Switchboard-0.3.0-Setup.exe").write_bytes(b"exe")
    (d / "notes.txt").write_text("x")
    with TestClient(create_app(Engine(cfg)), client=REMOTE, headers=H) as c:
        dl = c.get("/api/connect").json()["downloads"]
        assert dl["pi"]["name"].endswith(".deb") and dl["windows"]["name"].endswith(".exe") and "macos" not in dl
        anon = TestClient(c.app, client=REMOTE)
        assert anon.get(dl["windows"]["url"]).content == b"exe"  # no token needed to fetch the app
        assert anon.get("/downloads/notes.txt").status_code == 404
        assert anon.get("/downloads/..%2Fsecrets.env").status_code == 404


# ---- discovery ----------------------------------------------------------------------------------
async def test_hub_answers_discovery_broadcast():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        udp = s.getsockname()[1]
    transport = await start_responder("pi", 8765, udp_port=udp)
    assert transport is not None
    try:
        hubs = await asyncio.to_thread(find_hubs, 0.6, udp, ["127.0.0.1"])
    finally:
        transport.close()
    assert hubs == [{"name": "pi", "url": "http://127.0.0.1:8765", "version": hubs[0]["version"]}]


# ---- writing the config -----------------------------------------------------------------------
def test_write_config_keeps_keys_and_switches_role(tmp_path):
    home = tmp_path / "h"
    res = setup.write_config("hub", "pi", home=home)
    token = res["token"]
    assert len(token) > 30 and Config(home).role == "hub"
    sec = home / "secrets.env"
    sec.write_text(sec.read_text() + "OPENAI_API_KEY=sk-test\n")
    setup.write_config("hub", "pi", home=home)  # rerun: same token
    assert read_env_file(sec)["SWITCHBOARD_TOKEN"] == token
    setup.write_config("agent", "mac", "http://10.0.0.17:8765", "hubtoken", home=home)
    cfg = Config(home)
    assert (cfg.role, cfg.node_name, cfg.hub_url, cfg.token) == ("agent", "mac", "http://10.0.0.17:8765", "hubtoken")
    assert read_env_file(sec)["OPENAI_API_KEY"] == "sk-test"
    assert "# Tazrivo node configuration" in cfg.path.read_text()  # comments survive


@pytest.mark.parametrize("raw,url", [("10.0.0.17", "http://10.0.0.17:8765"), ("pi.local:9000/", "http://pi.local:9000"),
                                     ("https://hub.example.com", "https://hub.example.com:8765")])
def test_clean_hub_url(raw, url):
    assert setup.clean_hub_url(raw) == url


def test_clean_hub_url_rejects_junk():
    with pytest.raises(setup.SetupError):
        setup.clean_hub_url("not a url/with spaces")


async def test_agent_setup_with_pairing_code_against_a_real_hub(tmp_path):
    port = free_port()
    hub_cfg = Config(make_home(tmp_path / "hub", name="pi", port=port))
    app = create_app(Engine(hub_cfg))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    try:
        code = app.state.pairing.new_code()["code"]
        home = tmp_path / "mac"
        with pytest.raises(setup.SetupError, match="wrong or has expired"):
            await asyncio.to_thread(setup.apply, "agent", "Mac Book!", f"127.0.0.1:{port}", None, "000000"
                                    if code != "000000" else "111111", home, False)
        res = await asyncio.to_thread(setup.apply, "agent", "Mac Book!", f"127.0.0.1:{port}", None, code, home, False)
        assert res["name"] == "mac-book" and "token" not in res
        cfg = Config(home)
        assert cfg.token == "test-token" and cfg.hub_url == f"http://127.0.0.1:{port}"
        assert res["dashboard_url"] == "http://localhost:8766"
    finally:
        server.should_exit = True
        await task


# ---- the wizard's own web server ---------------------------------------------------------------
def test_wizard_needs_its_key_and_rejects_other_hosts(tmp_path):
    app = build_app("secret-key", home=tmp_path / "h", start_services=False)
    with TestClient(app, base_url="http://localhost") as c:
        assert c.get("/").status_code == 403
        assert "Set up Tazrivo" in c.get("/?k=secret-key").text
        assert c.get("/api/info").status_code == 403
        info = c.get("/api/info", headers={"X-Setup-Key": "secret-key"}).json()
        assert info["current"] == {"configured": False}
        r = c.post("/api/finish", json={"role": "hub", "name": "Pi"}, headers={"X-Setup-Key": "secret-key"})
        assert r.status_code == 200 and r.json()["role"] == "hub" and r.json()["name"] == "pi"
        assert Config(tmp_path / "h").role == "hub"
        bad = c.post("/api/finish", json={"role": "agent", "hub_url": ""}, headers={"X-Setup-Key": "secret-key"})
        assert bad.status_code == 400 and "address" in bad.json()["detail"]
    with TestClient(app, base_url="http://evil.example") as c:  # DNS rebinding
        assert c.get("/?k=secret-key").status_code == 403


# ---- packaged app --------------------------------------------------------------------------------------
def test_self_command_from_source_and_frozen(monkeypatch, tmp_path):
    assert paths.self_command("agent") == [sys.executable, "-m", "switchboard", "agent"]
    exe = tmp_path / ("switchboard-cli.exe" if sys.platform.startswith("win") else "Switchboard")
    exe.write_text("")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert paths.self_command("tray", "--service", gui=True)[1:] == ["tray", "--service"]
    assert paths.self_command("mcp", "serve")[0].endswith(exe.name)


def test_frozen_app_with_no_arguments_launches(monkeypatch):
    from switchboard import cli

    seen = {}
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setitem(cli.COMMANDS, "launch", lambda cfg, a: seen.setdefault("cmd", a.cmd))
    cli.main(["-psn_0_12345"])
    assert seen["cmd"] == "launch"
