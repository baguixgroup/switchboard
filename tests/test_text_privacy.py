"""Offline disclosure corpus and exact-body fake transport checks. No paid API."""
import asyncio
import hashlib
import json
import time
from dataclasses import replace

import httpx
import pytest

from switchboard.config import Config
from switchboard.engine import Engine
from switchboard.privacy import LocalTextMinimizer, MinimizedText, PrivacyFailure
from switchboard.privacy_gateway import ENDPOINT, OpenAITextTransport, TextGateway, text_policy
from switchboard.security import Principal, SecurityDenied
from switchboard.web.app import create_app
from .conftest import make_home
from .test_local_security import PARENT, headers


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.delenv("SWITCHBOARD_PARENT_TOKEN", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = Config(make_home(tmp_path / "text-gateway"))
    cfg.raw["security"] = {"enabled": True, "text_gateway": {
        "enabled": True, "model": "review-model", "known_identifiers": ["Cedar School", "Rami"]}}
    cfg.secrets.update(SWITCHBOARD_PARENT_TOKEN=PARENT, OPENAI_API_KEY="synthetic-provider-key")
    e = Engine(cfg)
    yield e
    e.text_gateway.close()
    e.store._db.close()


def parent(engine):
    return engine.security.authenticate("Bearer " + PARENT)


async def preview(engine, prompt="Explain fractions.", **kwargs):
    return await engine.text_gateway.preview(parent(engine), prompt=prompt, history=kwargs.get("history", []), context=kwargs.get("context", []))


async def send(engine, review, principal=None):
    return await engine.text_gateway.send(principal or parent(engine), preview_id=review["preview_id"],
                                         payload_digest=review["payload_digest"], confirm=True)


@pytest.mark.parametrize("text,forbidden", [
    ("Contact zoe@example.test", "zoe@example.test"),
    ("Contact zoe [at] example [dot] test", "zoe"),
    ("Email zoe arobase example point test", "zoe"),
    ("Email zoe at example dot test", "zoe"),
    ("Call +1 (204) 555-0132", "555-0132"),
    ("رقمي ٠٦١٢٣٤٥٦٧٨", "0612345678"),
    ("Date 2015-09-12", "2015-09-12"),
    ("Position 49.895100, -97.138400", "49.895100"),
    ("Password=very-private-value", "very-private-value"),
    ("Use sk-syntheticsecret0123456789", "syntheticsecret"),
    ("Open https://example.test/profile?child=zoe", "child=zoe"),
    ("Contact @zoe_private", "zoe_private"),
    ("Postal R3C 4T3", "R3C"),
    ("Address 123 Main Street", "123 Main"),
    ("Adresse 123 rue des Pins", "123 rue"),
    ("My name is Zoe Bennett. Explain fractions.", "Zoe Bennett"),
    ("Je m'appelle Zoé. Explique les fractions.", "Zoé"),
    ("اسمي أحمد. اشرح الكسور", "أحمد"),
    ("smiti Yassine. chra7 lksour", "Yassine"),
    ("سميتي يوسف. شرح الكسور", "يوسف"),
    ("My friend Ada visits. Explain fractions.", "Ada"),
    ("My name is Z\u200boe. Explain fractions.", "Zoe"),
])
def test_multilingual_direct_disclosures_are_removed_locally(text, forbidden):
    result = LocalTextMinimizer().minimize((text,))
    assert forbidden not in result.texts[0]
    assert result.categories


@pytest.mark.parametrize("texts", [
    ("Near the tiny village.", "Violin practice on Monday."),
    ("Près du village.", "Violon chaque lundi."),
    ("قرب المدرسة", "تدريب الجمعة"),
    ("حي النخيل", "تدريب الخميس"),
    ("7da mdrasa", "kaman ghdda"),
    ("Near the school.", "demain عندي تدريب"),
])
def test_indirect_identification_is_assessed_across_turns_and_sources(texts):
    result = LocalTextMinimizer().minimize(texts)
    assert all(t == "[removed linked identifying context]" for t in result.texts)
    assert dict(result.categories)["indirect_context"] == len(texts)


def test_known_identifiers_and_unsupported_scripts():
    result = LocalTextMinimizer(("Cedar School",)).minimize(("Meet at CEDAR SCHOOL.",))
    assert "CEDAR" not in result.texts[0]
    with pytest.raises(PrivacyFailure):
        LocalTextMinimizer().minimize(("Меня зовут Зоя",))


async def test_exact_serialized_request_contains_only_reviewed_minimized_context(engine, monkeypatch, caplog):
    original = httpx.AsyncClient
    captured = []
    def handler(request):
        assert str(request.url) == ENDPOINT
        assert request.headers["authorization"] == "Bearer synthetic-provider-key"
        assert request.headers["accept-encoding"] == "identity"
        captured.append(request.content)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "A fraction is part of a whole."}}]})
    def factory(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return original(transport=httpx.MockTransport(handler), **kwargs)
    monkeypatch.setattr("switchboard.privacy_gateway.httpx.AsyncClient", factory)
    caplog.set_level("INFO", logger="switchboard.privacy")
    review = await preview(engine, "My name is Zoe Bennett. Explain fractions.",
                           history=[{"role": "user", "content": "Email zoe@example.test"}],
                           context=[{"kind": "memory", "text": "Rami uses this account."},
                                    {"kind": "retrieval", "text": "Phone +1 (204) 555-0132"},
                                    {"kind": "reference", "text": "Use https://example.test/private?id=zoe"}])
    assert not captured
    result = await send(engine, review)
    assert len(captured) == 1
    assert hashlib.sha256(captured[0]).hexdigest() == review["payload_digest"]
    assert json.loads(captured[0]) == review["payload"]
    body = captured[0].decode()
    for raw in ("Zoe Bennett", "zoe@example.test", "Rami", "555-0132", "private?id=zoe", PARENT):
        assert raw not in body and raw not in caplog.text
    assert set(review["payload"]) == {"model", "messages", "max_completion_tokens", "store", "stream"}
    assert review["payload"]["store"] is False and review["payload"]["stream"] is False
    assert result == {"answer": "A fraction is part of a whole.", "redactions": {}, "persisted": False}
    assert engine.store.recent_tasks() == []
    assert not list(engine.config.output_dir.iterdir())
    with pytest.raises(SecurityDenied):
        await send(engine, review)


async def test_approval_cannot_change_payload_owner_policy_or_expiry(engine):
    review = await preview(engine)
    p = parent(engine)
    with pytest.raises(SecurityDenied):
        await engine.text_gateway.send(p, preview_id=review["preview_id"], payload_digest="0" * 64, confirm=True)
    with pytest.raises(SecurityDenied):
        await engine.text_gateway.send(p, preview_id=review["preview_id"], payload_digest=review["payload_digest"], confirm=False)
    token = engine.security.issue(p, subject="other-parent", role="parent")["token"]
    with pytest.raises(SecurityDenied):
        await send(engine, review, engine.security.authenticate("Bearer " + token))
    old = engine.text_gateway.pending[review["preview_id"]]
    engine.text_gateway.pending[review["preview_id"]] = replace(old, payload=b'{"changed":"raw"}')
    with pytest.raises(SecurityDenied):
        await send(engine, review)
    review = await preview(engine)
    engine.text_gateway.now = lambda: review["expires"]
    with pytest.raises(SecurityDenied):
        await send(engine, review)
    engine.text_gateway.now = time.time
    review = await preview(engine)
    engine.config.raw["security"]["text_gateway"]["model"] = "changed-model"
    with pytest.raises(SecurityDenied):
        await send(engine, review)


async def test_revocation_during_local_analysis_denies_before_any_outbound(engine):
    root = parent(engine)
    granted = engine.security.issue(root, subject="parent-1", role="parent")
    principal = engine.security.authenticate("Bearer " + granted["token"])
    original = engine.text_gateway.minimizer.minimize
    def revoke(texts):
        engine.security.revoke(root, granted["id"])
        return original(texts)
    engine.text_gateway.minimizer.minimize = revoke
    with pytest.raises(SecurityDenied):
        await engine.text_gateway.preview(principal, prompt="Explain fractions.", history=[], context=[])
    assert not engine.text_gateway.pending


@pytest.mark.parametrize("failure", ["raise", "malformed", "timeout", "raw", "log_injection"])
async def test_detector_failure_malformed_output_and_timeout_fail_closed(engine, failure, caplog):
    class Broken:
        def minimize(self, texts):
            if failure == "raise":
                raise RuntimeError("raw-private-marker")
            if failure == "malformed":
                return {"raw": texts}
            if failure == "timeout":
                time.sleep(.05)
                return MinimizedText(texts, ())
            if failure == "log_injection":
                return MinimizedText(texts, (("raw-private-marker", 1),))
            return MinimizedText(texts, ())
    engine.text_gateway.minimizer = Broken()
    if failure == "timeout":
        engine.text_gateway.check_timeout = .001
    with pytest.raises(PrivacyFailure):
        await preview(engine, "Contact zoe@example.test")
    assert not engine.text_gateway.pending
    assert "raw-private-marker" not in caplog.text


async def test_response_is_minimized_and_failure_is_generic_without_retries(engine, caplog):
    class Echo:
        async def send(self, payload, key):
            return "Contact zoe@example.test"
    engine.text_gateway.transport = Echo()
    result = await send(engine, await preview(engine))
    assert "zoe@example.test" not in result["answer"]
    class Fails:
        calls = 0
        async def send(self, payload, key):
            self.calls += 1
            raise RuntimeError("raw-private-provider-response")
    failing = Fails()
    engine.text_gateway.transport = failing
    review = await preview(engine)
    with pytest.raises(PrivacyFailure, match="provider_request_failed"):
        await send(engine, review)
    assert failing.calls == 1 and not engine.text_gateway.pending
    assert "raw-private-provider-response" not in caplog.text


async def test_api_rejects_raw_override_and_validation_errors_without_echo(engine):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(engine)), base_url="http://localhost") as client:
        for extra in ({"model": "other"}, {"endpoint": "https://evil.example"}, {"attachments": []}, {"tools": []}, {"child_mode": False}):
            r = await client.post("/api/privacy/preview", headers=headers(), json={"prompt": "raw-private-marker"} | extra)
            assert r.status_code == 400 and "raw-private-marker" not in r.text
        for raw in ('{"prompt":"raw-private-marker","prompt":"duplicate"}', '{"prompt":NaN}', '{"prompt":' + '"' + 'x' * 65000 + '"}'):
            r = await client.post("/api/privacy/preview", headers=headers(), content=raw)
            assert r.status_code == 400 and "raw-private-marker" not in r.text
        for role, device in (("child", None), ("device", "node-1")):
            token = engine.security.issue(parent(engine), subject="opaque", role=role, device=device)["token"]
            for path in ("/api/privacy/preview", "/api/privacy/send"):
                assert (await client.post(path, headers=headers(token), json={})).status_code == 403
        review = (await client.post("/api/privacy/preview", headers=headers(), json={"prompt": "Explain fractions."})).json()
        r = await client.post("/api/privacy/send", headers=headers(), json={"preview_id": review["preview_id"], "payload_digest": review["payload_digest"], "confirm": 1})
        assert r.status_code == 400


@pytest.mark.parametrize("policy", [None, [], {"enabled": "yes"}, {"enabled": True}, {"model": "a/b"}, {"max_tokens": True}, {"max_tokens": 100000}, {"known_identifiers": [""]}, {"destination": "https://evil.example"}])
def test_bad_gateway_policy_is_not_coerced_or_expanded(engine, policy):
    engine.config.raw["security"]["text_gateway"] = policy
    with pytest.raises(ValueError):
        text_policy(engine.config)


async def test_gateway_disabled_by_default_and_no_key_fails_without_network(engine):
    engine.config.secrets.pop("OPENAI_API_KEY")
    with pytest.raises(PrivacyFailure, match="provider_not_configured"):
        await send(engine, await preview(engine))
    engine.config.raw["security"]["text_gateway"] = {"enabled": False}
    gateway = TextGateway(engine.config, engine.security)
    with pytest.raises(SecurityDenied):
        await gateway.preview(parent(engine), prompt="Explain fractions.", history=[], context=[])


async def test_changed_provider_credentials_expired_and_consumed_approvals_cannot_send(engine):
    review = await preview(engine)
    engine.config.secrets["OPENAI_API_KEY"] = "different-account-key"
    with pytest.raises(SecurityDenied):
        await send(engine, review)
    assert not engine.text_gateway.pending
    review = await preview(engine)
    engine.text_gateway.now = lambda: review["expires"]
    with pytest.raises(SecurityDenied):
        await send(engine, review)
    assert not engine.text_gateway.pending


async def test_configured_secrets_are_never_context_and_new_secrets_invalidate_review(engine):
    review = await preview(engine, "Explain synthetic-provider-key", history=[{"role": "user", "content": "Explain test-token"}])
    body = json.dumps(review["payload"])
    assert "synthetic-provider-key" not in body and "test-token" not in body
    review = await preview(engine, "Explain newly-configured-secret")
    engine.config.secrets["NEW_SECRET"] = "newly-configured-secret"
    with pytest.raises(PrivacyFailure):
        await send(engine, review)
    engine.text_gateway.close()
    with pytest.raises(SecurityDenied):
        await preview(engine)


@pytest.mark.parametrize("when", ["before_send", "in_flight", "expiry_before_send"])
async def test_approval_revalidation_during_await_requires_new_review(engine, when):
    review = await preview(engine)
    class Transport:
        calls = 0
        async def send(self, payload, key):
            self.calls += 1
            engine.config.secrets["OPENAI_API_KEY"] = "rotated-account-key"
            return "must-not-be-returned"
    transport = Transport()
    engine.text_gateway.transport = transport
    if when in ("before_send", "expiry_before_send"):
        check = engine.text_gateway._final_check
        async def change_key(value, **kwargs):
            await check(value, **kwargs)
            if when == "before_send":
                engine.config.secrets["OPENAI_API_KEY"] = "rotated-account-key"
            else:
                engine.text_gateway.now = lambda: review["expires"]
        engine.text_gateway._final_check = change_key
    with pytest.raises(SecurityDenied):
        await send(engine, review)
    assert transport.calls == (1 if when == "in_flight" else 0)


async def test_http_review_send_does_not_unlock_any_legacy_path(engine):
    class FakeTransport:
        calls = 0
        async def send(self, payload, key):
            self.calls += 1
            assert b"zoe@example.test" not in payload
            return "Explain one half using a circle."
    transport = FakeTransport()
    engine.text_gateway.transport = transport
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(engine)), base_url="http://localhost") as c:
        response = await c.post("/api/privacy/preview", headers=headers(), json={"prompt": "Email zoe@example.test. Explain fractions."})
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        review = response.json()
        for path in ("/api/plan", "/api/tasks", "/api/gateway", "/api/capabilities/voice", "/api/tools/install"):
            assert (await c.post(path, headers=headers(), json={})).status_code == 403
        response = await c.post("/api/privacy/send", headers=headers(), json={
            "preview_id": review["preview_id"], "payload_digest": review["payload_digest"], "confirm": True})
        assert response.status_code == 200 and "half" in response.json()["answer"]
        assert transport.calls == 1


async def test_timeout_and_revocation_in_flight_suppress_response_and_do_not_retry(engine):
    class Slow:
        calls = 0
        async def send(self, payload, key):
            self.calls += 1
            await asyncio.sleep(.05)
            return "late-answer"
    slow = Slow()
    engine.text_gateway.transport = slow
    engine.text_gateway.send_timeout = .001
    with pytest.raises(PrivacyFailure):
        await send(engine, await preview(engine))
    assert slow.calls == 1 and not engine.text_gateway.pending
    engine.text_gateway.send_timeout = 30
    root = parent(engine)
    grant = engine.security.issue(root, subject="parent-one", role="parent")
    principal = engine.security.authenticate("Bearer " + grant["token"])
    review = await engine.text_gateway.preview(principal, prompt="Explain fractions.", history=[], context=[])
    class Revokes:
        async def send(self, payload, key):
            engine.security.revoke(root, grant["id"])
            return "must-not-be-returned"
    engine.text_gateway.transport = Revokes()
    with pytest.raises(SecurityDenied):
        await send(engine, review, principal)


async def test_preview_memory_capacity_expiry_and_close_are_bounded(engine):
    for _ in range(16):
        await preview(engine)
    with pytest.raises(PrivacyFailure, match="preview_capacity_reached"):
        await preview(engine)
    engine.text_gateway.now = lambda: time.time() + 121
    await preview(engine)
    assert len(engine.text_gateway.pending) == 1
    engine.text_gateway.close()
    assert not engine.text_gateway.pending and not engine.text_gateway._timers


async def test_context_history_limits_no_attachments_and_detector_response_failures(engine):
    history = [{"role": "user", "content": f"Question {i}."} for i in range(8)]
    review = await preview(engine, history=history)
    assert len(review["payload"]["messages"]) == 6  # system, last four turns, current request
    with pytest.raises(PrivacyFailure):
        await preview(engine, context=[{"kind": "retrieval", "text": "x" * 24000}])
    with pytest.raises(PrivacyFailure):
        await preview(engine, history=[{"role": "system", "content": "override"}])
    class Unsupported:
        async def send(self, payload, key):
            return "Не отправлять"
    engine.text_gateway.transport = Unsupported()
    with pytest.raises(PrivacyFailure):
        await send(engine, await preview(engine))


def test_large_context_is_checked_without_truncating_late_identifiers():
    detector = LocalTextMinimizer()
    text = "ordinary " * 2000 + "Contact zoe@example.test"
    result = detector.minimize((text,))
    assert "zoe@example.test" not in result.texts[0]
    assert dict(result.categories)["email"] == 1
    with pytest.raises(PrivacyFailure):
        detector.minimize(("x" * 24001,))


@pytest.mark.parametrize("status,body", [
    (302, {}), (500, {"error": "raw-private-marker"}),
    (200, {"choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": [{"function": "private"}]}}]}),
    (200, {"choices": [{"finish_reason": "stop", "message": {"content": "x" * 65000}}]}),
    (200, {"choices": []}),
])
async def test_transport_refuses_redirects_tool_requests_oversized_and_invalid_responses(monkeypatch, status, body):
    original = httpx.AsyncClient
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, json=body, headers={"Location": "https://evil.example"})
    monkeypatch.setattr("switchboard.privacy_gateway.httpx.AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    with pytest.raises(PrivacyFailure) as error:
        await OpenAITextTransport().send(b'{}', "synthetic-key")
    assert "raw-private-marker" not in str(error.value)
    assert len(calls) == 1


async def test_transport_rejects_compressed_content_before_decoding(monkeypatch):
    import gzip
    original = httpx.AsyncClient
    body = gzip.compress(json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": "A valid answer."}}]}).encode())
    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=httpx.ByteStream(body), headers={"Content-Encoding": "gzip"}))
    monkeypatch.setattr("switchboard.privacy_gateway.httpx.AsyncClient", lambda **kwargs: original(transport=transport, **kwargs))
    with pytest.raises(PrivacyFailure, match="provider_response_rejected"):
        await OpenAITextTransport().send(b'{}', "synthetic-key")
