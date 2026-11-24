"""Synthetic local roles, no external execution or production configuration."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from switchboard.config import Config
from switchboard.engine import Engine
from switchboard.security import (
    LocalSecurity, OutboundIntent, Principal, SecurityDenied, outbound_decision,
)
from switchboard.web.app import create_app
from .conftest import make_home

PARENT = "synthetic-parent-bootstrap-credential-0123456789abcdef"


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.delenv("SWITCHBOARD_PARENT_TOKEN", raising=False)
    cfg = Config(make_home(tmp_path / "protected"))
    cfg.raw["security"] = {"enabled": True}
    cfg.secrets["SWITCHBOARD_PARENT_TOKEN"] = PARENT
    e = Engine(cfg)
    yield e
    e.store._db.close()


def headers(token=PARENT):
    return {"Authorization": "Bearer " + token}


def session(engine, role="child", **kwargs):
    return engine.security.issue(engine.security.authenticate("Bearer " + PARENT),
                                 role=role, subject="profile-1", **kwargs)


@pytest.mark.parametrize("peer", ["127.0.0.1", "203.0.113.9"])
async def test_no_loopback_shared_cookie_query_or_invalid_credential_authority(engine, peer):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(engine), client=(peer, 5000)), base_url="http://localhost") as client:
        for kwargs in ({}, {"headers": headers("invalid")}, {"headers": headers("test-token")},
                       {"headers": {b"authorization": b"Bearer \xff"}},
                       {"headers": {"Cookie": "sb_token=" + PARENT}}, {"params": {"token": PARENT}}):
            assert (await client.get("/api/settings", **kwargs)).status_code == 401
        assert (await client.get("/api/settings", headers=headers())).status_code == 200
        for origin in ("null", "https://foreign.example", "http://localhost:9999"):
            assert (await client.get("/api/settings", headers=headers() | {"Origin": origin})).status_code == 403
        assert (await client.get("/api/settings", headers=headers() | {"Origin": "http://localhost"})).status_code == 200


def test_protected_startup_does_not_recover_or_start_legacy_work(engine, monkeypatch):
    def forbidden(*a, **kw):
        pytest.fail("Protected startup must not recover legacy work")
    monkeypatch.setattr(engine.capabilities, "recover", forbidden)
    monkeypatch.setattr(engine.studio, "recover", forbidden)
    with TestClient(create_app(engine)) as client:
        assert client.get("/api/security/me", headers=headers()).json()["role"] == "parent"
        assert client.get("/docs").status_code == 404


async def test_parent_issues_and_revokes_sessions_without_returning_stored_tokens(engine):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(engine)), base_url="http://localhost") as c:
        issued = await c.post("/api/security/sessions", headers=headers(), json={"role": "child", "subject": "child-opaque"})
        assert issued.status_code == 201
        child = issued.json()
        assert issued.headers["cache-control"] == "no-store"
        assert (await c.get("/api/security/me", headers=headers(child["token"]))).json()["role"] == "child"
        listed = await c.get("/api/security/sessions", headers=headers())
        assert child["token"] not in listed.text and "token_hash" not in listed.text
        assert listed.json()[0]["subject"] == "child-opaque"
        assert (await c.delete("/api/security/sessions/" + child["id"], headers=headers())).status_code == 200
        assert (await c.get("/api/security/me", headers=headers(child["token"]))).status_code == 401


@pytest.mark.parametrize("role,kwargs", [("child", {}), ("device", {"device": "device-1"})])
async def test_child_and_device_cannot_escalate_read_legacy_content_or_execute(engine, role, kwargs):
    token = session(engine, role, **kwargs)["token"]
    cases = [("GET", "/api/settings"), ("GET", "/api/tasks"), ("GET", "/api/connect"),
             ("GET", "/api/security/sessions"), ("POST", "/api/security/sessions"),
             ("PUT", "/api/settings"), ("POST", "/api/tasks"), ("POST", "/api/plan"),
             ("POST", "/api/pair"), ("POST", "/api/laith/chats"),
             ("POST", "/api/gateway"), ("POST", "/api/tools/install"),
             ("POST", "/api/devices/action"), ("POST", "/api/capabilities/voice"),
             ("GET", "/api/agent/jobs?node=device-1"), ("POST", "/api/future-route")]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(engine)), base_url="http://localhost") as c:
        for method, path in cases:
            r = await c.request(method, path, headers=headers(token), json={"role": "parent", "child_mode": False, "confirm": True})
            assert r.status_code == 403, (method, path, r.text)
        assert (await c.get("/api/security/me", headers=headers(token))).status_code == 200


async def test_device_can_register_only_its_bound_node(engine):
    token = session(engine, "device", device="device-1")["token"]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(engine)), base_url="http://localhost") as c:
        for node, expected in (("another-device", 403), ("device-1", 200)):
            r = await c.post("/api/agent/heartbeat", headers=headers(token), json={"inventory": {"node": node}})
            assert r.status_code == expected
        assert "another-device" not in engine.nodes.nodes
        assert "device-1" in engine.nodes.nodes


async def test_relay_never_inherits_parent_authority_even_when_configured_with_parent_token(engine, tmp_path, monkeypatch):
    from switchboard.localproxy import build_proxy_app
    original_client = httpx.AsyncClient
    hub_transport = httpx.ASGITransport(app=create_app(engine), client=("203.0.113.9", 5000))

    def hub_client(**kwargs):
        return original_client(transport=hub_transport, **kwargs)
    monkeypatch.setattr("switchboard.localproxy.httpx.AsyncClient", hub_client)
    for token, expected in (("test-token", 401), (PARENT, 403), (session(engine, "parent")["token"], 403), (session(engine)["token"], 200)):
        agent = Config(make_home(tmp_path / "relay", role="agent", token=token))
        relay = build_proxy_app(agent)
        async with relay.router.lifespan_context(relay):
            async with original_client(transport=httpx.ASGITransport(app=relay), base_url="http://localhost") as c:
                response = await c.get("/api/security/me")
                assert response.status_code == expected
                assert (await c.get("/api/settings")).status_code in (401, 403)


async def test_parent_cannot_enable_egress_or_legacy_execution_over_api(engine):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(engine)), base_url="http://localhost") as c:
        for method, path in (("POST", "/api/plan"), ("POST", "/api/tasks"), ("POST", "/api/pair"),
                             ("POST", "/api/capabilities/voice"), ("POST", "/api/gateway"), ("PUT", "/api/settings")):
            assert (await c.request(method, path, headers=headers(), json={"security": {"enabled": False}, "prompt": "synthetic"})).status_code == 403
        policy = (await c.get("/api/security/policy", headers=headers())).json()
        assert policy["outbound"]["allowed"] is False
        assert policy["child_chat_enabled"] is False
        assert policy["pii_sanitization_implemented"] is True
        assert policy["text_gateway_enabled"] is False


def test_sessions_expire_survive_restart_and_rotate_with_parent_authority(engine):
    clock = [1000.0]
    s = LocalSecurity(engine.config, engine.store, now=lambda: clock[0])
    parent = s.authenticate("Bearer " + PARENT)
    issued = s.issue(parent, subject="child-1", role="child", seconds=60)
    credential = "Bearer " + issued["token"]
    assert LocalSecurity(engine.config, engine.store, now=lambda: clock[0]).authenticate(credential).role == "child"
    # The database contains only hashes, never the issued bearer credential.
    assert issued["token"] not in json.dumps([tuple(row) for row in engine.store._exec("SELECT * FROM security_sessions")])
    clock[0] = 1060
    with pytest.raises(SecurityDenied):
        s.authenticate(credential)
    clock[0] = 1000
    engine.config.secrets["SWITCHBOARD_PARENT_TOKEN"] = "rotated-parent-credential-0123456789abcdef"
    rotated = LocalSecurity(engine.config, engine.store, now=lambda: clock[0])
    with pytest.raises(SecurityDenied):
        rotated.authenticate(credential)
    with pytest.raises(SecurityDenied):
        rotated.authenticate("Bearer " + PARENT)


def test_expired_or_revoked_parent_session_cannot_issue_and_child_never_can(engine):
    parent_session = session(engine, "parent")
    p = engine.security.authenticate("Bearer " + parent_session["token"])
    root = engine.security.authenticate("Bearer " + PARENT)
    engine.security.revoke(root, parent_session["id"])
    for principal in (p, Principal("child-1", "child")):
        with pytest.raises(SecurityDenied):
            engine.security.issue(principal, subject="attacker", role="parent")


@pytest.mark.parametrize("setting", ["false", 0, None, [], {"enabled": "true"}, {"enabeld": True}])
def test_malformed_security_configuration_is_not_coerced_to_legacy(engine, setting):
    engine.config.raw["security"] = setting
    with pytest.raises(ValueError):
        LocalSecurity(engine.config, engine.store)


@pytest.mark.parametrize("token", ["", "short", "test-token"])
def test_protected_mode_requires_new_strong_parent_bootstrap(engine, token):
    engine.config.secrets["SWITCHBOARD_PARENT_TOKEN"] = token
    with pytest.raises(ValueError):
        LocalSecurity(engine.config, engine.store)


def test_parent_bootstrap_cannot_reuse_even_a_long_legacy_token(engine):
    engine.config.secrets["SWITCHBOARD_TOKEN"] = PARENT
    with pytest.raises(ValueError):
        LocalSecurity(engine.config, engine.store)


async def test_invalid_session_fields_and_child_issuance_are_rejected(engine):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(engine)), base_url="http://localhost") as c:
        for body in ({"subject": "child-1", "role": "admin"},
                     {"subject": "child-1", "role": "child", "device": "device-1"},
                     {"subject": "device-1", "role": "device"},
                     {"subject": "child-1", "role": "child", "seconds": True},
                     {"subject": "child-1", "role": "child", "permissions": ["admin"]}):
            assert (await c.post("/api/security/sessions", headers=headers(), json=body)).status_code == 422


async def test_execution_guards_precede_provider_transport_storage_and_subprocess(engine, monkeypatch):
    from switchboard.providers.anthropic_provider import AnthropicProvider
    from switchboard.providers.openai_compat import OpenAICompatProvider
    from switchboard.providers.cli_bridge import CLIProvider
    from switchboard.providers.handoff import HandoffProvider
    from switchboard.providers.base import RunRequest
    from switchboard.capabilities.adapters import execute_service
    from switchboard.router import classify
    from switchboard import notify

    def forbidden(*a, **kw):
        pytest.fail("No SDK, subprocess or network transport may be created")
    monkeypatch.setattr("openai.AsyncOpenAI", forbidden)
    monkeypatch.setattr("anthropic.AsyncAnthropic", forbidden)
    monkeypatch.setattr("asyncio.create_subprocess_exec", forbidden)
    monkeypatch.setattr("httpx.AsyncClient", forbidden)
    req = RunRequest("synthetic identifier", engine.catalog.find("mock:mock-large"))
    for cls in (AnthropicProvider, OpenAICompatProvider, CLIProvider, HandoffProvider):
        with pytest.raises(SecurityDenied):
            await cls("blocked", {}, engine.config).run(req)
    with pytest.raises(SecurityDenied):
        await engine._llm_classify("synthetic", classify("synthetic"))
    plan = engine.router.plan("synthetic", {"mock"}, classify("synthetic"))
    with pytest.raises(SecurityDenied):
        engine.start_task("synthetic", plan)
    with pytest.raises(SecurityDenied):
        await engine.run_task("blocked-task", "synthetic", plan, [], [])
    assert engine.store.recent_tasks() == []
    assert engine.running == {}
    with pytest.raises(SecurityDenied):
        await engine.mcp.ensure("not-started")
    with pytest.raises(SecurityDenied):
        await engine.executor.execute("cli_run", {})
    with pytest.raises(SecurityDenied):
        await engine._run_cli(None, req, engine.config.output_dir)
    with pytest.raises(SecurityDenied):
        await engine.build_toolbox(None, engine.config.output_dir)
    with pytest.raises(SecurityDenied):
        await engine.create_gateway([], None)
    with pytest.raises(SecurityDenied):
        engine.start_install("device-1", "unused")
    with pytest.raises(SecurityDenied):
        await execute_service(engine.config, {}, "audio.transcribe", {}, [], {}, None)
    with pytest.raises(SecurityDenied):
        engine.capabilities.approve("does-not-exist")
    with pytest.raises(SecurityDenied):
        await engine.capabilities.run("does-not-exist")
    assert await notify.send(engine.config, "synthetic", "synthetic") is False


@pytest.mark.parametrize("role", [None, "parent", "child", "device", "guest"])
def test_outbound_policy_has_no_implicit_role_or_destination_grant(role):
    principal = Principal("opaque", role) if role else None
    for destination in ("https://api.openai.com/v1", "https://api.anthropic.com", "http://localhost:11434"):
        assert outbound_decision(principal, OutboundIntent(destination, "chat")).allowed is False
