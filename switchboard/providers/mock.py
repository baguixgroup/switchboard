"""Offline provider for demos and tests (kind: mock). Never calls a network API.

If the prompt contains `TOOL:<name-suffix> {json}`, it calls the first tool whose name ends with
that suffix and reports the result, which exercises the whole tool path (including remote nodes).
"""
from __future__ import annotations

import json
import re

from ..toolbox import ToolBox
from .base import Provider, ProviderError, RunRequest, RunResult

_TOOL = re.compile(r"TOOL:(\S+)\s*(\{.*?\})?", re.S)


class MockProvider(Provider):
    kind = "mock"

    def available(self) -> bool:
        return True

    async def list_models(self) -> list[str]:
        return ["mock-small", "mock-large"]

    async def run(self, req: RunRequest, toolbox: ToolBox | None = None) -> RunResult:
        if self.cfg.get("fail"):  # simulate an outage / exhausted credit to test failover
            raise ProviderError(f"simulated failure: {self.cfg['fail']}", self.cfg["fail"])
        parts = [f"Mock answer from {req.model.id} (effort={req.effort}).", f"You asked: {req.prompt[:300]}"]
        calls = 1
        for m in _TOOL.finditer(req.prompt):
            suffix, raw = m.group(1), m.group(2) or "{}"
            spec = next((s for s in (toolbox.specs() if toolbox else []) if s.name.endswith(suffix)), None)
            if not spec:
                parts.append(f"No tool matching '{suffix}'. Available: {[s.name for s in (toolbox.specs() if toolbox else [])]}")
                continue
            out, err = await toolbox.call(spec.name, json.loads(raw))
            calls += 1
            parts.append(f"Tool {spec.origin} -> {'ERROR ' if err else ''}{out}")
        if "```" not in req.prompt and "code" in req.prompt.lower():
            parts.append("```python hello.py\nprint('hello from switchboard')\n```")
        text = "\n\n".join(parts)
        return RunResult(text=text, provider=self.name, model=req.model.id, input_tokens=len(req.prompt) // 4,
                         output_tokens=len(text) // 4, cost_usd=req.model.cost(len(req.prompt) // 4, len(text) // 4),
                         requests=calls, stop_reason="end_turn")
