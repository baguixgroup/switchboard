"""Registered synthetic data only: encryption, lineage, crash/replay/restore and API scope."""
import hashlib
import json
import os
import sqlite3
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet

from switchboard.config import Config
from switchboard.engine import Engine
from switchboard.retention import KINDS, Retention, RetentionFailure, retention_policy
from switchboard.retention_inventory import legacy_inventory
from switchboard.security import SecurityDenied
from switchboard.web.app import create_app
from .conftest import make_home
from .test_local_security import PARENT, headers, session


@pytest.fixture
def engine(tmp_path, monkeypatch):
    for key in ("SWITCHBOARD_PARENT_TOKEN","SWITCHBOARD_RETENTION_KEY"):
        monkeypatch.delenv(key,raising=False)
    config=Config(make_home(tmp_path/"phase6"))
    config.raw["security"]={"enabled":True,"retention":{"enabled":True,"seconds":3600}}
    config.secrets.update(SWITCHBOARD_PARENT_TOKEN=PARENT,SWITCHBOARD_RETENTION_KEY=Fernet.generate_key().decode())
    e=Engine(config)
    yield e
    e.retention.close(); e.local_tools.close(); e.text_gateway.close(); e.store._db.close()


def parent(e): return e.security.authenticate("Bearer "+PARENT)


async def delete(e,root):
    p=parent(e); preview=await e.retention.preview_delete(p,root)
    return e.retention.confirm_delete(p,preview["preview_id"],preview["digest"],True)


@pytest.mark.parametrize("storage",["database","file"])
async def test_all_kinds_cascade_db_fts_files_queues_cache_backup(engine,storage,caplog):
    vault=engine.retention;p=parent(engine);root=vault.create(p)
    first=None;ids=[]
    sensitive=b"SyntheticSensitiveMarker alpha beta"
    for kind in KINDS:
        key=vault.put(p,root,kind,sensitive,parent=first,storage=storage)
        if first is None:first=key
        ids.append(key)
        assert vault.read(p,root,key)==sensitive
    backup=vault.backup(p,root)
    assert first in vault.search(p,root,"SyntheticSensitiveMarker")
    for file in vault.path.rglob("*"):
        if file.is_file(): assert sensitive not in file.read_bytes()
    plan=await vault.preview_delete(p,root)
    assert "SyntheticSensitiveMarker" not in json.dumps(plan) and plan["scope"]=="registered_root_only"
    assert vault.read(p,root,first)==sensitive # preview is read-only
    receipt=vault.confirm_delete(p,plan["preview_id"],plan["digest"],True)
    assert receipt["local_deleted"] and receipt["tombstoned"] and receipt["local_key_destroyed"]
    assert not receipt["provider_deletion_guaranteed"] and not receipt["physical_erasure_guaranteed"]
    assert vault.data.execute("SELECT COUNT(*) FROM payloads").fetchone()[0]==0
    assert vault.data.execute("SELECT COUNT(*) FROM search_index").fetchone()[0]==0
    assert list((vault.path/"files").iterdir())==[]
    for key in ids+[backup]:
        with pytest.raises(RetentionFailure): vault.read(p,root,key)
    with pytest.raises(RetentionFailure): vault.put(p,root,"agent_result",b"late result")
    assert "SyntheticSensitiveMarker" not in caplog.text


async def test_deletion_leaves_legacy_and_other_registered_root_untouched(engine):
    p=parent(engine);v=engine.retention
    engine.store.save_task("legacy-task","done",prompt="LegacySyntheticContent")
    doc=engine.studio.knowledge.put("Legacy synthetic","LegacySyntheticContent")
    legacy_file=engine.config.output_dir/"legacy-synthetic.txt";legacy_file.write_text("LegacySyntheticContent")
    root=v.create(p);other=v.create(p)
    v.put(p,root,"conversation",b"delete only this")
    retain=v.put(p,other,"conversation",b"keep other root")
    await delete(engine,root)
    assert engine.store.get_task("legacy-task")["prompt"]=="LegacySyntheticContent"
    assert engine.studio.knowledge.get(doc["id"])["text"]=="LegacySyntheticContent"
    assert legacy_file.read_text()=="LegacySyntheticContent" and v.read(p,other,retain)==b"keep other root"


async def test_preview_owner_digest_expiry_replay_and_changed_lineage(engine):
    v=engine.retention;p=parent(engine);root=v.create(p)
    v.put(p,root,"conversation",b"text")
    preview=await v.preview_delete(p,root)
    issued=engine.security.issue(p,role="parent",subject="other-parent")
    other=engine.security.authenticate("Bearer "+issued["token"])
    for principal,digest,confirm in [(other,preview["digest"],True),(p,"0"*64,True),(p,preview["digest"],False)]:
        with pytest.raises(SecurityDenied): v.confirm_delete(principal,preview["preview_id"],digest,confirm)
    v.put(p,root,"queue",b"new queued result")
    with pytest.raises(RetentionFailure,match="retention_conflict"):v.confirm_delete(p,preview["preview_id"],preview["digest"],True)
    with pytest.raises(SecurityDenied):v.confirm_delete(p,preview["preview_id"],preview["digest"],True)
    preview=await v.preview_delete(p,root);clock=v.now();v.now=lambda:clock+121
    with pytest.raises(SecurityDenied):v.confirm_delete(p,preview["preview_id"],preview["digest"],True)
    v.now=lambda:clock
    preview=await v.preview_delete(other,root);engine.security.revoke(p,issued["id"])
    with pytest.raises(SecurityDenied):v.confirm_delete(other,preview["preview_id"],preview["digest"],True)
    assert v.active(root)


async def test_crash_after_tombstone_before_cleanup_restart_denies_late_results(engine,monkeypatch):
    v=engine.retention;p=parent(engine);root=v.create(p)
    key=v.put(p,root,"transcript",b"synthetic transcript",storage="file")
    snapshot=v.snapshot(p,root)
    def crash(root):raise RuntimeError("synthetic power loss")
    with monkeypatch.context() as patch:
        patch.setattr(v,"_cleanup",crash)
        with pytest.raises(RuntimeError):await delete(engine,root)
    assert v.receipt(p,root)["cleanup_pending"]
    assert (v.path/"files"/(key+".bin")).exists()
    v.close();v=Retention(engine.config,engine.security);engine.retention=v
    for action in (lambda:v.read(p,root,key),lambda:v.restore(p,root,snapshot),lambda:v.put(p,root,"agent_result",b"late")):
        with pytest.raises(RetentionFailure):action()
    v.maintain(p)
    assert v.receipt(p,root)["local_deleted"] and not (v.path/"files"/(key+".bin")).exists()


async def test_partial_file_failure_retries_and_never_deletes_symlink_target(engine):
    v=engine.retention;p=parent(engine);root=v.create(p)
    key=v.put(p,root,"temporary",b"synthetic",storage="file")
    target=engine.config.home/"outside.txt";target.write_text("must survive")
    owned=v.path/"files"/(key+".bin");owned.unlink();owned.symlink_to(target)
    result=await delete(engine,root)
    assert result["cleanup_pending"] and not result["local_deleted"] and target.read_text()=="must survive"
    owned.unlink() # synthetic test repair, not product traversal
    v.maintain(p)
    assert v.receipt(p,root)["local_deleted"] and target.read_text()=="must survive"


async def test_finite_retention_access_cutoff_and_temporary_cascade(engine):
    v=engine.retention;p=parent(engine);clock=v.now();v.now=lambda:clock
    root=v.create(p);temporary=v.put(p,root,"temporary",b"short lived",storage="file")
    child=v.put(p,root,"transcript",b"derived",parent=temporary)
    keep=v.put(p,root,"conversation",b"longer lived")
    v.now=lambda:clock+901
    for key in (temporary,child):
        with pytest.raises(RetentionFailure):v.read(p,root,key)
    with pytest.raises(RetentionFailure):v.put(p,root,"transcript",b"late",parent=temporary)
    v.maintain(p)
    assert v.read(p,root,keep)==b"longer lived" and not (v.path/"files"/(temporary+".bin")).exists()
    v.now=lambda:clock+3601
    with pytest.raises(RetentionFailure):v.read(p,root,keep)
    v._scheduled_maintain()
    assert v.receipt(p,root)["tombstoned"] and v.receipt(p,root)["local_key_destroyed"]


@pytest.mark.parametrize("storage",["database","file"])
async def test_supported_data_only_restore_and_tombstone_precedence(engine,storage):
    v=engine.retention;p=parent(engine);root=v.create(p)
    key=v.put(p,root,"conversation",b"restorable synthetic text",storage=storage)
    snapshot=v.snapshot(p,root)
    assert b"restorable synthetic text" not in snapshot and b"wrapped" not in snapshot
    with v.data:v.data.execute("DELETE FROM payloads WHERE id=?",(key,));v.data.execute("DELETE FROM search_index WHERE artifact=?",(key,))
    if storage=="file":(v.path/"files"/(key+".bin")).unlink()
    receipt=v.restore(p,root,snapshot)
    assert receipt["tombstones_preserved"] and not receipt["keys_restored"]
    assert v.read(p,root,key)==b"restorable synthetic text" and v.search(p,root,"restorable")==[key]
    await delete(engine,root)
    with pytest.raises(RetentionFailure):v.restore(p,root,snapshot)
    assert v.data.execute("SELECT COUNT(*) FROM payloads").fetchone()[0]==0


async def test_restore_rejects_other_root_expired_copies_corruption_and_bad_lineage(engine):
    v=engine.retention;p=parent(engine);root=v.create(p);other=v.create(p)
    v.put(p,root,"queue",b"short lived")
    snapshot=v.snapshot(p,root)
    for bad in (b"not encrypted",snapshot[:-5]+b"wrong"):
        with pytest.raises(RetentionFailure):v.restore(p,root,bad)
    with pytest.raises(RetentionFailure):v.restore(p,other,snapshot)
    cipher=v.cipher(root);value=json.loads(cipher.decrypt(snapshot));value["items"][0]["parent"]=value["items"][0]["id"]
    with pytest.raises(RetentionFailure):v.restore(p,root,cipher.encrypt(json.dumps(value).encode()))
    clock=v.now();v.now=lambda:clock+901
    with pytest.raises(RetentionFailure):v.restore(p,root,snapshot)


async def test_offline_agent_ack_pending_retry_capability_and_provider_limits(engine):
    v=engine.retention;p=parent(engine);root=v.create(p);agent="a"*32
    token=v.register_agent(p,root,agent)
    for provider in ("openai","anthropic","gemini"):v.register_provider(p,root,provider)
    with pytest.raises(SecurityDenied):v.agent_ack(root,agent,token)
    result=await delete(engine,root)
    assert result["local_deleted"] and result["agent_copies"]==[{"agent":agent,"state":"pending"}]
    assert all(r["state"]=="no_request_level_deletion_adapter" for r in result["provider_copies"])
    with pytest.raises(SecurityDenied):v.agent_ack(root,agent,"wrong")
    assert v.agent_ack(root,agent,token)["verification"]=="authenticated_agent_claim_only"
    assert v.agent_ack(root,agent,token)["acknowledged"] # idempotent retry
    assert v.receipt(p,root)["agent_copies"][0]["state"]=="reported_deleted"
    assert not v.receipt(p,root)["provider_deletion_guaranteed"]


def test_no_default_persistence_required_key_wrong_key_lock_and_symlink_refusal(engine):
    with pytest.raises(RetentionFailure): Retention(engine.config,engine.security)
    engine.retention.close()
    current=engine.config.secrets["SWITCHBOARD_RETENTION_KEY"]
    engine.config.secrets["SWITCHBOARD_RETENTION_KEY"]=Fernet.generate_key().decode()
    with pytest.raises(RetentionFailure):Retention(engine.config,engine.security)
    engine.config.secrets["SWITCHBOARD_RETENTION_KEY"]=current
    opened=Retention(engine.config,engine.security);opened.close()
    engine.config.raw["security"]["retention"]={"enabled":False}
    v=Retention(engine.config,engine.security)
    assert v.db is None
    v.close()


@pytest.mark.parametrize("policy",[{"enabled":"true"},{"enabled":True,"seconds":0},{"enabled":True,"seconds":True},
    {"enabled":True,"seconds":2592001},{"enabled":True,"path":"/tmp/unsafe"},{"enabled":True,"delete_legacy":True}])
def test_strict_finite_policy(engine,policy):
    engine.config.raw["security"]["retention"]=policy
    with pytest.raises(ValueError):retention_policy(engine.config)


@pytest.mark.parametrize("role",["child","device"])
async def test_all_retention_routes_deny_child_device_and_relay(engine,role):
    token=session(engine,role=role,**({"device":"node"} if role=="device" else {}))["token"]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(engine)),base_url="http://local") as client:
        for method,route in [("GET","/api/retention/policy"),("GET","/api/retention/inventory"),
            ("POST","/api/retention/delete/preview"),("POST","/api/retention/delete/confirm"),
            ("POST","/api/retention/receipt"),("POST","/api/retention/maintain")]:
            assert (await client.request(method,route,headers=headers(token),json={})).status_code==403
            assert (await client.request(method,route,headers={**headers(),"X-Switchboard-Relay":"1"},json={})).status_code in (401,403)


async def test_api_requires_exact_review_and_never_accepts_paths_or_raw_content(engine):
    v=engine.retention;p=parent(engine);root=v.create(p);key=v.put(p,root,"conversation",b"PrivateSyntheticMarker")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(engine)),base_url="http://local") as client:
        preview=await client.post("/api/retention/delete/preview",headers=headers(),json={"root":root})
        assert preview.status_code==200 and "PrivateSyntheticMarker" not in preview.text
        value=preview.json()
        bad=await client.post("/api/retention/delete/confirm",headers=headers(),json={"preview_id":value["preview_id"],"digest":value["digest"],"confirm":"true"})
        assert bad.status_code==400 and v.read(p,root,key)==b"PrivateSyntheticMarker"
        good=await client.post("/api/retention/delete/confirm",headers=headers(),json={"preview_id":value["preview_id"],"digest":value["digest"],"confirm":True})
        assert good.status_code==200 and good.json()["local_deleted"] and good.headers["cache-control"]=="no-store"
        for value in ({"root":"../../private-marker"},{"root":root,"path":"private-marker"}):
            response=await client.post("/api/retention/delete/preview",headers=headers(),json=value)
            assert response.status_code==400 and "private-marker" not in response.text


def test_read_only_inventory_db_fts_counts_no_payload_names_and_no_initialization(tmp_path):
    home=tmp_path/"legacy";data=home/"data";data.mkdir(parents=True)
    db=sqlite3.connect(data/"switchboard.db")
    db.executescript("CREATE TABLE tasks(id TEXT,prompt TEXT); CREATE VIRTUAL TABLE studio_fts USING fts5(body);")
    db.execute("INSERT INTO tasks VALUES('secret-id','PrivateSyntheticMarker')")
    db.execute("INSERT INTO studio_fts VALUES('PrivateSyntheticMarker')");db.commit();db.close()
    output=tmp_path/"output";output.mkdir();(output/"private-filename.txt").write_text("PrivateSyntheticMarker")
    (output/"outside-link").symlink_to(tmp_path,target_is_directory=True)
    path=data/"switchboard.db";before=hashlib.sha256(path.read_bytes()).hexdigest()
    report=legacy_inventory(home,output)
    assert report["legacy_database"]["tables"]=={"tasks":1,"studio_fts":1}
    assert report["output_tree"]["files"]==1 and report["output_tree"]["symlinks_skipped"]==1
    assert "PrivateSyntheticMarker" not in json.dumps(report) and "private-filename" not in json.dumps(report)
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before
    missing=tmp_path/"not-created";legacy_inventory(missing,missing)
    assert not missing.exists()


async def test_policy_key_changes_and_api_authorization_never_delete(engine):
    v=engine.retention;p=parent(engine);root=v.create(p)
    key=v.put(p,root,"conversation",b"must remain")
    preview=await v.preview_delete(p,root)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(engine)),base_url="http://local") as client:
        bad=await client.post("/api/retention/delete/confirm",headers=headers(),json={"preview_id":preview["preview_id"],"digest":"0"*64,"confirm":True})
        assert bad.status_code==403
    original=engine.config.secrets["SWITCHBOARD_RETENTION_KEY"]
    engine.config.secrets["SWITCHBOARD_RETENTION_KEY"]=Fernet.generate_key().decode()
    with pytest.raises(SecurityDenied):v.confirm_delete(p,preview["preview_id"],preview["digest"],True)
    engine.config.secrets["SWITCHBOARD_RETENTION_KEY"]=original
    engine.config.raw["security"]["retention"]["seconds"]=120
    with pytest.raises(SecurityDenied):v.confirm_delete(p,preview["preview_id"],preview["digest"],True)
    engine.config.raw["security"]["retention"]["seconds"]=3600
    assert v.read(p,root,key)==b"must remain"


def test_fresh_disabled_store_never_creates_vault_missing_key_and_symlink_fail_closed(tmp_path,monkeypatch):
    monkeypatch.delenv("SWITCHBOARD_RETENTION_KEY",raising=False)
    monkeypatch.delenv("SWITCHBOARD_PARENT_TOKEN",raising=False)
    cfg=Config(make_home(tmp_path/"fresh"));cfg.raw["security"]={"enabled":True}
    cfg.secrets["SWITCHBOARD_PARENT_TOKEN"]=PARENT
    e=Engine(cfg)
    try:
        assert not (cfg.home/"data/retention-v1").exists()
        cfg.raw["security"]["retention"]={"enabled":True}
        with pytest.raises(RetentionFailure):Retention(cfg,e.security)
        assert not (cfg.home/"data/retention-v1").exists()
        cfg.secrets["SWITCHBOARD_RETENTION_KEY"]=Fernet.generate_key().decode()
        outside=tmp_path/"outside";outside.mkdir();sentinel=outside/"sentinel";sentinel.write_text("safe")
        (cfg.home/"data/retention-v1").symlink_to(outside,target_is_directory=True)
        with pytest.raises(RetentionFailure):Retention(cfg,e.security)
        assert sentinel.read_text()=="safe" and list(outside.iterdir())==[sentinel]
    finally:
        e.retention.close();e.local_tools.close();e.text_gateway.close();e.store._db.close()


@pytest.mark.parametrize("bad",["../escape","/absolute","A"*32,{},None])
def test_no_arbitrary_ids_or_paths_in_storage(engine,bad):
    with pytest.raises(RetentionFailure):engine.retention.put(parent(engine),bad,"conversation",b"payload")


def test_staging_crash_recovery_and_root_bound_search_tokens(engine):
    v=engine.retention;p=parent(engine);a=v.create(p);b=v.create(p)
    ka=v.put(p,a,"conversation",b"same private terms",storage="file")
    kb=v.put(p,b,"conversation",b"same private terms")
    assert v.terms(a,"private")!=v.terms(b,"private")
    assert v.search(p,a,"private")==[ka] and v.search(p,b,"private")==[kb]
    with v.db:v.db.execute("UPDATE artifacts SET state='staging' WHERE id=?",(ka,))
    with pytest.raises(RetentionFailure):v.read(p,a,ka)
    v.maintain(p)
    assert not (v.path/"files"/(ka+".bin")).exists() and not v.search(p,a,"private")
    assert v.read(p,b,kb)==b"same private terms"


async def test_pending_maintenance_reports_blocked_cleanup_and_clears_after_repair(engine):
    v=engine.retention;p=parent(engine);root=v.create(p)
    key=v.put(p,root,"conversation",b"synthetic",storage="file")
    owned=v.path/"files"/(key+".bin");owned.unlink();owned.mkdir()
    result=await delete(engine,root)
    assert result["cleanup_pending"]
    status=v.maintain(p)
    assert status["maintenance_complete"] is False and status["cleanup_pending_roots"]==1
    owned.rmdir()
    assert v.maintain(p)["maintenance_complete"]


async def test_preview_capacity_close_and_persistent_offline_agent_ack(engine):
    v=engine.retention;p=parent(engine);root=v.create(p)
    token=v.register_agent(p,root,"c"*32)
    for _ in range(16):await v.preview_delete(p,root)
    with pytest.raises(RetentionFailure,match="retention_limit"):await v.preview_delete(p,root)
    v.close();assert not v.pending and not v.timers
    v=Retention(engine.config,engine.security);engine.retention=v
    await delete(engine,root)
    v.close();v=Retention(engine.config,engine.security);engine.retention=v
    assert v.receipt(p,root)["agent_copies"][0]["state"]=="pending"
    assert v.agent_ack(root,"c"*32,token)["acknowledged"]


def test_vault_master_secret_is_minimized_even_when_only_in_environment(engine,monkeypatch):
    key=Fernet.generate_key().decode()
    monkeypatch.setenv("SWITCHBOARD_RETENTION_KEY",key)
    assert key in engine.text_gateway._local_identifiers()
    from switchboard.privacy import LocalTextMinimizer
    assert key not in LocalTextMinimizer(engine.text_gateway._local_identifiers()).minimize(("Credential "+key,)).texts[0]
