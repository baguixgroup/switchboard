"""Writing services must remain explicit, preserve source text and accept reviewed results."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from switchboard.catalog import Catalog
from switchboard.config import Config, load_default
from switchboard.credits import Ledger
from switchboard.engine import Engine
from switchboard.providers.base import RunRequest
from switchboard.providers.handoff import HandoffProvider
from switchboard.router import Router, classify
from switchboard.store import Store
from switchboard.web.app import create_app

from .conftest import MOCK_MODELS, make_home


WRITERS = [m for m in load_default("models.yaml")["models"] if "writing" in m.get("features", [])]


@pytest.mark.parametrize("entry", WRITERS)
async def test_writing_handoff_preserves_source(entry, tmp_path):
    cfg = Config(make_home(tmp_path))
    model = Catalog([entry]).all()[0]
    provider = HandoffProvider(model.provider, cfg.providers[model.provider], cfg)
    source = "  café: i has two apple.\nDo not change the name: A&B.\n"
    result = await provider.run(RunRequest(prompt="Proofread.\n\nText to edit:\n" + source, model=model))
    assert result.handoff["prompt"] == source
    assert result.handoff["task_type"] == "writing"
    assert "manual handoff" in result.text
    assert "create the image" not in result.text
    assert result.cost_usd == 0


def test_automatic_routing_never_uses_manual_writing_services(tmp_path):
    cfg = Config(make_home(tmp_path))
    catalog = Catalog(MOCK_MODELS + WRITERS)
    router = Router(catalog, Ledger(cfg, Store(cfg.db_path)))
    available = {"mock", *(m["provider"] for m in WRITERS)}
    plan = router.plan("Proofread my grammar", available, classify("Proofread my grammar"))
    assert plan.provider == "mock"
    explicit = router.plan("Proofread my grammar", available, classify("Proofread my grammar"),
                           model="grammarly-app:writing")
    assert explicit.provider == "grammarly-app"


async def test_writing_result_auth_validation_and_persistence(tmp_path, monkeypatch):
    cfg = Config(make_home(tmp_path / "hub", providers={"mock": {"kind": "mock"}, "grammarly-app": {"disabled": False}}))
    engine = Engine(cfg)
    plan = await engine.plan("Proofread my spelling", model="grammarly-app:writing")
    task = await engine.run_task("writing1", "Proofread.\n\nText to edit:\ni has apples.", plan, [], [])
    assert task["status"] == "waiting"
    jobs = []

    async def fake_submit(node, kind, args, **kwargs):
        jobs.append(kind)
        return {"copied": True, "opened": "app"}

    monkeypatch.setattr(engine.nodes, "submit", fake_submit)
    await engine.handoff_open("writing1", "mac")
    await asyncio.sleep(0)
    assert jobs == ["open_app"]  # writing must not start a background image watcher
    with TestClient(create_app(engine), client=("203.0.113.9", 5000)) as client:
        endpoint = "/api/tasks/writing1/handoff/text"
        assert client.post(endpoint, json={"text": "I have apples."}).status_code == 401
        client.headers["Authorization"] = "Bearer test-token"
        assert client.post(endpoint, json={"text": "  "}).status_code == 400
        assert client.post(endpoint, json={"text": "x" * 200_001}).status_code == 422
        assert client.post(endpoint, json={"text": "I have apples."}).status_code == 200
        assert client.post(endpoint, json={"text": "overwrite"}).status_code == 400
        done = client.get("/api/tasks/writing1").json()
        assert done["status"] == "done" and done["result"] == "I have apples."
        assert (engine._task_dir("writing1") / "response.md").read_text() == "I have apples."
        assert client.post("/api/tasks/missing/handoff/text", json={"text": "result"}).status_code == 400
