"""The hub's engine: plan -> gather tools from every machine -> run with failover -> account -> save outputs."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
import uuid
from datetime import datetime
from pathlib import Path

from . import advisor, balances, notify, outputs
from .catalog import Catalog
from .credits import Ledger
from .executor import Executor
from .inventory import build_inventory, mcp_catalog
from .mcp_hub import MCPHub
from .nodes import NodeRegistry
from .providers import ProviderError, RunRequest, build_providers
from .router import PRIORITY, Plan, Router, TaskProfile, classify
from .store import Store
from .toolbox import ToolBox

log = logging.getLogger("switchboard.engine")
MIRROR_MAX_BYTES = 20 * 1024 * 1024


class Engine:
    def __init__(self, config):
        self.config = config
        self.store = Store(config.db_path)
        self.catalog = Catalog.load(config)
        self.providers = build_providers(config)
        self.ledger = Ledger(config, self.store, lambda p: p in self.available_providers())
        self.router = Router(self.catalog, self.ledger, config.router)
        self.mcp = MCPHub(config)
        self.executor = Executor(config, self.mcp)
        self.nodes = NodeRegistry(config.node_name, self.executor)
        self.running: dict[str, asyncio.Task] = {}
        self._avail: tuple[float, set[str]] = (0.0, set())
        self._bg: set[asyncio.Task] = set()

    # ---- discovery ----------------------------------------------------------------
    def available_providers(self, refresh: bool = False) -> set[str]:
        ts, cached = self._avail
        if refresh or time.time() - ts > 30:
            cached = {n for n, p in self.providers.items() if p.available()}
            self._avail = (time.time(), cached)
        return cached

    async def refresh_local_inventory(self, start_mcp: bool = True) -> dict:
        inv = await build_inventory(self.config, self.mcp, start_mcp=start_mcp)
        self.nodes.set_local(inv)
        return inv

    def tool_hints(self) -> dict[str, list[str]]:
        cat = mcp_catalog()
        names = set(cat) | set(self.nodes.mcp_locations())
        return {n: list((cat.get(n) or {}).get("hints") or []) for n in names}

    # ---- planning -------------------------------------------------------------------
    async def classify(self, prompt: str, images: int = 0, attachments: int = 0, history_tokens: int = 0) -> TaskProfile:
        profile = classify(prompt, attachments=attachments, image_attachments=images, history_tokens=history_tokens,
                           tool_hints=self.tool_hints())
        if self.config.router.get("classifier") == "llm":
            profile = await self._llm_classify(prompt, profile)
        return profile

    async def _llm_classify(self, prompt: str, profile: TaskProfile) -> TaskProfile:
        avail = self.available_providers()
        cands = sorted((m for m in self.catalog.all() if m.provider in avail and m.q >= 6 and
                        not ({"image_gen", "cli"} & m.features)), key=lambda m: m.price_in + m.price_out)
        if not cands:
            return profile
        m = cands[0]
        q = ("Classify the task below. Reply with JSON only: "
             f'{{"category": one of {PRIORITY}, "complexity": integer 1-5}}.\n\nTask:\n{prompt[:4000]}')
        try:
            res = await self.providers[m.provider].run(RunRequest(q, m, effort=m.native_effort("low"), max_tokens=300,
                                                                  system=None))
            src = self.ledger.pick_source(m.provider, m.id)
            self.ledger.record(m.provider, m.id, src.source.id if src else None, res.input_tokens, res.output_tokens,
                               res.cost_usd, None, res.requests)
            data = json.loads(re.search(r"\{.*\}", res.text, re.S).group(0))
            if data.get("category") in PRIORITY:
                profile.category = data["category"]
            profile.complexity = max(1, min(5, int(data.get("complexity", profile.complexity))))
            profile.signals.append(f"llm classifier: {m.id}")
        except Exception as exc:
            profile.signals.append(f"llm classifier failed ({exc}); used keywords")
        return profile

    async def plan(self, prompt: str, provider: str | None = None, model: str | None = None,
                   effort: str | None = None, mode: str | None = None, tools: str | list | None = "auto",
                   images: int = 0, history_tokens: int = 0) -> Plan:
        profile = await self.classify(prompt, images=images, attachments=images, history_tokens=history_tokens)
        hinted = list(profile.tool_servers)
        locations = self.nodes.mcp_locations()
        if tools == "none" or tools is False:
            profile.tool_servers = []
        elif isinstance(tools, (list, tuple)) and tools:
            profile.tool_servers = [t for t in tools if t in locations]
        else:
            profile.tool_servers = [t for t in profile.tool_servers if t in locations]
        if profile.tool_servers and "tools" not in profile.needs:
            profile.needs.append("tools")
        if not profile.tool_servers and "tools" in profile.needs:
            profile.needs.remove("tools")
        plan = self.router.plan(prompt, self.available_providers(), profile, provider=provider or None,
                                model=model or None, effort=effort or None, mode=mode or None)
        plan.suggestions = advisor.suggest(profile.category, hinted, self.nodes.list())
        missing = [s for s in hinted if s not in locations]
        if missing:
            plan.warnings.append(f"This task could use {', '.join(missing)} but no connected machine runs it yet "
                                 f"(see suggestions).")
        return plan

    # ---- tools --------------------------------------------------------------------------
    async def build_toolbox(self, plan: Plan, task_dir: Path) -> ToolBox:
        tb = ToolBox()
        locations = self.nodes.mcp_locations()
        for server in plan.tools:
            node = next((n for n in locations.get(server, []) if self.nodes.online(n)), None)
            if not node:
                continue
            if node == self.config.node_name:
                try:
                    tool_list = await self.mcp.tools(server)
                except Exception as exc:
                    log.warning("skipping MCP server %s: %s", server, exc)
                    continue
            else:
                tool_list = ((self.nodes.nodes.get(node) or {}).get("mcp") or {}).get(server, {}).get("tool_list", [])
            for t in tool_list:
                tb.add(f"{server}__{t['name']}", f"[{server} on {node}] {t.get('description', '')}", t.get("schema"),
                       self._remote_tool(tb, node, server, t["name"]), origin=f"{node}/{server}/{t['name']}")
        self._add_builtin_tools(tb, task_dir)
        return tb

    def _remote_tool(self, tb: ToolBox, node: str, server: str, tool: str):
        async def call(args: dict):
            payload = await self.nodes.submit(node, "mcp_call", {"server": server, "tool": tool, "args": args})
            for i, img in enumerate(payload.get("images") or [], start=1):
                ext = (img.get("mime") or "image/png").split("/")[-1].replace("jpeg", "jpg")
                tb.artifacts.append((f"tool-images/{server}-{tool}-{len(tb.artifacts) + 1}.{ext}", base64.b64decode(img["data"])))
            return payload.get("text", ""), bool(payload.get("is_error"))
        return call

    def _add_builtin_tools(self, tb: ToolBox, task_dir: Path) -> None:
        async def save_output_file(args: dict):
            target = outputs.safe_join(task_dir / "files", str(args["path"]))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(args.get("content", "")), encoding="utf-8")
            return f"saved {target}"

        tb.add("save_output_file", "Save a text file (code, config, document) into this task's output folder.",
               {"type": "object", "properties": {"path": {"type": "string", "description": "relative path, e.g. src/main.py"},
                                                 "content": {"type": "string"}}, "required": ["path", "content"]},
               save_output_file, origin="switchboard/save_output_file")

        async def list_machines(args: dict):
            return [{"node": n["node"], "platform": n.get("platform"), "online": n.get("online"),
                     "devices": n.get("devices", []), "mcp_servers": list((n.get("mcp") or {}).keys())}
                    for n in self.nodes.list()]

        tb.add("list_machines_and_devices", "List connected machines, their devices (phones, consoles, servers) and MCP servers.",
               {"type": "object", "properties": {}}, list_machines, origin="switchboard/list_machines_and_devices")

        level = (self.config.permissions or {}).get("device_actions", "read_only")
        if level != "none":
            async def device_action(args: dict):
                action = str(args.get("action"))
                if level == "read_only" and action not in ("info", "screenshot", "logs", "check", "ping"):
                    return f"'{action}' needs permissions.device_actions: all (read-only mode).", True
                node = args.get("node") or self.config.node_name
                res = await self.nodes.submit(node, "device_action", {"device": args["device"], "action": action,
                                                                       "args": args.get("args") or {}})
                return json.dumps(res), not res.get("ok", False)

            tb.add("device_action", "Act on a connected device or server: android/sim screenshot, logs, info, install, "
                   "launch, open_url; server check/deploy/run. Device ids come from list_machines_and_devices.",
                   {"type": "object", "properties": {"node": {"type": "string"}, "device": {"type": "string"},
                                                     "action": {"type": "string"}, "args": {"type": "object"}},
                    "required": ["device", "action"]}, device_action, origin="switchboard/device_action")

    # ---- running ------------------------------------------------------------------------
    def start_task(self, prompt: str, plan: Plan, images: list[Path] | None = None,
                   history: list[dict] | None = None) -> str:
        task_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        self.store.save_task(task_id, "queued", prompt, plan.to_dict())
        t = asyncio.create_task(self.run_task(task_id, prompt, plan, images or [], history or []))
        self.running[task_id] = t
        t.add_done_callback(lambda _t: self.running.pop(task_id, None))
        return task_id

    async def run_task(self, task_id: str, prompt: str, plan: Plan, images: list[Path], history: list[dict]) -> dict:
        self.store.save_task(task_id, "running", prompt, plan.to_dict())
        task_dir = outputs.task_folder(self.config.output_dir, prompt)
        attempts: list[Plan] = [plan]
        if self.config.router.get("failover", True) and not plan.manual:
            for alt in plan.alternatives:
                try:
                    attempts.append(self.router.plan(prompt, self.available_providers(),
                                                     TaskProfile(**plan.profile.__dict__), model=alt["key"],
                                                     effort=plan.effort))
                except ValueError:
                    pass
        errors: list[str] = []
        result = used = toolbox = None
        for attempt in attempts:
            m = self.catalog.find(f"{attempt.provider}:{attempt.model}")
            prov = self.providers.get(attempt.provider)
            if not m or not prov:
                continue
            toolbox = await self.build_toolbox(attempt, task_dir) if ("tools" in m.features and attempt.tools) else None
            req = RunRequest(prompt=prompt, model=m, effort=attempt.native_effort, max_tokens=attempt.max_tokens,
                             history=history, images=images, workdir=task_dir,
                             allow_edits=bool((self.config.permissions or {}).get("cli_agents_can_edit")),
                             max_tool_rounds=int(self.config.router.get("max_tool_rounds", 25)))
            try:
                result = await prov.run(req, toolbox)
                used = attempt
                break
            except ProviderError as exc:
                errors.append(f"{attempt.provider}:{attempt.model}: {exc}")
                log.warning("task %s attempt failed: %s", task_id, exc)
                if not exc.failover_ok:
                    break
            except Exception as exc:  # unexpected: record and stop
                errors.append(f"{attempt.provider}:{attempt.model}: {type(exc).__name__}: {exc}")
                log.exception("task %s crashed", task_id)
                break
        if result is None:
            msg = "All attempts failed:\n" + "\n".join(errors)
            (task_dir / "error.txt").write_text(msg, encoding="utf-8")
            self.store.save_task(task_id, "failed", error=msg, output_path=str(task_dir))
            return self.store.get_task(task_id)

        src = self.ledger.pick_source(used.provider, used.model) if used is not plan else None
        source_id = (src.source.id if src else None) if used is not plan else used.source_id
        self.ledger.record(used.provider, used.model, source_id, result.input_tokens, result.output_tokens,
                           result.cost_usd, task_id, result.requests)
        meta = {"task_id": task_id, "provider": used.provider, "model": used.model, "effort": used.effort,
                "source": source_id, "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
                "cost_usd": round(result.cost_usd, 6), "notional_cost_usd": result.notional_cost_usd,
                "category": plan.profile.category, "complexity": plan.profile.complexity,
                "tool_calls": toolbox.calls if toolbox else [], "failed_attempts": errors,
                "finished": datetime.now().isoformat(timespec="seconds")}
        files = list(result.files) + (list(toolbox.artifacts) if toolbox else [])
        outputs.save_task_output(task_dir, prompt, result.text, meta, files)
        final = result.text + (f"\n\n[Failover: {len(errors)} earlier attempt(s) failed]" if errors else "")
        self.store.save_task(task_id, "done", result=final, output_path=str(task_dir), cost_usd=result.cost_usd,
                             plan=dict(plan.to_dict(), executed=f"{used.provider}:{used.model}", tool_calls=meta["tool_calls"]))
        self._spawn(self._mirror(task_dir))
        return self.store.get_task(task_id)

    async def _mirror(self, task_dir: Path) -> None:
        rel_root = task_dir.relative_to(self.config.output_dir)
        for n in self.nodes.list():
            if n["local"] or not n["online"] or not n.get("mirror_outputs"):
                continue
            for f in task_dir.rglob("*"):
                if f.is_file() and f.stat().st_size <= MIRROR_MAX_BYTES:
                    rel = (rel_root / f.relative_to(task_dir)).as_posix()
                    try:
                        await self.nodes.submit(n["node"], "write_output", {
                            "path": rel, "content_b64": base64.b64encode(f.read_bytes()).decode("ascii")}, timeout=120)
                    except Exception as exc:
                        log.warning("mirror to %s failed: %s", n["node"], exc)
                        break

    def _spawn(self, coro) -> None:
        t = asyncio.create_task(coro)
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)

    # ---- credits & scheduling -----------------------------------------------------------
    def credits(self) -> dict:
        self.ledger.reload()
        avail = self.available_providers()
        statuses = [st.to_dict() for st in self.ledger.statuses()]
        tracked = {s["provider"] for s in statuses}
        untracked = sorted(p for p in avail if p not in tracked and self.providers[p].kind != "mock")
        month_start = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
        return {"sources": statuses, "alerts": [a.to_dict() for a in self.ledger.alerts()],
                "untracked_providers": untracked, "spend_this_month": self.store.usage_summary(month_start)}

    async def refresh_balances(self) -> dict:
        return await balances.refresh_all(self.config, self.store)

    async def scheduler(self) -> None:
        every = max(5, int((self.config.notify or {}).get("check_every_minutes", 30))) * 60
        last_balance = 0.0
        while True:
            try:
                if time.time() - last_balance > 6 * 3600:
                    await self.refresh_balances()
                    last_balance = time.time()
                self.ledger.reload()
                await notify.dispatch(self.config, self.store, self.ledger.alerts())
                await self.refresh_local_inventory(start_mcp=False)
            except Exception:
                log.exception("scheduler tick failed")
            await asyncio.sleep(every)

    async def close(self) -> None:
        for t in list(self.running.values()) + list(self._bg):
            t.cancel()
        await self.mcp.close()
