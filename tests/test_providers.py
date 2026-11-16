"""Run the real Anthropic and OpenAI SDKs against a local fake API to check requests, tool loops and usage."""
import asyncio
import json

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from switchboard.catalog import ModelInfo
from switchboard.config import Config
from switchboard.providers import RunRequest
from switchboard.providers.anthropic_provider import AnthropicProvider
from switchboard.providers.openai_compat import OpenAICompatProvider
from switchboard.toolbox import ToolBox

from .conftest import free_port, make_home

SEEN: list[dict] = []


def fake_api() -> FastAPI:
    app = FastAPI()

    @app.post("/v1/messages")
    async def messages(req: Request):
        body = await req.json()
        SEEN.append({"path": "anthropic", "body": body, "beta": req.headers.get("anthropic-beta")})
        usage = {"input_tokens": 100, "output_tokens": 50}
        if body.get("stream"):
            def sse():
                msg = {"id": "msg_1", "type": "message", "role": "assistant", "model": body["model"], "content": [],
                       "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 100, "output_tokens": 1}}
                events = [
                    ("message_start", {"type": "message_start", "message": msg}),
                    ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
                    ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "streamed hello"}}),
                    ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                    ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 50}}),
                    ("message_stop", {"type": "message_stop"}),
                ]
                for name, data in events:
                    yield f"event: {name}\ndata: {json.dumps(data)}\n\n"
            return StreamingResponse(sse(), media_type="text/event-stream")
        last = body["messages"][-1]
        done = isinstance(last["content"], list) and any(b.get("type") == "tool_result" for b in last["content"])
        content = [{"type": "text", "text": "the sum is 5"}] if done else \
            [{"type": "tool_use", "id": "toolu_1", "name": body["tools"][0]["name"], "input": {"a": 2, "b": 3}}]
        return {"id": "msg_2", "type": "message", "role": "assistant", "model": body["model"], "content": content,
                "stop_reason": "end_turn" if done else "tool_use", "stop_sequence": None, "usage": usage}

    @app.post("/v1/chat/completions")
    async def chat(req: Request):
        body = await req.json()
        SEEN.append({"path": "openai", "body": body})
        done = body["messages"][-1]["role"] == "tool"
        msg = {"role": "assistant", "content": "the sum is 5"} if done else {
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "call_1", "type": "function",
                            "function": {"name": body["tools"][0]["function"]["name"], "arguments": '{"a": 2, "b": 3}'}}]}
        return {"id": "c1", "object": "chat.completion", "created": 0, "model": body["model"],
                "choices": [{"index": 0, "message": msg, "finish_reason": "stop" if done else "tool_calls"}],
                "usage": {"prompt_tokens": 80, "completion_tokens": 20, "total_tokens": 100}}

    return app


def adder_box() -> ToolBox:
    tb = ToolBox()

    async def add(args):
        return str(args["a"] + args["b"])

    tb.add("calc__add", "Add two numbers", {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}}, add)
    return tb


async def test_real_sdks_against_fake_api(tmp_path):
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(fake_api(), host="127.0.0.1", port=port, log_config=None))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    try:
        cfg = Config(make_home(tmp_path / "h"))
        cfg.secrets.update({"FAKE_KEY": "sk-test"})
        claude = AnthropicProvider("anthropic", {"api_key_env": "FAKE_KEY", "base_url": f"http://127.0.0.1:{port}"}, cfg)
        opus = ModelInfo(provider="anthropic", id="claude-opus-5-5", price_in=4, price_out=20,
                         efforts=["low", "medium", "high", "xhigh", "max"], features={"tools", "fallbacks"})

        # Tool loop (non-streaming), effort + server-side fallbacks sent.
        tb = adder_box()
        res = await claude.run(RunRequest("add 2 and 3", opus, effort="high", max_tokens=4000), tb)
        assert res.text == "the sum is 5" and res.requests == 2 and tb.calls[0]["tool"] == "calc__add"
        first = [s for s in SEEN if s["path"] == "anthropic"][0]
        assert first["body"]["output_config"] == {"effort": "high"} and first["body"]["fallbacks"] == "default"
        assert "server-side-fallback" in (first["beta"] or "") and "thinking" not in first["body"]
        second = [s for s in SEEN if s["path"] == "anthropic"][1]["body"]["messages"][-1]["content"][0]
        assert second["type"] == "tool_result" and second["content"] == "5"
        assert res.input_tokens == 200 and abs(res.cost_usd - (200 * 4 + 100 * 20) / 1e6) < 1e-9

        # Streaming path (no tools).
        res = await claude.run(RunRequest("hi", opus, effort="low"), None)
        assert res.text == "streamed hello" and res.output_tokens == 50

        # OpenAI-compatible tool loop with reasoning_effort and max_completion_tokens.
        oa = OpenAICompatProvider("openai", {"api_key_env": "FAKE_KEY", "base_url": f"http://127.0.0.1:{port}/v1",
                                             "max_tokens_param": "max_completion_tokens"}, cfg)
        gpt = ModelInfo(provider="openai", id="gpt-x", price_in=1, price_out=2, efforts=["low", "medium", "high"], features={"tools"})
        res = await oa.run(RunRequest("add 2 and 3", gpt, effort="medium", max_tokens=1000), adder_box())
        assert res.text == "the sum is 5" and res.requests == 2 and res.input_tokens == 160
        body = [s for s in SEEN if s["path"] == "openai"][0]["body"]
        assert body["reasoning_effort"] == "medium" and body["max_completion_tokens"] == 1000
        assert [s for s in SEEN if s["path"] == "openai"][1]["body"]["messages"][-1] == {"role": "tool", "tool_call_id": "call_1", "content": "5"}
    finally:
        server.should_exit = True
        await task
