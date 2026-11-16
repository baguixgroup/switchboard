"""Stands in for a plan CLI like `claude -p ... --output-format json` in tests.

Prompt keywords: LIMIT -> plan-limit error with a reset time; WRITE -> creates made.txt;
USE_GATEWAY -> starts the MCP server from --mcp-config and calls its echo__add tool.
"""
import asyncio
import json
import sys
import time
from pathlib import Path

argv = sys.argv[1:]
prompt = argv[argv.index("-p") + 1] if "-p" in argv else ""
Path("argv.json").write_text(json.dumps(argv))

if "LIMIT" in prompt:
    print(f"Claude AI usage limit reached|{int(time.time()) + 7200}", file=sys.stderr)
    sys.exit(1)
if "WRITE" in prompt:
    Path("made.txt").write_text("created by the fake CLI")
answer = "fake plan answer"
if "USE_GATEWAY" in prompt:
    from mcp import Client, StdioServerParameters

    cfg = json.loads(Path(argv[argv.index("--mcp-config") + 1]).read_text())["mcpServers"]["switchboard"]

    async def use() -> str:
        params = StdioServerParameters(command=cfg["command"], args=cfg["args"], env=cfg.get("env"))
        async with Client(params) as c:
            names = [t.name for t in (await c.list_tools()).tools]
            tool = next(n for n in names if n.endswith("echo__add"))
            res = await c.call_tool(tool, {"a": 2, "b": 3})
            return f"tools={names} add={res.content[0].text}"

    answer = asyncio.run(use())
print(json.dumps({"result": answer, "total_cost_usd": 0.12, "is_error": False}))
