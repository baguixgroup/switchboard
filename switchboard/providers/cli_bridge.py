"""Run official subscription CLIs (Claude Code, Codex, Gemini CLI, Copilot CLI) non-interactively.

These spend your Claude / ChatGPT / Google / Copilot plan instead of API credit. Each runs in the
task's output folder, so with `permissions.cli_agents_can_edit: true` the files they write land
straight in your output folder.
"""
from __future__ import annotations

import asyncio
import json
import shutil

from ..toolbox import ToolBox
from .base import Provider, ProviderError, RunRequest, RunResult


def _fill(args: list, **values) -> list[str]:
    out = []
    for a in args:
        s = str(a)
        for k, v in values.items():
            s = s.replace("{" + k + "}", str(v))
        out.append(s)
    return out


def _dig(data, path: str | None):
    if not path:
        return None
    cur = data
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


class CLIProvider(Provider):
    kind = "cli"

    @property
    def executable(self) -> str | None:
        return shutil.which(self.cfg.get("command", self.name))

    def available(self) -> bool:
        return self.executable is not None

    async def run(self, req: RunRequest, toolbox: ToolBox | None = None) -> RunResult:
        exe = self.executable
        if not exe:
            raise ProviderError(f"{self.cfg.get('command')} is not installed on this machine", "unavailable")
        prompt = req.prompt
        if req.history:
            prev = "\n\n".join(f"{m['role'].upper()}: {m['content']}" for m in req.history if isinstance(m.get("content"), str))
            prompt = f"Conversation so far:\n{prev}\n\nUSER: {req.prompt}"
        if req.images:
            prompt += "\n\nImages for this task: " + ", ".join(str(p) for p in req.images)
        via_stdin = self.cfg.get("prompt_via") == "stdin" or len(prompt) > 24_000
        argv = [exe] + _fill(self.cfg.get("args") or ["-p", "{prompt}"], prompt="" if via_stdin else prompt)
        argv = [a for a in argv if a != ""] if via_stdin else argv
        if req.model.id not in ("", "default"):
            argv += _fill(self.cfg.get("model_args") or [], model=req.model.id)
        if req.effort and self.cfg.get("effort_args"):
            argv += _fill(self.cfg["effort_args"], effort=req.effort)
        if req.allow_edits:
            argv += list(self.cfg.get("edit_args") or [])
        timeout = float(self.cfg.get("timeout_seconds", 1800))
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=str(req.workdir) if req.workdir else None,
                stdin=asyncio.subprocess.PIPE if via_stdin else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, err = await asyncio.wait_for(proc.communicate(prompt.encode() if via_stdin else None), timeout)
        except asyncio.TimeoutError as exc:
            proc.kill()
            raise ProviderError(f"{self.name} timed out after {timeout:.0f}s", "unavailable") from exc
        except OSError as exc:
            raise ProviderError(f"Could not start {exe}: {exc}", "unavailable") from exc
        stdout = out.decode(errors="replace").strip()
        stderr = err.decode(errors="replace").strip()
        if proc.returncode != 0:
            low = (stderr + stdout).lower()
            kind = "rate_limit" if any(w in low for w in ("usage limit", "rate limit", "quota", "limit reached")) else \
                "auth" if any(w in low for w in ("log in", "login", "not authenticated", "unauthorized")) else "error"
            raise ProviderError(f"{self.name} exited {proc.returncode}: {(stderr or stdout)[-1500:]}", kind)

        text, notional = stdout, None
        if self.cfg.get("json_result"):
            try:
                data = json.loads(stdout)
                text = str(_dig(data, self.cfg["json_result"]) or stdout)
                cost = _dig(data, self.cfg.get("json_cost"))
                notional = float(cost) if cost is not None else None
                if data.get("is_error"):
                    raise ProviderError(f"{self.name}: {text[-1500:]}", "error")
            except ValueError:
                pass
        approx_in, approx_out = len(prompt) // 4, len(text) // 4
        return RunResult(text=text, provider=self.name, model=req.model.id, input_tokens=approx_in,
                         output_tokens=approx_out, cost_usd=0.0, requests=1, stop_reason="cli",
                         notional_cost_usd=notional)
