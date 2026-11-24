"""Regression reproductions from the Phase 5–7 audit; synthetic and offline."""
import asyncio
import json
import os
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
from cryptography.fernet import Fernet

from switchboard.config import Config
from switchboard.engine import Engine
from switchboard.retention import RetentionFailure
from switchboard.security import SecurityDenied
from switchboard.web.app import create_app
from .conftest import make_home
from .test_local_security import PARENT, headers


@pytest.fixture
def engine(tmp_path, monkeypatch):
    for name in ("SWITCHBOARD_PARENT_TOKEN", "SWITCHBOARD_RETENTION_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    config = Config(make_home(tmp_path / "audit"))
    config.raw["security"] = {"enabled": True,
        "text_gateway": {"enabled": True, "model": "audit-model"},
        "local_tools": {"enabled": True, "allowed": ["calculate"]},
        "child_safety": {"enabled": True, "review_queue": True},
        "retention": {"enabled": True, "seconds": 3600}}
    config.secrets.update(SWITCHBOARD_PARENT_TOKEN=PARENT, OPENAI_API_KEY="synthetic-audit-api-key",
                          SWITCHBOARD_RETENTION_KEY=Fernet.generate_key().decode())
    e = Engine(config)
    yield e
    e.retention.close(); e.local_tools.close(); e.text_gateway.close(); e.store._db.close()


def parent(e):
    return e.security.authenticate("Bearer " + PARENT)


def test_backup_cannot_outlive_any_source_copy(engine):
    v = engine.retention; p = parent(engine); clock = v.now(); v.now = lambda: clock
    root = v.create(p)
    short = v.put(p, root, "queue", b"short-lived synthetic content")
    long = v.put(p, root, "conversation", b"long-lived synthetic content")
    backup = v.backup(p, root)
    deadline = v.db.execute("SELECT expires FROM artifacts WHERE id=?", (short,)).fetchone()[0]
    assert v.db.execute("SELECT expires FROM artifacts WHERE id=?", (backup,)).fetchone()[0] <= deadline
    v.now = lambda: clock + 901
    with pytest.raises(RetentionFailure): v.read(p, root, backup)
    v.maintain(p)
    assert not (v.path / "files" / (backup + ".bin")).exists()
    assert v.read(p, root, long) == b"long-lived synthetic content"


@pytest.mark.parametrize("parent_id", ["", False, 0])
def test_explicit_invalid_lineage_parent_is_rejected(engine, parent_id):
    v = engine.retention; p = parent(engine); root = v.create(p)
    with pytest.raises(RetentionFailure):
        v.put(p, root, "transcript", b"synthetic", parent=parent_id)
    assert v.db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 0


@pytest.mark.parametrize("change", ["disable", "policy", "key"])
def test_agent_ack_obeys_current_retention_policy_and_key(engine, change):
    v = engine.retention; p = parent(engine); root = v.create(p); agent = "a" * 32
    token = v.register_agent(p, root, agent); v._tombstone(root)
    if change == "disable": engine.config.raw["security"]["retention"]["enabled"] = False
    if change == "policy": engine.config.raw["security"]["retention"]["seconds"] = 120
    if change == "key": engine.config.secrets["SWITCHBOARD_RETENTION_KEY"] = Fernet.generate_key().decode()
    with pytest.raises(SecurityDenied): v.agent_ack(root, agent, token)
    assert v.db.execute("SELECT state FROM agents").fetchone()[0] == "pending"


@pytest.mark.parametrize("storage", ["database", "file"])
def test_rejected_restore_preflights_all_conflicts_before_writes(engine, storage):
    v = engine.retention; p = parent(engine); root = v.create(p)
    v.put(p, root, "conversation", b"first synthetic item", storage=storage)
    v.put(p, root, "transcript", b"second synthetic item", storage=storage)
    snapshot = v.snapshot(p, root)
    items = json.loads(v.cipher(root).decrypt(snapshot))["items"]
    first, second = items[0]["id"], items[1]["id"]
    bad = v.cipher(root).encrypt(b"conflicting synthetic bytes")
    if storage == "database":
        with v.data:
            v.data.execute("DELETE FROM payloads WHERE id=?", (first,))
            v.data.execute("UPDATE payloads SET cipher=? WHERE id=?", (bad, second))
    else:
        (v.path / "files" / (first + ".bin")).unlink()
        (v.path / "files" / (second + ".bin")).write_bytes(bad)
    with pytest.raises(RetentionFailure): v.restore(p, root, snapshot)
    if storage == "database":
        assert not v.data.execute("SELECT 1 FROM payloads WHERE id=?", (first,)).fetchone()
    else:
        assert not (v.path / "files" / (first + ".bin")).exists()


def test_unexpected_fifo_is_rejected_without_waiting_for_writer(engine):
    v = engine.retention; p = parent(engine); root = v.create(p)
    key = v.put(p, root, "transcript", b"synthetic", storage="file")
    path = v.path / "files" / (key + ".bin"); path.unlink(); os.mkfifo(path)
    def rescue():
        try: fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK); os.close(fd)
        except OSError: pass
    timer = threading.Timer(.3, rescue); timer.start()
    try:
        start = time.monotonic()
        with pytest.raises(RetentionFailure): v.read(p, root, key)
        assert time.monotonic() - start < .15
    finally: timer.cancel(); timer.join()


async def test_retention_preview_does_not_block_http_event_loop_on_storage_lock(engine):
    v = engine.retention; p = parent(engine); root = v.create(p)
    acquired = threading.Event(); release = threading.Event()
    def hold():
        with v.lock: acquired.set(); release.wait(2)
    thread = threading.Thread(target=hold); thread.start(); assert acquired.wait(1)
    rescue = threading.Timer(.3, release.set); rescue.start()
    try:
        start = time.monotonic()
        task = asyncio.create_task(v.preview_delete(p, root))
        await asyncio.sleep(.02)
        elapsed = time.monotonic() - start
        release.set(); await task
        assert elapsed < .15
    finally: release.set(); thread.join(); rescue.cancel(); rescue.join()


async def test_child_policy_changes_invalidate_local_tool_approval(engine):
    tools = engine.local_tools; p = parent(engine)
    preview = await tools.preview(p, tool="calculate", arguments={"expression": "2+2"})
    engine.config.raw["security"]["child_safety"]["age_band"] = "6-8"
    calls = []
    async def worker(*args): calls.append(args); return "4"
    tools.worker.run = worker
    with pytest.raises(SecurityDenied):
        await tools.run(p, preview_id=preview["preview_id"], payload_digest=preview["payload_digest"], confirm=True)
    assert calls == []


async def test_overlong_disclosure_retains_supportive_fallback_and_category(engine):
    result = await engine.text_gateway.safety.gate(parent(engine), ("My dad hits me. " + "x" * 3000,), stage="response")
    assert "abuse_disclosure" in result["categories"] and result["check_unavailable"]
    assert "not your fault" in result["answer"] and "choose someone else" in result["answer"]


@pytest.mark.parametrize("section,patch,path,body", [
    ("text_gateway", {"provider": "unsupported"}, "/api/privacy/preview", {"prompt": "Explain fractions"}),
    ("local_tools", {"allowed": ["unsupported"]}, "/api/privacy/tools/preview", {"tool": "calculate", "arguments": {"expression": "2+2"}}),
])
async def test_invalid_runtime_policies_return_authorization_denial(engine, section, patch, path, body):
    engine.config.raw["security"][section].update(patch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(engine)), base_url="http://local") as c:
        assert (await c.post(path, headers=headers(), json=body)).status_code == 403


def test_restore_io_failure_marks_existing_copy_staging_for_recovery(engine, monkeypatch):
    v = engine.retention; p = parent(engine); root = v.create(p)
    key = v.put(p, root, "conversation", b"synthetic original", storage="file")
    snapshot = v.snapshot(p, root)
    (v.path / "files" / (key + ".bin")).unlink()
    def disk_error(*args): raise OSError("synthetic fsync failure")
    with monkeypatch.context() as patch:
        patch.setattr("switchboard.retention.os.fsync", disk_error)
        with pytest.raises((OSError, RetentionFailure)): v.restore(p, root, snapshot)
    assert v.db.execute("SELECT state FROM artifacts WHERE id=?", (key,)).fetchone()[0] == "staging"
    with pytest.raises(RetentionFailure): v.read(p, root, key)
    v.maintain(p)
    assert not (v.path / "files" / (key + ".bin")).exists()
    assert v.restore(p, root, snapshot)["restored"] == 1
    assert v.read(p, root, key) == b"synthetic original"


async def test_root_cleanup_removes_attributed_orphans_without_touching_other_roots(engine):
    v = engine.retention; p = parent(engine); root = v.create(p); other = v.create(p)
    orphan, keep = "d" * 32, "e" * 32
    with v.data:
        v.data.execute("INSERT INTO payloads VALUES(?,?,?)", (orphan, root, v.cipher(root).encrypt(b"synthetic orphan")))
        v.data.execute("INSERT INTO search_index VALUES(?,?,?)", (orphan, root, " ".join(v.terms(root, "orphan"))))
        v.data.execute("INSERT INTO payloads VALUES(?,?,?)", (keep, other, v.cipher(other).encrypt(b"keep other orphan")))
    preview = await v.preview_delete(p, root)
    assert v.confirm_delete(p, preview["preview_id"], preview["digest"], True)["local_deleted"]
    assert not v.data.execute("SELECT 1 FROM payloads WHERE root=?", (root,)).fetchone()
    assert not v.data.execute("SELECT 1 FROM search_index WHERE root=?", (root,)).fetchone()
    assert v.data.execute("SELECT 1 FROM payloads WHERE id=?", (keep,)).fetchone()


def test_inflight_retention_read_rechecks_parent_revocation(engine, monkeypatch):
    v = engine.retention; bootstrap = parent(engine); root = v.create(bootstrap)
    key = v.put(bootstrap, root, "conversation", b"must not return after revocation")
    issued = engine.security.issue(bootstrap, role="parent", subject="audit-reader")
    reader = engine.security.authenticate("Bearer " + issued["token"])
    original = v._blob
    def revoked(row):
        value = original(row)
        engine.security.revoke(bootstrap, issued["id"])
        return value
    monkeypatch.setattr(v, "_blob", revoked)
    with pytest.raises(SecurityDenied): v.read(reader, root, key)


def test_snapshot_rechecks_short_deadlines_after_io(engine, monkeypatch):
    v = engine.retention; p = parent(engine); clock = v.now(); v.now = lambda: clock
    root = v.create(p); v.put(p, root, "temporary", b"must expire")
    original = v._blob
    def expired(row):
        value = original(row); v.now = lambda: clock + 901
        return value
    monkeypatch.setattr(v, "_blob", expired)
    with pytest.raises(RetentionFailure): v.snapshot(p, root)


async def test_inventory_preview_storage_error_is_fixed_no_echo(engine, monkeypatch):
    v = engine.retention; root = v.create(parent(engine))
    def unavailable(*args): raise OSError("private synthetic storage path")
    monkeypatch.setattr(v, "_plan", unavailable)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(engine)), base_url="http://local") as c:
        result = await c.post("/api/retention/delete/preview", headers=headers(), json={"root": root})
        assert result.status_code == 409 and result.json() == {"detail": "retention_unavailable"}


async def test_protected_status_never_executes_legacy_cli_feature_probes(engine, monkeypatch):
    from switchboard.providers.cli_bridge import CLIProvider
    calls = []
    def probe(*args, **kwargs):
        calls.append(args[0])
        return SimpleNamespace(returncode=0, stdout="image_generation stable true\n")
    monkeypatch.setattr("switchboard.runtime.which", lambda *args: "/synthetic-audit-cli")
    monkeypatch.setattr("switchboard.runtime._FEATURE_CACHE", {})
    monkeypatch.setattr("switchboard.runtime.subprocess.run", probe)
    engine.providers = {"codex-images": CLIProvider("codex-images", {"command":"codex", "required_feature":"image_generation"}, engine.config)}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(engine)), base_url="http://local") as c:
        result = await c.get("/api/status", headers=headers())
        assert result.status_code == 200
        assert calls == []
        assert result.json()["providers"][0]["available"] is False


def test_protected_availability_ignores_advertised_remote_clis(engine, monkeypatch):
    from switchboard.providers.cli_bridge import CLIProvider
    provider = CLIProvider("codex-images", {"required_feature":"image_generation"}, engine.config)
    engine.providers = {"codex-images":provider}
    monkeypatch.setattr(provider, "available", lambda: False)
    engine.nodes.heartbeat({"node":"synthetic-agent", "cli_providers":["codex-images"]})
    assert engine.available_providers(refresh=True) == set()
    assert engine.cli_location("codex-images") is None


async def test_protected_status_does_not_call_provider_availability_hooks(engine, monkeypatch):
    for provider in engine.providers.values():
        monkeypatch.setattr(provider, "available", lambda: pytest.fail("Status must not execute provider probes"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(engine)), base_url="http://local") as c:
        response = await c.get("/api/status", headers=headers())
        assert response.status_code == 200 and all(not p["available"] for p in response.json()["providers"])


async def test_legacy_agent_stops_before_inventory_or_outbound_client_in_protected_mode(engine, monkeypatch):
    from switchboard.agent import run_agent
    async def forbidden(*args, **kwargs): pytest.fail("Legacy agent must not inventory or send in protected mode")
    monkeypatch.setattr("switchboard.agent.build_inventory", forbidden)
    with pytest.raises(SecurityDenied): await run_agent(engine.config)


async def test_legacy_inventory_stops_before_tool_device_or_network_probes(engine, monkeypatch):
    from switchboard.inventory import build_inventory
    def forbidden(*args, **kwargs): pytest.fail("Legacy inventory probes are not approved protected tools")
    monkeypatch.setattr("switchboard.inventory.detect_tools", forbidden)
    with pytest.raises(SecurityDenied): await build_inventory(engine.config)


def test_legacy_relay_stops_before_unauthenticated_local_controls_or_client_creation(engine, monkeypatch):
    from switchboard.localproxy import build_proxy_app
    def forbidden(*args, **kwargs): pytest.fail("Protected mode must not construct the legacy relay client")
    monkeypatch.setattr("switchboard.localproxy.httpx.AsyncClient", forbidden)
    with pytest.raises(SecurityDenied): build_proxy_app(engine.config)


async def test_device_heartbeat_bounds_chunked_body_before_inventory_storage(engine):
    p = parent(engine)
    credential = engine.security.issue(p, role="device", subject="audit-device", device="device-1")["token"]
    raw = json.dumps({"inventory": {"node": "device-1", "diagnostic": "PrivateSyntheticMarker" + "x" * 70000}}).encode()
    async def chunks():
        for offset in range(0, len(raw), 4096): yield raw[offset:offset+4096]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(engine)), base_url="http://local") as c:
        result = await c.post("/api/agent/heartbeat", headers={**headers(credential), "Content-Type":"application/json"}, content=chunks())
        assert result.status_code in (400, 413) and "PrivateSyntheticMarker" not in result.text
        assert "device-1" not in engine.nodes.nodes


async def test_device_revoked_while_reading_heartbeat_cannot_update_inventory(engine):
    p = parent(engine)
    credential = engine.security.issue(p, role="device", subject="audit-device", device="device-1")
    raw = json.dumps({"inventory": {"node": "device-1"}}).encode()
    async def chunks():
        yield raw[:5]
        engine.security.revoke(p, credential["id"])
        yield raw[5:]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(engine)), base_url="http://local") as c:
        result = await c.post("/api/agent/heartbeat", headers={**headers(credential["token"]), "Content-Type":"application/json"}, content=chunks())
        assert result.status_code in (401, 403) and "device-1" not in engine.nodes.nodes


@pytest.mark.parametrize("invalid_root", [None, []])
@pytest.mark.parametrize("path,body", [
    ("/api/privacy/preview", {"prompt":"Explain fractions"}),
    ("/api/privacy/tools/preview", {"tool":"calculate","arguments":{"expression":"2+2"}}),
    ("/api/safety/evaluate", {"input":["Explain fractions"],"candidate":"Two halves make one whole."}),
    ("/api/retention/receipt", {"root":"a"*32}),
])
async def test_malformed_security_root_is_denied_uniformly(engine, invalid_root, path, body):
    app = create_app(engine)
    engine.config.raw["security"] = invalid_root
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as c:
        result = await c.post(path, headers=headers(), json=body)
        assert result.status_code == 403
