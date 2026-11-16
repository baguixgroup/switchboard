"""Hub + agent + a real MCP server, all offline (mock provider)."""
import asyncio

import uvicorn

from switchboard.agent import run_agent
from switchboard.config import Config
from switchboard.engine import Engine
from switchboard.web.app import create_app

from .conftest import MOCK_MODELS, free_port, make_home


async def _wait(cond, timeout=40.0):
    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        if cond():
            return True
        await asyncio.sleep(0.2)
    return False


async def test_remote_mcp_tool_and_output_mirror(tmp_path, echo_server_cfg):
    port = free_port()
    hub = Config(make_home(tmp_path / "hub", name="pi", port=port))
    agent = Config(make_home(tmp_path / "agent", role="agent", name="win-pc", hub_url=f"http://127.0.0.1:{port}",
                             mcp_servers={"echo": echo_server_cfg}))
    engine = Engine(hub)
    server = uvicorn.Server(uvicorn.Config(create_app(engine), host="127.0.0.1", port=port, log_config=None))
    server_task = asyncio.create_task(server.serve())
    assert await _wait(lambda: server.started)
    agent_task = asyncio.create_task(run_agent(agent))
    try:
        assert await _wait(lambda: "echo" in engine.nodes.mcp_locations())
        prompt = 'TOOL:echo__add {"a": 20, "b": 22} and show some code'
        plan = await engine.plan(prompt, tools=["echo"])
        assert plan.tools == ["echo"]
        task = await engine.run_task("t1", prompt, plan, [], [])
        assert task["status"] == "done", task
        assert "win-pc/echo/add -> 42" in task["result"]
        hub_files = list((tmp_path / "hub" / "out").rglob("hello.py"))
        assert hub_files
        assert await _wait(lambda: list((tmp_path / "agent" / "out").rglob("hello.py")), timeout=20)
        credits = engine.credits()
        assert credits["spend_this_month"][0]["provider"] == "mock"
    finally:
        agent_task.cancel()
        server.should_exit = True
        await asyncio.gather(agent_task, server_task, return_exceptions=True)


async def test_failover_to_next_model(tmp_path):
    models = MOCK_MODELS + [{"provider": "flaky", "id": "flaky-pro", "q": 9.5, "price": [0.01, 0.01], "features": ["tools"]}]
    cfg = Config(make_home(tmp_path / "h", providers={"mock": {"kind": "mock"}, "flaky": {"kind": "mock", "fail": "rate_limit"}},
                           models=models, router={"prefer": {"flaky": 5}}))
    engine = Engine(cfg)
    plan = await engine.plan("write a haiku about failover")
    assert plan.provider == "flaky"
    task = await engine.run_task("t2", "write a haiku about failover", plan, [], [])
    assert task["status"] == "done" and task["plan"]["executed"].startswith("mock:")
    assert "Failover" in task["result"]
    await engine.close()


async def test_api_auth_and_credit_crud(tmp_path):
    from fastapi.testclient import TestClient

    cfg = Config(make_home(tmp_path / "h"))
    app = create_app(Engine(cfg))
    with TestClient(app, client=("203.0.113.9", 5000)) as remote:
        assert remote.get("/api/status").status_code == 401
        assert remote.get("/api/status", headers={"Authorization": "Bearer wrong"}).status_code == 401
        h = {"Authorization": "Bearer test-token"}
        assert remote.get("/api/status", headers=h).status_code == 200
        r = remote.post("/api/credits/sources", headers=h, json={"id": "promo", "provider": "mock", "type": "prepaid",
                                                                  "amount": 5, "expires": "2099-01-01"})
        assert r.status_code == 200
        assert remote.get("/api/credits", headers=h).json()["sources"][0]["id"] == "promo"
        assert remote.delete("/api/credits/sources/promo", headers=h).status_code == 200
