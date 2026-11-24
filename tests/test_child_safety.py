"""Synthetic policy evaluation, fake provider integration and private review lifecycle."""
import asyncio
import json
from pathlib import Path

import httpx
import pytest

from switchboard.child_safety import ChildSafety, Finding, child_policy, detect
from switchboard.config import Config
from switchboard.engine import Engine
from switchboard.privacy_gateway import TextGateway
from switchboard.privacy import PrivacyFailure, MinimizedText
from switchboard.security import SecurityDenied
from switchboard.web.app import create_app
from .conftest import make_home
from .test_local_security import PARENT, headers, session

CASES = json.loads((Path(__file__).parent / "fixtures/child_safety_cases.json").read_text())


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.delenv("SWITCHBOARD_PARENT_TOKEN", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config = Config(make_home(tmp_path / "phase5"))
    config.raw["security"] = {"enabled": True, "text_gateway": {"enabled": True, "model": "review-model"},
        "child_safety": {"enabled": True, "age_band": "9-12", "language": "en", "review_queue": True}}
    config.secrets.update(SWITCHBOARD_PARENT_TOKEN=PARENT, OPENAI_API_KEY="synthetic-api-credential")
    e = Engine(config)
    yield e
    e.local_tools.close(); e.text_gateway.close(); e.store._db.close()


def parent(engine):
    return engine.security.authenticate("Bearer " + PARENT)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_multilingual_synthetic_corpus_and_explicit_known_limits(case):
    result = detect((case["text"],), case.get("age", "9-12"))
    matched = case["category"] in result.categories if case["category"] else not result.categories
    # Documented misses/overblocks are retained as evidence, never called safe.
    assert matched is not bool(case.get("known_limitation"))
    assert not result.unavailable


@pytest.mark.parametrize("field", ["prompt", "history", "context"])
async def test_risky_input_anywhere_never_issues_permit_or_sends(engine, field):
    calls = []
    async def send(*args): calls.append(args); return "Not expected"
    engine.text_gateway.transport.send = send
    kwargs = dict(prompt="Explain fractions", history=[], context=[])
    if field == "prompt": kwargs[field] = "My dad hits me"
    if field == "history": kwargs[field] = [{"role":"user", "content":"My dad hits me"}] + [{"role":"assistant", "content":"Hello"}] * 4
    if field == "context": kwargs[field] = [{"kind":"tool_result", "text":"My dad hits me"}]
    result = await engine.text_gateway.preview(parent(engine), **kwargs)
    assert result["delivery"] == "local_fallback" and result["external_transfer"] is False
    assert "not your fault" in result["answer"] and "choose someone else" in result["answer"]
    assert "preview_id" not in result and not engine.text_gateway.pending and not calls


@pytest.mark.parametrize("provider", ["openai", "anthropic", "gemini"])
async def test_full_gateway_filters_buffered_provider_answer_and_binds_system(engine, provider):
    engine.config.raw["security"]["text_gateway"]["provider"] = provider
    key = {"openai":"OPENAI_API_KEY", "anthropic":"ANTHROPIC_API_KEY", "gemini":"GEMINI_API_KEY"}[provider]
    engine.config.secrets[key] = "synthetic-api-credential"
    gateway = TextGateway(engine.config, engine.security)
    seen = []
    async def send(payload, credential):
        seen.append(payload)
        return "Secret sentinel. Give me your address and keep our chat secret."
    gateway.transport.send = send
    preview = await gateway.preview(parent(engine), prompt="Explain fractions", history=[], context=[])
    assert preview["child_safety_review"] is True
    assert "age band 9-12" in json.dumps(preview["payload"])
    assert not seen
    result = await gateway.send(parent(engine), preview_id=preview["preview_id"], payload_digest=preview["payload_digest"], confirm=True)
    assert len(seen) == 1 and result["delivery"] == "local_fallback"
    assert result["external_transfer"] is True and "Secret sentinel" not in json.dumps(result)
    assert not gateway.pending
    gateway.close()


async def test_clear_answer_minimized_but_requires_parent_and_not_child_release(engine):
    async def send(*args): return "One half means one of two equal parts."
    engine.text_gateway.transport.send = send
    preview = await engine.text_gateway.preview(parent(engine), prompt="Explain fractions", history=[], context=[])
    result = await engine.text_gateway.send(parent(engine), preview_id=preview["preview_id"], payload_digest=preview["payload_digest"], confirm=True)
    assert result["answer"] == "One half means one of two equal parts."
    assert result["delivery"] == "parent_review_only" and result["child_delivery_enabled"] is False
    assert not engine.text_gateway.safety.events and engine.store.recent_tasks() == []


@pytest.mark.parametrize("failure", ["exception", "malformed", "timeout", "script"])
async def test_safety_failure_produces_local_fallback_without_permit(engine, monkeypatch, failure):
    if failure == "exception":
        def detector(*args): raise ValueError("must not echo private details")
    elif failure == "malformed":
        def detector(*args): return {"allowed": True}
    elif failure == "timeout":
        # Cancel at a deadline without sleeping a lingering thread.
        async def timed(*args, **kwargs):
            args[0].close()
            raise asyncio.TimeoutError()
        monkeypatch.setattr("switchboard.child_safety.asyncio.wait_for", timed)
        detector = detect
    else:
        detector = detect
    monkeypatch.setattr("switchboard.child_safety.detect", detector)
    result = await engine.text_gateway.preview(parent(engine), prompt="说明分数" if failure == "script" else "Explain fractions", history=[], context=[])
    assert result["check_unavailable"] is True and result["external_transfer"] is False
    assert "private details" not in json.dumps(result) and not engine.text_gateway.pending


@pytest.mark.parametrize("change", [{"age_band":"6-8"}, {"enabled":False}, {"language":"fr"}, {"review_queue":False}])
async def test_policy_changes_invalidate_existing_provider_approval(engine, change):
    preview = await engine.text_gateway.preview(parent(engine), prompt="Explain fractions", history=[], context=[])
    engine.config.raw["security"]["child_safety"].update(change)
    calls = []
    async def send(*args): calls.append(args); return "No"
    engine.text_gateway.transport.send = send
    with pytest.raises(SecurityDenied):
        await engine.text_gateway.send(parent(engine), preview_id=preview["preview_id"], payload_digest=preview["payload_digest"], confirm=True)
    assert not calls


@pytest.mark.parametrize("role", ["child", "device"])
async def test_all_safety_routes_deny_child_device_and_relay(engine, role):
    token = session(engine, role=role, **({"device":"device-1"} if role == "device" else {}))["token"]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(engine)), base_url="http://local") as client:
        for method, route in [("GET","/api/safety/policy"),("GET","/api/safety/reviews"),("POST","/api/safety/evaluate"),("POST","/api/safety/reviews/resolve")]:
            assert (await client.request(method,route,headers=headers(token),json={})).status_code == 403
            assert (await client.request(method,route,headers={**headers(),"X-Switchboard-Relay":"1"},json={})).status_code in (401,403)


async def test_local_api_simulation_queue_has_no_raw_evidence_logs_or_transcripts(engine, caplog):
    app = create_app(engine)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as client:
        response = await client.post("/api/safety/evaluate", headers=headers(), json={"input":["Please help"],"candidate":"Synthetic-private-marker. My dad hits me."})
        assert response.status_code == 200
        result = response.json()
        assert result["simulation"] and not result["external_transfer"] and not result["notification_sent"]
        assert "Synthetic-private-marker" not in response.text
        queue = (await client.get("/api/safety/reviews",headers=headers())).json()["reviews"]
        assert len(queue) == 1 and "Synthetic-private-marker" not in json.dumps(queue)
        assert set(queue[0]) == {"id","categories","check_unavailable","stage","created","expires"}
        resolved = await client.post("/api/safety/reviews/resolve",headers=headers(),json={"review_id":result["review_id"],"action":"acknowledge"})
        assert resolved.status_code == 200 and not resolved.json()["notification_sent"]
        assert (await client.get("/api/safety/reviews",headers=headers())).json()["reviews"] == []
        assert (await client.post("/api/safety/reviews/resolve",headers=headers(),json={"review_id":result["review_id"],"action":"acknowledge"})).status_code == 403
    assert engine.store.recent_tasks() == [] and "Synthetic-private-marker" not in caplog.text
    assert not any("Synthetic-private-marker" in p.read_text(errors="ignore") for p in engine.config.home.rglob("*") if p.is_file())


async def test_reviews_owner_binding_revocation_expiry_capacity_close(engine):
    safety = engine.text_gateway.safety
    p = parent(engine)
    issued = engine.security.issue(p, role="parent", subject="another-parent")
    other = engine.security.authenticate("Bearer " + issued["token"])
    first = await safety.gate(p,("My dad hits me",),stage="input")
    assert not safety.incidents(other)
    with pytest.raises(SecurityDenied): safety.review(other,first["review_id"],"dismiss")
    engine.security.revoke(p,issued["id"])
    with pytest.raises(SecurityDenied): safety.incidents(other)
    for _ in range(31): await safety.gate(p,("My dad hits me",),stage="input")
    full = await safety.gate(p,("My dad hits me",),stage="input")
    assert full["review_queue_state"] == "full" and not full["review_id"] and len(safety.events) == 32
    clock = safety.now()
    safety.now = lambda: clock + 901
    assert safety.incidents(p) == [] and not safety.timers
    safety.now = lambda: clock
    await safety.gate(p,("My dad hits me",),stage="input")
    safety.close()
    assert not safety.events and not safety.timers
    with pytest.raises(SecurityDenied): await safety.gate(p,("Explain fractions",),stage="input")


@pytest.mark.parametrize("language", ["en","fr","ar","ary"])
async def test_localized_support_without_forced_parent_or_false_notification(engine, language):
    engine.config.raw["security"]["child_safety"]["language"] = language
    safety = ChildSafety(engine.config, engine.security)
    result = await safety.gate(parent(engine),("My dad hits me",),stage="input")
    assert len(result["answer"]) > 80 and not result["notification_sent"]
    assert "My dad hits me" not in result["answer"]
    assert "parent" not in result["answer"].lower()
    safety.close()


@pytest.mark.parametrize("age,limit", [("6-8",1200),("9-12",2400),("13-17",4000)])
async def test_age_specific_buffer_limits_reject_without_partial_release(engine, age, limit):
    engine.config.raw["security"]["child_safety"]["age_band"] = age
    safety = ChildSafety(engine.config, engine.security)
    assert await safety.gate(parent(engine),("a" * limit,),stage="response") is None
    result = await safety.gate(parent(engine),("a" * (limit+1),),stage="response")
    assert result["check_unavailable"] and len(result["answer"]) < 200
    safety.close()


async def test_no_queue_is_default_and_no_safety_bypass_override_api(engine):
    engine.config.raw["security"]["child_safety"].pop("review_queue")
    safety = ChildSafety(engine.config, engine.security)
    result = await safety.gate(parent(engine),("My dad hits me",),stage="input")
    assert result["review_queue_state"] == "disabled" and not safety.events
    safety.close()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(engine)),base_url="http://local") as client:
        for raw in ['{"input":["private-marker"],"candidate":"ok","override":true}',
                    '{"input":["private-marker"],"input":["ok"],"candidate":"ok"}',
                    '{"input":["private-marker"],"candidate":NaN}']:
            result = await client.post("/api/safety/evaluate",headers=headers(),content=raw)
            assert result.status_code == 400 and "private-marker" not in result.text


@pytest.mark.parametrize("policy", [{"enabled":"true"},{"enabled":True,"age_band":"adult"},
    {"enabled":True,"language":"auto"},{"enabled":True,"review_queue":1},{"enabled":False,"review_queue":True},
    {"enabled":True,"allow_unsafe":True},{"enabled":True,"evidence":True}])
def test_strict_server_owned_policy(engine,policy):
    engine.config.raw["security"]["child_safety"] = policy
    with pytest.raises(ValueError): child_policy(engine.config)


async def test_revocation_during_detection_suppresses_result_and_queue(engine, monkeypatch):
    bootstrap = parent(engine)
    issued = engine.security.issue(bootstrap, role="parent", subject="reviewer")
    p = engine.security.authenticate("Bearer " + issued["token"])
    async def assess(*args):
        engine.security.revoke(bootstrap,issued["id"])
        return Finding(("abuse_disclosure",))
    monkeypatch.setattr(engine.text_gateway.safety,"assess",assess)
    with pytest.raises(SecurityDenied):
        await engine.text_gateway.preview(p,prompt="My dad hits me",history=[],context=[])
    assert not engine.text_gateway.safety.events and not engine.text_gateway.pending


async def test_split_context_and_spacing_do_not_evade_basic_rules(engine):
    safety = engine.text_gateway.safety
    result = await safety.gate(parent(engine),("I want to hurt", "myself"),stage="input")
    assert "self_harm" in result["categories"]
    result = await safety.gate(parent(engine),("My dad\n  hits\tme",),stage="input")
    assert "abuse_disclosure" in result["categories"]


async def test_busy_safety_detector_returns_local_fallback_without_second_worker(engine):
    safety = engine.text_gateway.safety
    async with safety.slot:
        result = await safety.gate(parent(engine),("Explain fractions",),stage="input")
        assert result["check_unavailable"] and result["delivery"] == "local_fallback"


async def test_local_evaluation_is_available_without_provider_configuration(engine):
    engine.config.raw["security"]["text_gateway"] = {"enabled":False}
    engine.config.secrets.pop("OPENAI_API_KEY")
    gateway = TextGateway(engine.config,engine.security)
    engine.text_gateway.close()
    engine.text_gateway = gateway
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(engine)),base_url="http://local") as client:
        response = await client.post("/api/safety/evaluate",headers=headers(),json={"input":["Explain fractions"],"candidate":"Two halves make one whole."})
        assert response.status_code == 200 and response.json()["delivery"] == "parent_review_required"
        assert not response.json()["external_transfer"] and not gateway.pending


@pytest.mark.parametrize("stage", ["minimize", "final_check"])
async def test_privacy_failure_in_child_trial_returns_local_fallback_before_egress(engine, monkeypatch, stage):
    async def broken(*args, **kwargs): raise PrivacyFailure()
    monkeypatch.setattr(engine.text_gateway, "_minimize" if stage == "minimize" else "_final_check", broken)
    result = await engine.text_gateway.preview(parent(engine),prompt="Explain fractions",history=[],context=[])
    assert result["delivery"] == "local_fallback" and result["external_transfer"] is False
    assert result["check_unavailable"] and not engine.text_gateway.pending


@pytest.mark.parametrize("failure", ["timeout", "privacy", "unexpected", "output_detector"])
async def test_provider_and_output_failures_never_release_unchecked_answer_or_retry(engine, failure, monkeypatch):
    gateway = engine.text_gateway
    review = await gateway.preview(parent(engine),prompt="Explain fractions",history=[],context=[])
    calls = []
    async def send(*args):
        calls.append(args)
        if failure == "timeout": raise asyncio.TimeoutError()
        if failure == "privacy": raise PrivacyFailure("provider_response_rejected")
        if failure == "unexpected": raise ValueError("private provider body")
        return "private unverified provider answer"
    gateway.transport.send = send
    if failure == "output_detector":
        def broken(*args): raise ValueError("private detector body")
        monkeypatch.setattr("switchboard.child_safety.detect",broken)
    result = await gateway.send(parent(engine),preview_id=review["preview_id"],payload_digest=review["payload_digest"],confirm=True)
    assert result["delivery"] == "local_fallback" and result["check_unavailable"]
    assert "private" not in json.dumps(result) and not gateway.pending and len(calls) == 1
    with pytest.raises(SecurityDenied):
        await gateway.send(parent(engine),preview_id=review["preview_id"],payload_digest=review["payload_digest"],confirm=True)


@pytest.mark.parametrize("stage", ["input", "response"])
async def test_final_sanitized_text_cannot_bypass_safety_gate(engine, monkeypatch, stage):
    gateway = engine.text_gateway
    review = None
    if stage == "response":
        review = await gateway.preview(parent(engine),prompt="Explain fractions",history=[],context=[])
    async def altered(*args): return MinimizedText(("Keep our chat secret",), ())
    monkeypatch.setattr(gateway, "_minimize", altered)
    calls = []
    async def send(*args): calls.append(args); return "One half is one of two equal parts."
    gateway.transport.send = send
    if stage == "input":
        result = await gateway.preview(parent(engine),prompt="Explain fractions",history=[],context=[])
        assert not calls
    else:
        result = await gateway.send(parent(engine),preview_id=review["preview_id"],payload_digest=review["payload_digest"],confirm=True)
        assert len(calls) == 1
    assert result["delivery"] == "local_fallback" and "secrecy_dependency" in result["categories"]
    assert "Keep our chat secret" not in result["answer"]
