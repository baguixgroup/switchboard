"""Image tasks on ChatGPT/Grok plans (handoff + folder watcher), adding files, shared recent work."""
import asyncio
import base64
import time

import uvicorn
import yaml
from fastapi.testclient import TestClient

from switchboard.agent import run_agent
from switchboard.catalog import Catalog, ModelInfo
from switchboard.config import Config
from switchboard.credits import Ledger
from switchboard.engine import Engine
from switchboard.executor import Executor
from switchboard.providers import RunRequest
from switchboard.providers.cli_bridge import CLIProvider
from switchboard.providers.handoff import image_prompt
from switchboard.router import Router, classify
from switchboard.store import Store
from switchboard.web.app import create_app

from .conftest import MOCK_MODELS, free_port, make_home

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==")
IMAGE_MODELS = [
    {"provider": "openai", "id": "gpt-image-1", "q": 9, "price": [0, 0], "per_call": 0.06, "features": ["image_gen"]},
    {"provider": "chatgpt-app", "id": "images", "label": "ChatGPT images", "q": 9, "price": [0, 0], "features": ["image_gen", "handoff"]},
]
HANDOFF = {"chatgpt-app": {"kind": "handoff", "label": "ChatGPT", "url": "https://chatgpt.com/"}}


def test_image_prompt_keeps_the_users_words():
    assert image_prompt("Generate an image of a neon fox logo on black") == "A neon fox logo on black"
    assert image_prompt("a watercolor of Lisbon at dusk") == "A watercolor of Lisbon at dusk"


def test_image_tasks_go_to_the_plan_unless_cost_doesnt_matter(tmp_path):
    cfg = Config(make_home(tmp_path / "h", models=IMAGE_MODELS + MOCK_MODELS))
    r = Router(Catalog(IMAGE_MODELS + MOCK_MODELS), Ledger(cfg, Store(cfg.db_path)))
    prof = lambda: classify("generate an image of a fox logo")  # noqa: E731
    avail = {"openai", "chatgpt-app", "mock"}
    assert r.plan("x", avail, prof(), billing="plans_first").provider == "chatgpt-app"
    assert r.plan("x", avail, prof(), billing="plans_only").provider == "chatgpt-app"
    assert r.plan("x", avail, prof(), billing="any").provider == "openai"


async def test_handoff_open_watch_and_attach(tmp_path, monkeypatch):
    async def fake_open(self, h):  # don't launch real apps in tests
        return {"opened": "app", "copied": True, "message": f"Opened {h['label']}"}
    monkeypatch.setattr(Executor, "open_app", fake_open)
    port = free_port()
    hub = Config(make_home(tmp_path / "hub", name="pi", port=port, providers={"mock": {"kind": "mock"}, **HANDOFF},
                           models=IMAGE_MODELS + MOCK_MODELS))
    agent_home = make_home(tmp_path / "agent", role="agent", name="win-pc", hub_url=f"http://127.0.0.1:{port}")
    raw = yaml.safe_load((agent_home / "config.yaml").read_text())
    raw["node"]["watch_folder"] = str(tmp_path / "Downloads")
    (agent_home / "config.yaml").write_text(yaml.safe_dump(raw))
    (tmp_path / "Downloads").mkdir()
    engine = Engine(hub)
    server = uvicorn.Server(uvicorn.Config(create_app(engine), host="127.0.0.1", port=port, log_config=None))
    st = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    at = asyncio.create_task(run_agent(Config(agent_home)))
    try:
        for _ in range(100):
            if "win-pc" in engine.nodes.nodes:
                break
            await asyncio.sleep(0.2)
        plan = await engine.plan("generate an image of a neon fox logo")
        assert plan.provider == "chatgpt-app"
        task = await engine.run_task("img1", "generate an image of a neon fox logo", plan, [], [])
        assert task["status"] == "waiting" and task["plan"]["handoff"]["prompt"] == "A neon fox logo"
        second = await engine.run_task("img2", "generate an image of a neon fox logo", plan, [], [])
        assert second["plan"]["handoff"]["prompt"] == "A neon fox logo"  # no earlier-work note in the copied prompt
        assert task["cost_usd"] == 0
        res = await engine.handoff_open("img1", "win-pc")
        assert res["opened"] == "app"
        assert engine.store.get_task("img1")["plan"]["handoff"]["watching"] == "win-pc"
        await asyncio.sleep(1)
        (tmp_path / "Downloads" / "old.txt").write_text("not an image")
        (tmp_path / "Downloads" / "ChatGPT Image fox.png").write_bytes(PNG)
        for _ in range(60):
            if engine.store.get_task("img1")["status"] == "done":
                break
            await asyncio.sleep(0.5)
        done = engine.store.get_task("img1")
        assert done["status"] == "done" and "Image received" in done["result"]
        assert (tmp_path / "hub" / "out").joinpath(done["output_path"].split("/out/")[1], "files", "ChatGPT Image fox.png").read_bytes() == PNG
    finally:
        at.cancel()
        server.should_exit = True
        await asyncio.gather(at, st, return_exceptions=True)


def test_add_a_file_by_hand_finishes_a_waiting_task(tmp_path):
    cfg = Config(make_home(tmp_path / "h", providers={"mock": {"kind": "mock"}, **HANDOFF}, models=IMAGE_MODELS + MOCK_MODELS))
    with TestClient(create_app(Engine(cfg)), client=("203.0.113.9", 1), headers={"Authorization": "Bearer test-token"}) as c:
        tid = c.post("/api/tasks", json={"prompt": "draw a picture of a red bicycle", "confirm": True}).json()["task_id"]
        for _ in range(50):
            if c.get(f"/api/tasks/{tid}").json()["status"] != "running":
                break
            time.sleep(0.1)
        assert c.get(f"/api/tasks/{tid}").json()["status"] == "waiting"
        r = c.post(f"/api/tasks/{tid}/files", json={"name": "../../bike.png", "b64": "data:image/png;base64," + base64.b64encode(PNG).decode()})
        assert r.status_code == 200 and r.json()["path"] == "files/bike.png"  # name is sanitised
        t = c.get(f"/api/tasks/{tid}").json()
        assert t["status"] == "done" and "bike.png" in t["result"]
        assert any(f["path"] == "files/bike.png" and f["kind"] == "image" for f in c.get(f"/api/tasks/{tid}/files").json())


async def test_later_tasks_know_where_earlier_work_is(tmp_path):
    cfg = Config(make_home(tmp_path / "h"))
    engine = Engine(cfg)
    first = await engine.plan("write python code that prints hello")
    t1 = await engine.run_task("one", "write python code that prints hello", first, [], [])
    note = engine.recent_work_note(exclude="two")
    assert "write python code that prints hello" in note and "files/hello.py" in note and "{OUTPUT}/" in note
    second = await engine.plan("now add a test for it")
    t2 = await engine.run_task("two", "now add a test for it", second, [], [])
    assert "Earlier work" not in t2["result"]  # API models can't open files, so they don't get the note
    assert (tmp_path / "h" / "out").exists() and t1["status"] == "done"
    cfg.raw["router"]["share_recent_work"] = False
    assert engine.recent_work_note() == ""


def test_plan_apps_get_read_access_to_earlier_work(tmp_path):
    cfg = Config(make_home(tmp_path / "h"))
    prov = CLIProvider("claude-code", {"command": "claude", "args": ["-p", "{prompt}"], "read_dir_args": ["--add-dir", "{dir}"]}, cfg)
    argv = prov.build_argv(RunRequest("hi", ModelInfo(provider="claude-code", id="default"), read_dirs=["/out"]), "hi", False, None)
    assert argv[-2:] == ["--add-dir", "/out"]
