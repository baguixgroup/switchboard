"""Plan CLIs (daily limits, no credit): billing modes, limit detection + failover, remote runs, tool gateway."""
import asyncio
import os
import stat
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest
import uvicorn

from switchboard.agent import run_agent
from switchboard.config import Config
from switchboard.engine import Engine
from switchboard.providers.cli_bridge import parse_reset
from switchboard.web.app import create_app

from .conftest import FIXTURES, MOCK_MODELS, free_port, make_home

PLAN_MODEL = {"provider": "fakeclaude", "id": "opus", "label": "Fake Claude plan", "q": 9.5, "price": [0, 0],
              "features": ["cli", "agentic", "tools"]}


def fake_cli(tmp_path: Path) -> str:
    exe = tmp_path / "fake-claude"
    exe.write_text(f"#!{sys.executable}\n" + (FIXTURES / "fake_cli.py").read_text())
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return str(exe)


def cli_cfg(command: str) -> dict:
    return {"kind": "cli", "command": command, "args": ["-p", "{prompt}", "--output-format", "json"],
            "json_result": "result", "json_cost": "total_cost_usd", "mcp_style": "claude", "tool_id": "fake_claude"}


async def _wait(cond, timeout=40.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        await asyncio.sleep(0.2)
    return False


def test_parse_reset_formats():
    now = datetime(2026, 11, 16, 10, 0).astimezone()
    assert parse_reset("Claude AI usage limit reached|1800000000", now) == 1800000000
    assert parse_reset("You've hit your usage limit. Try again in 2 hours 13 minutes.", now) == \
        now.timestamp() + 2 * 3600 + 13 * 60
    t = datetime.fromtimestamp(parse_reset("5-hour limit reached - resets 3pm", now))
    assert (t.hour, t.minute) == (15, 0)
    t = datetime.fromtimestamp(parse_reset("Usage limit. Try again at 9:30 AM", now))
    assert (t.day, t.hour, t.minute) == (17, 9, 30)  # already past today -> tomorrow
    assert parse_reset("something else went wrong", now) is None


async def test_plans_first_prefers_plan_and_fails_over_on_limit(tmp_path):
    cfg = Config(make_home(tmp_path / "h", providers={"mock": {"kind": "mock"}, "fakeclaude": cli_cfg(fake_cli(tmp_path))},
                           models=MOCK_MODELS + [PLAN_MODEL]))
    engine = Engine(cfg)
    plan = await engine.plan("write a python script to sort photos")
    assert plan.provider == "fakeclaude" and any("plan's limits" in r for r in plan.reasons)

    ok = await engine.run_task("t-ok", "write a python script to sort photos", plan, [], [])
    assert ok["status"] == "done" and "fake plan answer" in ok["result"] and ok["cost_usd"] == 0

    plan = await engine.plan("LIMIT write a python script")
    task = await engine.run_task("t-limit", "LIMIT write a python script", plan, [], [])
    assert task["status"] == "done" and task["plan"]["executed"].startswith("mock:")  # failed over to credit
    limits = engine.limits()
    assert "fakeclaude" in limits and limits["fakeclaude"]["until"] > time.time() + 3600
    after = await engine.plan("write another script")
    assert after.provider == "mock" and any("Plan limits reached" in w for w in after.warnings)
    status = {p["provider"]: p for p in engine.plans_status()}
    assert status["fakeclaude"]["status"] == "limit reached" and status["fakeclaude"]["resets"]


async def test_plans_only_never_spends_credit(tmp_path):
    cfg = Config(make_home(tmp_path / "h", providers={"mock": {"kind": "mock"}, "fakeclaude": cli_cfg(fake_cli(tmp_path))},
                           models=MOCK_MODELS + [PLAN_MODEL], router={"billing": "plans_only"}))
    engine = Engine(cfg)
    assert (await engine.plan("hello there")).provider == "fakeclaude"
    engine.store.set_limit("fakeclaude", time.time() + 3600, "test")
    with pytest.raises(ValueError, match="All usable plans have hit their limits"):
        await engine.plan("hello there")
    # the same task is allowed to use credit when asked
    assert (await engine.plan("hello there", billing="plans_first")).provider == "mock"


async def test_plan_cli_on_agent_uses_tools_through_gateway(tmp_path, echo_server_cfg):
    """The plan CLI is installed only on the agent; it reaches the agent's MCP tools via gateway -> hub -> agent."""
    exe = fake_cli(tmp_path)
    port = free_port()
    hub = Config(make_home(tmp_path / "hub", name="pi", port=port,
                           providers={"mock": {"kind": "mock"}, "fakeclaude": cli_cfg("not-installed-on-hub")},
                           models=MOCK_MODELS + [PLAN_MODEL], router={"billing": "plans_only"}))
    agent = Config(make_home(tmp_path / "agent", role="agent", name="win-pc", hub_url=f"http://127.0.0.1:{port}",
                             providers={"fakeclaude": cli_cfg(exe)}, mcp_servers={"echo": echo_server_cfg}))
    engine = Engine(hub)
    server = uvicorn.Server(uvicorn.Config(create_app(engine), host="127.0.0.1", port=port, log_config=None))
    server_task = asyncio.create_task(server.serve())
    assert await _wait(lambda: server.started)
    agent_task = asyncio.create_task(run_agent(agent))
    try:
        assert await _wait(lambda: "echo" in engine.nodes.mcp_locations())
        assert engine.cli_location("fakeclaude") == "win-pc"
        prompt = "USE_GATEWAY WRITE add two numbers with the echo tool"
        plan = await engine.plan(prompt, tools=["echo"])
        assert plan.provider == "fakeclaude" and plan.tools == ["echo"]
        task = await engine.run_task("t-gw", prompt, plan, [], [])
        assert task["status"] == "done", task
        assert "add=5" in task["result"] and "echo__add" in task["result"]
        assert "made.txt" in task["result"]  # file written on win-pc is reported...
        assert list((tmp_path / "hub" / "out").rglob("files-from-win-pc/made.txt"))  # ...and copied to the hub
        assert any(c["tool"] == "win-pc/echo/add" for c in task["plan"]["tool_calls"])
    finally:
        agent_task.cancel()
        server.should_exit = True
        await asyncio.gather(agent_task, server_task, return_exceptions=True)
