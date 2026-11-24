"""Run the official plan CLIs non-interactively: Claude Code, Codex, Gemini CLI, Copilot CLI, Grok Build.

These sign in with your Claude / ChatGPT / Google / Copilot / SuperGrok account, so they spend your
plan's daily or weekly limits instead of API credit. Each run happens in the task's output folder
(with `permissions.cli_agents_can_edit: true` they can write files there), and gets Tazrivo's tools
(MCP servers on every machine, devices) through `switchboard mcp gateway`.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pathlib import Path

from ..toolbox import ToolBox
from .base import Provider, ProviderError, RunRequest, RunResult, DEFAULT_SYSTEM

LIMIT_WORDS = ("usage limit", "limit reached", "rate limit", "quota", "too many requests", "hit your limit",
               "reached your", "limit exceeded", "resource_exhausted", "out of credits", "weekly limit",
               "daily limit", "monthly limit", "usage_limit_reached")
AUTH_WORDS = ("log in", "login", "not logged in", "not authenticated", "unauthorized", "sign in", "authenticate",
              "auth login", "auth method", "please set an auth", "no credentials")
LIMIT_ANSWER = re.compile(r"(claude ai usage limit|you've hit your (?:(?:usage|weekly|daily|monthly|plan) )?limit|you have hit your|usage limit reached|"
                          r"you've reached your|you have reached your|quota exceeded|rate limit exceeded|5-hour limit)", re.I)
_AUTH_OK: dict[str, float] = {}
CONFIG_FILES = {".switchboard-mcp.json", ".gemini", "codex-last-message.txt"}


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


def parse_reset(text: str, now: datetime | None = None) -> float | None:
    """Find when a plan limit resets in a CLI's error text. Returns epoch seconds or None."""
    now = now or datetime.now().astimezone()
    m = re.search(r"\|(\d{10})\b", text)  # Claude Code: "...usage limit reached|1731790800"
    if m:
        return float(m.group(1))
    m = re.search(r"(?:try again|resets?|available again|retry)\s+in\s+(?:(\d+)\s*d(?:ays?)?\s*)?(?:(\d+)\s*h(?:ours?|rs?)?\s*)?"
                  r"(?:(\d+)\s*m(?:in(?:ute)?s?)?)?(?:\s*(\d+)\s*s)?", text, re.I)
    if m and any(m.groups()):
        d, h, mi, sec = (int(x) if x else 0 for x in m.groups())
        return (now + timedelta(days=d, hours=h, minutes=mi, seconds=sec)).timestamp()
    # Claude's weekly quota: "resets Nov 23 at 1am (America/Winnipeg)".
    dated = re.search(r"resets?\s+([A-Za-z]{3,9})\s+(\d{1,2})(?:,?\s+(\d{4}))?\s+at\s+"
                      r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text, re.I)
    if dated:
        month, day, year, hour, minute, ampm = dated.groups()
        zone = now.tzinfo
        named_zone = re.search(r"\(([A-Za-z_]+/[A-Za-z_/+-]+)\)", text)
        if named_zone:
            try:
                zone = ZoneInfo(named_zone.group(1))
            except ZoneInfoNotFoundError:
                pass
        try:
            month_number = datetime.strptime(month[:3].title(), "%b").month
            hour = int(hour) % 12 + (12 if (ampm or "").lower() == "pm" else 0) if ampm else int(hour)
            candidate = datetime(int(year or now.year), month_number, int(day), hour, int(minute or 0), tzinfo=zone)
            if not year and candidate.timestamp() <= now.timestamp():
                candidate = candidate.replace(year=candidate.year + 1)
            return candidate.timestamp()
        except ValueError:
            return None
    m = re.search(r"(?:resets?|try again|available again)\s+(?:at\s+|on\s+)?(?:\w{3,9},?\s+)?(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)?",
                  text, re.I)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        ampm = (m.group(3) or "").lower().replace(".", "")
        if ampm == "pm" and hour < 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
        if hour <= 23 and minute <= 59 and (ampm or m.group(2)):
            cand = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if cand <= now:
                cand += timedelta(days=1)
            return cand.timestamp()
    return None


def gateway_command(config, servers: list[str], task_rel: str | None) -> tuple[str, list[str], dict]:
    from ..paths import self_command

    cmd = self_command("mcp", "gateway", "--servers", ",".join(servers))
    if task_rel:
        cmd += ["--task", task_rel]
    env = {"SWITCHBOARD_HOME": str(config.home)}
    return cmd[0], cmd[1:], env


class CLIProvider(Provider):
    kind = "cli"

    @property
    def executable(self) -> str | None:
        from ..runtime import which

        return which(self.cfg.get("command", self.name))

    def available(self) -> bool:
        from ..runtime import cli_feature_enabled

        feature = self.cfg.get("required_feature")
        return self.executable is not None and (not feature or cli_feature_enabled(self.executable, feature))

    def _attach_gateway(self, argv: list[str], req: RunRequest) -> None:
        style = self.cfg.get("mcp_style")
        if not (style and req.tool_servers and req.workdir):
            return
        cmd, args, env = gateway_command(self.config, req.tool_servers, req.task_rel)
        server = {"command": cmd, "args": args, "env": env}
        wd = Path(req.workdir)
        if style == "claude":
            path = wd / ".switchboard-mcp.json"
            path.write_text(json.dumps({"mcpServers": {"switchboard": server}}), encoding="utf-8")
            argv += ["--mcp-config", str(path), "--strict-mcp-config", "--allowedTools", "mcp__switchboard"]
        elif style == "codex":
            env_toml = "{ " + ", ".join(f"{k} = {json.dumps(v)}" for k, v in env.items()) + " }"
            argv += ["-c", f"mcp_servers.switchboard.command={json.dumps(cmd)}",
                     "-c", f"mcp_servers.switchboard.args={json.dumps(args)}",
                     "-c", f"mcp_servers.switchboard.env={env_toml}",
                     "-c", 'mcp_servers.switchboard.default_tools_approval_mode="approve"']
        elif style == "gemini":
            (wd / ".gemini").mkdir(exist_ok=True)
            (wd / ".gemini" / "settings.json").write_text(
                json.dumps({"mcpServers": {"switchboard": dict(server, trust=True)}}), encoding="utf-8")
            argv += ["--allowed-mcp-server-names", "switchboard"]
        elif style == "copilot":
            path = wd / ".switchboard-mcp.json"
            path.write_text(json.dumps({"mcpServers": {"switchboard": dict(server, type="local", tools=["*"])}}),
                            encoding="utf-8")
            argv += ["--additional-mcp-config", f"@{path}", "--allow-tool", "switchboard"]

    def build_argv(self, req: RunRequest, prompt: str, via_stdin: bool, result_file: Path | None) -> list[str]:
        exe = self.executable or self.cfg.get("command", self.name)
        argv = [exe] + _fill(self.cfg.get("args") or ["-p", "{prompt}"], prompt="" if via_stdin else prompt,
                             result_file=str(result_file or ""))
        if via_stdin:
            argv = [a for a in argv if a != ""]
        if req.model.id not in ("", "default"):
            argv += _fill(self.cfg.get("model_args") or [], model=req.model.id)
        if self.cfg.get("prompt_last") and not via_stdin:
            tail_prompt = prompt
        else:
            tail_prompt = None
        if req.effort and self.cfg.get("effort_args"):
            argv += _fill(self.cfg["effort_args"], effort=req.effort)
        for img in req.images:
            if self.cfg.get("image_args"):
                argv += _fill(self.cfg["image_args"], image=str(img))
        writes = req.allow_edits or self.cfg.get("output_kind") == "image"
        argv += list(self.cfg.get("edit_args") or []) if writes else list(self.cfg.get("readonly_args") or [])
        for d in req.read_dirs:
            argv += _fill(self.cfg.get("read_dir_args") or [], dir=d)
        self._attach_gateway(argv, req)
        if tail_prompt is not None:
            argv.append(tail_prompt)
        return argv

    async def check_auth(self) -> None:
        """Optional fast login check (e.g. `codex login status`), cached for 10 minutes when it passes."""
        check = self.cfg.get("auth_check")
        if not check or time.time() - _AUTH_OK.get(self.name, 0) < 600:
            return
        try:
            proc = await asyncio.create_subprocess_exec(self.executable, *check, stdin=asyncio.subprocess.DEVNULL,
                                                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, err = await asyncio.wait_for(proc.communicate(), 20)
        except (OSError, asyncio.TimeoutError):
            return
        text = (out + err).decode(errors="replace")
        if str(self.cfg.get("auth_check_fail", "not logged in")).lower() in text.lower():
            raise ProviderError(f"{self.name}: not signed in. Run `{self.cfg.get('command')} login` once on this machine "
                                f"with your plan account.", "auth")
        _AUTH_OK[self.name] = time.time()

    async def run(self, req: RunRequest, toolbox: ToolBox | None = None) -> RunResult:
        from ..security import require_execution_enabled
        # Standalone CLI adapters also support config=None (legacy embedding).
        # Hub-built adapters always carry their actual deployment Config.
        if self.config is not None:
            require_execution_enabled(self.config)
        if not self.executable:
            raise ProviderError(f"{self.cfg.get('command')} is not installed on this machine", "unavailable")
        await self.check_auth()
        prompt = req.prompt
        if req.history:
            prev = "\n\n".join(f"{m['role'].upper()}: {m['content']}" for m in req.history if isinstance(m.get("content"), str))
            prompt = f"Conversation so far:\n{prev}\n\nUSER: {req.prompt}"
        if req.images and not self.cfg.get("image_args"):
            prompt += "\n\nImages for this task (read these files): " + ", ".join(str(p) for p in req.images)
        if req.tool_servers:
            prompt += ("\n\nYou have Tazrivo tools (MCP server 'switchboard') for: "
                       + ", ".join("machine management" if s == "__machine_management__" else s for s in req.tool_servers)
                       + ", plus the user's machines and devices. Use them.")
        if req.system and req.system != DEFAULT_SYSTEM:
            prompt = 'Companion instructions (communication only; existing tool permissions still apply):\n'+req.system+'\n\nRequest and reference context:\n'+prompt
        via_stdin = self.cfg.get("prompt_via") == "stdin" or len(prompt) > 24_000
        workdir = Path(req.workdir) if req.workdir else None
        image_task = self.cfg.get("output_kind") == "image"
        if image_task:
            if not workdir:
                raise ProviderError("Image generation needs a task output folder.", "bad_request")
            prompt = ("Use native image generation to fulfill this request. Save the actual generated PNG or JPEG "
                      "in the current task directory. Do not substitute code-rendered shapes, SVG or a text-only "
                      "description. If native image generation fails, report the failure explicitly.\n\n" + prompt)
        started = time.time()
        result_file = (workdir / "codex-last-message.txt") if (workdir and "{result_file}" in json.dumps(self.cfg.get("args"))) else None
        argv = self.build_argv(req, prompt, via_stdin, result_file)
        timeout = float(self.cfg.get("timeout_seconds", 1800))
        env = dict(os.environ, NO_COLOR="1")
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=str(workdir) if workdir else None, env=env,
                stdin=asyncio.subprocess.PIPE if via_stdin else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        except OSError as exc:
            raise ProviderError(f"Could not start {argv[0]}: {exc}", "unavailable") from exc
        try:
            out, err = await asyncio.wait_for(proc.communicate(prompt.encode() if via_stdin else None), timeout)
        except asyncio.TimeoutError as exc:
            proc.kill()
            raise ProviderError(f"{self.name} timed out after {timeout:.0f}s", "unavailable") from exc
        stdout = out.decode(errors="replace").strip()
        stderr = err.decode(errors="replace").strip()
        combined = stderr + "\n" + stdout
        if proc.returncode != 0:
            self._raise(combined, proc.returncode)

        text, notional = stdout, None
        if result_file and result_file.exists():
            text = result_file.read_text(encoding="utf-8", errors="replace").strip() or stdout
            result_file.unlink(missing_ok=True)
        elif self.cfg.get("json_result"):
            try:
                data = json.loads(stdout.splitlines()[-1] if self.cfg.get("jsonl") else stdout)
            except (ValueError, IndexError):
                data = None
            if isinstance(data, dict):
                text = str(_dig(data, self.cfg["json_result"]) or stdout)
                cost = _dig(data, self.cfg.get("json_cost"))
                notional = float(cost) if isinstance(cost, (int, float)) else None
                if data.get("is_error"):
                    self._raise(text, 1)
        if len(text) < 400 and LIMIT_ANSWER.match(text.strip()):
            self._raise(text, 0)  # some CLIs exit 0 with a limit message as the "answer"
        if image_task:
            images = [p for p in new_files(workdir, started, max_bytes=20_000_000)
                      if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
                      and p.read_bytes().startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"RIFF"))]
            if not images:
                raise ProviderError("Codex completed without producing an image file. " + text[-600:], "unavailable")
        approx_in, approx_out = len(prompt) // 4, len(text) // 4
        return RunResult(text=text, provider=self.name, model=req.model.id, input_tokens=approx_in,
                         output_tokens=approx_out, cost_usd=0.0, requests=1, stop_reason="cli",
                         notional_cost_usd=notional)

    def _raise(self, text: str, code: int) -> None:
        quota = False
        for line in reversed(text.splitlines()):
            try:
                data = json.loads(line)
            except ValueError:
                continue
            if isinstance(data, dict) and (data.get("is_error") or data.get("api_error_status") == 429):
                quota = data.get("api_error_status") == 429 or data.get("api_error") == "usage_limit_reached"
                text = str(data.get("result") or data.get("message") or text)
                break
        low = text.lower()
        tail = text.strip()[-1500:]
        if quota or any(w in low for w in LIMIT_WORDS):
            raise ProviderError(f"{self.name}: plan limit reached: {tail}", "rate_limit", retry_at=parse_reset(text))
        if any(w in low for w in AUTH_WORDS):
            raise ProviderError(f"{self.name}: not signed in. Run `{self.cfg.get('command')}` once on this machine "
                                f"and log in with your plan account. ({tail[-300:]})", "auth")
        raise ProviderError(f"{self.name} exited {code}: {tail}", "error")


def new_files(workdir: Path, since: float, max_bytes: int = 5_000_000) -> list[Path]:
    """Files a CLI agent created or changed in its task folder (excluding Tazrivo's own config files)."""
    out = []
    for f in workdir.rglob("*"):
        rel = f.relative_to(workdir)
        if not f.is_file() or rel.parts[0] in CONFIG_FILES or rel.parts[0] in (".git", "node_modules"):
            continue
        if f.stat().st_mtime >= since - 1 and f.stat().st_size <= max_bytes:
            out.append(f)
    return out


__all__ = ["CLIProvider", "parse_reset", "new_files", "gateway_command", "time"]
