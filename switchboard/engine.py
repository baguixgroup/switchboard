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
from .inventory import build_inventory, mcp_catalog, resolve_tool_id, tools_catalog
from .mcp_hub import MCPHub
from .nodes import NodeRegistry
from .providers import ProviderError, RunRequest, RunResult, build_providers
from .providers.cli_bridge import CLIProvider
from .router import PRIORITY, Plan, Router, TaskProfile, classify
from .store import Store
from .security import LocalSecurity, require_execution_enabled, protected_mode
from .toolbox import ToolBox

log = logging.getLogger("switchboard.engine")
MIRROR_MAX_BYTES = 20 * 1024 * 1024
CLI_TOOL_IDS = {"claude-code": "claude_code", "codex": "codex", "gemini-cli": "gemini_cli",
                "copilot-cli": "copilot_cli", "grok-build": "grok_cli"}
GATEWAY_TTL = 3 * 3600
MANAGEMENT_TOOLS = "__machine_management__"


class Engine:
    def __init__(self, config):
        self.config = config
        self.store = Store(config.db_path)
        self.security = LocalSecurity(config, self.store)
        from .privacy_gateway import TextGateway
        self.text_gateway = TextGateway(config, self.security)
        from .privacy_tools import LocalTools
        self.local_tools = LocalTools(config, self.security, self.text_gateway)
        from .retention import Retention
        self.retention = Retention(config, self.security)
        self.catalog = Catalog.load(config)
        self.providers = build_providers(config)
        self.ledger = Ledger(config, self.store, lambda p: p in self.available_providers())
        self.router = Router(self.catalog, self.ledger, config.router)
        self.mcp = MCPHub(config)
        self.executor = Executor(config, self.mcp)
        self.nodes = NodeRegistry(config.node_name, self.executor)
        self.running: dict[str, asyncio.Task] = {}
        self.gateways: dict[str, tuple[ToolBox, float]] = {}
        self._avail: tuple[float, set[str]] = (0.0, set())
        self._bg: set[asyncio.Task] = set()
        self.installing: dict[tuple[str, str], str] = {}
        self.install_requests: dict[str, list[dict]] = {}
        from .studio import Studio
        self.studio = Studio(self)
        from .companion import Companion
        self.companion = Companion(self)
        from .capabilities.hub import CapabilityHub
        self.capabilities = CapabilityHub(self)

    # ---- discovery ----------------------------------------------------------------
    def available_providers(self, refresh: bool = False) -> set[str]:
        if protected_mode(self.config):
            return set()
        ts, cached = self._avail
        if refresh or time.time() - ts > 30:
            cached = {n for n, p in self.providers.items() if p.available()}
            self._avail = (time.time(), cached)
        # Plan CLIs count as available when any online machine has them installed.
        return cached | {n for n in self.providers if n not in cached and self.cli_location(n)}

    def cli_tool_id(self, provider: str) -> str | None:
        p = self.providers.get(provider)
        if not p or p.kind != "cli":
            return None
        return p.cfg.get("tool_id") or CLI_TOOL_IDS.get(provider)

    def cli_location(self, provider: str) -> str | None:
        """Machine to run a plan CLI on: this one if installed, else an online agent that has it."""
        if protected_mode(self.config):
            return None
        p = self.providers.get(provider)
        if not p or p.kind != "cli":
            return None
        if p.available():
            return self.config.node_name
        tid = self.cli_tool_id(provider)
        for n in self.nodes.list():
            if not n["online"] or n["local"]:
                continue
            if provider in (n.get("cli_providers") or []) or (not p.cfg.get("required_feature") and
                    ((n.get("tools") or {}).get(tid) or {}).get("installed")):
                return n["node"]
        return None

    def limits(self) -> dict[str, dict]:
        return self.store.active_limits()

    def mark_limited(self, provider: str, exc: ProviderError) -> float:
        until = exc.retry_at
        if not until:
            for src in self.ledger.sources:
                if src.provider == provider and src.reset:
                    until = src.reset.bounds(datetime.now().astimezone())[1].timestamp()
                    break
        until = until or time.time() + 3600
        self.store.set_limit(provider, until, str(exc)[:300])
        group = (self.config.providers.get(provider) or {}).get("quota_group", provider)
        for name, cfg in self.config.providers.items():
            if name != provider and (cfg or {}).get("quota_group", name) == group:
                self.store.set_limit(name, until, str(exc)[:300])
        log.info("%s hit its plan limit until %s", provider, datetime.fromtimestamp(until))
        return until

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
        require_execution_enabled(self.config)
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
                   images: int = 0, history_tokens: int = 0, billing: str | None = None,
                   direct_only: bool = False) -> Plan:
        profile = await self.classify(prompt, images=images, attachments=images, history_tokens=history_tokens)
        hinted = list(profile.tool_servers)
        locations = self.nodes.mcp_locations()
        if tools == "none" or tools is False:
            profile.tool_servers = []
        elif isinstance(tools, (list, tuple)) and tools:
            profile.tool_servers = [t for t in tools if t in locations]
        else:
            profile.tool_servers = [t for t in profile.tool_servers if t in locations]
        if tools != "none" and tools is not False and re.search(r"\b(?:install|installed|installation|setup|set up)\b", prompt, re.I):
            profile.tool_servers.append(MANAGEMENT_TOOLS)
        if profile.tool_servers and "tools" not in profile.needs:
            profile.needs.append("tools")
        if not profile.tool_servers and "tools" in profile.needs:
            profile.needs.remove("tools")
        image_redirect = "image_gen" in profile.needs and (provider == "codex" or model == "codex:default")
        if image_redirect:
            provider, model = "codex-images", "codex-images:default"
        plan = self.router.plan(prompt, self.available_providers(), profile, provider=provider or None,
                                model=model or None, effort=effort or None, mode=mode or None,
                                billing=billing or None, limits=self.limits(), allow_handoff=not direct_only)
        plan.suggestions = advisor.suggest(profile.category, hinted, self.nodes.list())
        if image_redirect:
            plan.reasons.append("uses Codex's native image generation on your ChatGPT plan")
        missing = [s for s in hinted if s not in locations]
        if missing:
            plan.warnings.append(f"This task could use {', '.join(missing)} but no connected machine runs it yet "
                                 f"(see suggestions).")
        return plan

    # ---- tools --------------------------------------------------------------------------
    async def build_toolbox(self, plan: Plan | None, task_dir: Path, servers: list[str] | None = None,
                            include_save: bool = True) -> ToolBox:
        require_execution_enabled(self.config)
        tb = ToolBox()
        locations = self.nodes.mcp_locations()
        for server in (servers if servers is not None else plan.tools):
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
        self._add_builtin_tools(tb, task_dir, include_save)
        return tb

    async def create_gateway(self, servers: list[str], task_rel: str | None) -> tuple[str, ToolBox]:
        """Tool gateway session for a plan CLI: the CLI's MCP client talks to these tools via the hub."""
        require_execution_enabled(self.config)
        now = time.time()
        for gid in [g for g, (_, ts) in self.gateways.items() if now - ts > GATEWAY_TTL]:
            self.gateways.pop(gid, None)
        task_dir = self.config.output_dir
        if task_rel:
            task_dir = outputs.safe_join(self.config.output_dir, task_rel)
            task_dir.mkdir(parents=True, exist_ok=True)
        tb = await self.build_toolbox(None, task_dir, servers=servers, include_save=False)
        gid = task_rel or uuid.uuid4().hex
        self.gateways[gid] = (tb, now)
        return gid, tb

    def _remote_tool(self, tb: ToolBox, node: str, server: str, tool: str):
        async def call(args: dict):
            require_execution_enabled(self.config)
            payload = await self.nodes.submit(node, "mcp_call", {"server": server, "tool": tool, "args": args})
            for i, img in enumerate(payload.get("images") or [], start=1):
                ext = (img.get("mime") or "image/png").split("/")[-1].replace("jpeg", "jpg")
                tb.artifacts.append((f"tool-images/{server}-{tool}-{len(tb.artifacts) + 1}.{ext}", base64.b64decode(img["data"])))
            return payload.get("text", ""), bool(payload.get("is_error"))
        return call

    def _add_builtin_tools(self, tb: ToolBox, task_dir: Path, include_save: bool = True) -> None:
        async def save_output_file(args: dict):
            target = outputs.safe_join(task_dir / "files", str(args["path"]))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(args.get("content", "")), encoding="utf-8")
            return f"saved {target}"

        if include_save:
            tb.add("save_output_file", "Save a text file (code, config, document) into this task's output folder.",
                   {"type": "object", "properties": {"path": {"type": "string", "description": "relative path, e.g. src/main.py"},
                                                     "content": {"type": "string"}}, "required": ["path", "content"]},
                   save_output_file, origin="switchboard/save_output_file")

        async def list_machines(args: dict):
            return [{"node": n["node"], "platform": n.get("platform"), "online": n.get("online"),
                     "installed_apps": [t["name"] for t in (n.get("tools") or {}).values() if t.get("installed")],
                     "devices": n.get("devices", []), "mcp_servers": list((n.get("mcp") or {}).keys())}
                    for n in self.nodes.list()]

        tb.add("list_machines_and_devices", "List connected machines, their devices (phones, consoles, servers) and MCP servers.",
               {"type": "object", "properties": {}}, list_machines, origin="switchboard/list_machines_and_devices")

        async def installable_apps(args: dict):
            return [{"id": name, "name": entry.get("name", name), "aliases": entry.get("aliases", []),
                     "platforms": list(entry.get("install", {})), "unsupported": entry.get("unsupported", {})}
                    for name, entry in tools_catalog().items()]

        tb.add("list_installable_apps", "List supported apps and tools with their catalog ids and platforms. Never guess installer commands.",
               {"type": "object", "properties": {}}, installable_apps, origin="switchboard/list_installable_apps")

        async def request_install(args: dict):
            tool = resolve_tool_id(str(args.get("tool", "")))
            if not tool:
                return json.dumps({"ok": False, "output": "Unknown app. Use list_installable_apps first."}), True
            node = self.resolve_node(str(args.get("node", "")))
            preflight = await self.nodes.submit(node, "install_tool", {"tool": tool, "approved": False}, timeout=60)
            if preflight.get("ok") or not preflight.get("needs_approval"):
                return json.dumps(preflight), not preflight.get("ok", False)
            request = {"node": node, "tool": tool, "name": tools_catalog()[tool]["name"], "command": preflight.get("command")}
            if self.config.permissions.get("install_tools") and not preflight.get("agreements"):
                request["task_id"] = self.start_install(node, tool)
            requests = self.install_requests.setdefault(str(task_dir), [])
            if not any(r["node"] == node and r["tool"] == tool for r in requests):
                requests.append(request)
            return json.dumps({"ok": True, **request, "needs_approval": "task_id" not in request,
                               "instruction": "A confirmation button will appear with your answer. Do not claim installation is complete before the installation task succeeds."})

        tb.add("request_app_install", "Request installation of a catalog app on a connected machine. User confirmation is required by default; agreements are never accepted automatically.",
               {"type": "object", "properties": {"node": {"type": "string"}, "tool": {"type": "string"}},
                "required": ["node", "tool"]}, request_install, origin="switchboard/request_app_install")

        async def install_status(args: dict):
            task = self.store.get_task(str(args.get("task_id", "")))
            if not task or (task.get("plan") or {}).get("kind") != "installation":
                return {"error": "No such installation task."}
            return {k: task.get(k) for k in ("id", "status", "result", "error")}

        tb.add("get_installation_status", "Check an installation task. Only a done status means installation was verified.",
               {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]},
               install_status, origin="switchboard/get_installation_status")

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
    def resolve_node(self, value: str) -> str:
        online = [n for n in self.nodes.list() if n["online"]]
        if any(n["node"] == value for n in online):
            return value
        platform = {"pc": "windows", "windows": "windows", "mac": "macos", "macos": "macos", "pi": "pi", "raspberry pi": "pi"}.get(value.casefold())
        matches = [n["node"] for n in online if platform and n.get("platform") == platform]
        if len(matches) == 1:
            return matches[0]
        raise ValueError("Choose an online machine by its exact name from the Machines page.")

    def install_routes(self, node: str) -> list[dict]:
        return [{**route, "controller": n["node"], "id": n["node"] + "/" + route["id"], "profile": route["id"]}
                for n in self.nodes.list() if n["online"] for route in n.get("install_routes", [])
                if route.get("target_node") == node]

    def install_route(self, node: str, route_id: str) -> dict:
        route = next((r for r in self.install_routes(node) if r["id"] == route_id), None)
        if not route:
            raise ValueError("That saved administrator connection is unavailable for this machine.")
        return route

    def start_install(self, node: str, tool: str, accept_agreements: bool = False, auth_route: str | None = None, auth_handle: str | None = None) -> str:
        require_execution_enabled(self.config)
        node = self.resolve_node(node)
        tool = resolve_tool_id(tool)
        if not tool:
            raise ValueError("Unknown app. Choose one from Install apps.")
        route = self.install_route(node, auth_route) if auth_route else None
        key = (node, tool)
        if key in self.installing:
            return self.installing[key]
        task_id = "install-" + datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        prompt = f"Install {tools_catalog()[tool]['name']} on {node}"
        plan = {"kind": "installation", "provider": "switchboard", "model": "installer", "model_label": "Remote installer",
                "node": node, "tool": tool, "auth_route": route, "effective_cost_usd": 0, "tools": [],
                "profile": {"category": "devops", "complexity": 1}, "est_cost_usd": 0}
        self.store.save_task(task_id, "queued", prompt, plan)
        self.installing[key] = task_id
        async_task = asyncio.create_task(self._run_install(task_id, prompt, plan, accept_agreements, auth_handle))
        self.running[task_id] = async_task

        def finished(_):
            self.running.pop(task_id, None)
            self.installing.pop(key, None)

        async_task.add_done_callback(finished)
        return task_id

    async def _run_install(self, task_id: str, prompt: str, plan: dict, accept_agreements: bool, auth_handle: str | None = None) -> None:
        folder = outputs.task_folder(self.config.output_dir, prompt)
        self.store.save_task(task_id, "running", output_path=str(folder), result=f"Installing on {plan['node']}…")
        try:
            route = plan.get("auth_route")
            payload = {"tool": plan["tool"], "approved": True, "accept_agreements": accept_agreements}
            if route:
                payload.update(profile=route["profile"], target_node=plan["node"], auth_handle=auth_handle)
            result = await self.nodes.submit(route["controller"] if route else plan["node"],
                                             "admin_install" if route else "install_tool", payload, timeout=1900)
            if result.get("inventory"):
                self.nodes.heartbeat(result["inventory"])
            if not result.get("ok"):
                raise RuntimeError(result.get("output") or "Installation was not completed on the target machine.")
            if not result.get("inventory"):
                inventory = await self.nodes.submit(plan["node"], "inventory", {"start_mcp": False}, timeout=90)
                self.nodes.heartbeat(inventory)
                if not inventory.get("tools", {}).get(plan["tool"], {}).get("installed"):
                    raise RuntimeError("The administrator installer finished, but the target agent cannot detect the app. Rescan before retrying.")
            text = result.get("output") or "Installed and verified."
            outputs.save_task_output(folder, prompt, text, {"task_id": task_id, "node": plan["node"], "tool": plan["tool"]})
            self.store.save_task(task_id, "done", result=text)
        except asyncio.CancelledError:
            self.store.save_task(task_id, "failed", error="Hub stopped before installation status was received. The installer may still be running on the target; rescan before retrying.")
            raise
        except Exception as exc:
            self.store.save_task(task_id, "failed", error=str(exc))
    def start_task(self, prompt: str, plan: Plan, images: list[Path] | None = None,
                   history: list[dict] | None = None, isolated: bool = False, read_only: bool = False,
                   estimate_allowance: float | None = None, system_prompt: str | None = None) -> str:
        require_execution_enabled(self.config)
        task_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        self.store.save_task(task_id, "queued", prompt, plan.to_dict())
        t = asyncio.create_task(self.run_task(task_id, prompt, plan, images or [], history or [], isolated=isolated,read_only=read_only,estimate_allowance=estimate_allowance,system_prompt=system_prompt))
        self.running[task_id] = t
        t.add_done_callback(lambda _t: self.running.pop(task_id, None))
        return task_id

    async def run_task(self, task_id: str, prompt: str, plan: Plan, images: list[Path], history: list[dict], isolated: bool = False, read_only: bool = False,
                       estimate_allowance: float | None = None, system_prompt: str | None = None) -> dict:
        require_execution_enabled(self.config)
        self.store.save_task(task_id, "running", prompt, plan.to_dict())
        task_dir = outputs.task_folder(self.config.output_dir, prompt)
        attempts: list[Plan] = [plan]
        if self.config.router.get("failover", True) and not plan.manual:
            for alt in plan.alternatives:
                alternate = self.catalog.find(alt["key"])
                if alternate and "handoff" in alternate.features:
                    continue  # a manual handoff must be chosen and confirmed by the user
                try:
                    fallback=self.router.plan(prompt, self.available_providers(),
                                              TaskProfile(**plan.profile.__dict__), model=alt["key"],
                                              effort=plan.effort, billing=plan.billing, limits=self.limits())
                    if estimate_allowance is not None and (fallback.effective_cost_usd>estimate_allowance+1e-9 or fallback.needs_confirmation):
                        continue
                    attempts.append(fallback)
                except ValueError:
                    pass
        errors: list[str] = []
        result = used = toolbox = None
        for attempt in attempts:
            m = self.catalog.find(f"{attempt.provider}:{attempt.model}")
            prov = self.providers.get(attempt.provider)
            if not m or not prov:
                continue
            is_cli = prov.kind == "cli"
            if attempt.provider in self.limits():
                continue
            toolbox = None
            if attempt.tools and "tools" in m.features and not is_cli:
                toolbox = await self.build_toolbox(attempt, task_dir)
            task_rel = task_dir.relative_to(self.config.output_dir).as_posix()
            remote_cli = is_cli and self.cli_location(attempt.provider) not in (None, self.config.node_name)
            note = self.recent_work_note(exclude=task_id) if not isolated and is_cli and "image_gen" not in m.features else ""
            run_prompt = prompt + (f"\n\n---\n{note}" if note else "")
            if not remote_cli:  # remote agents fill in their own output folder path
                run_prompt = run_prompt.replace("{OUTPUT}", str(self.config.output_dir))
            req = RunRequest(prompt=run_prompt, model=m, effort=attempt.native_effort, max_tokens=attempt.max_tokens,
                             history=history, images=images, workdir=task_dir,
                             allow_edits=not read_only and bool((self.config.permissions or {}).get("cli_agents_can_edit")),
                             max_tool_rounds=int(self.config.router.get("max_tool_rounds", 25)),
                             tool_servers=list(attempt.tools) if (is_cli and "tools" in m.features) else [],
                             task_rel=task_rel,
                             read_dirs=[str(self.config.output_dir)] if (is_cli and note and not remote_cli) else [])
            if system_prompt is not None:req.system=system_prompt
            try:
                if is_cli:
                    result = await self._run_cli(prov, req, task_dir)
                    gw = self.gateways.pop(task_rel, None)
                    if gw:
                        toolbox = gw[0]
                else:
                    result = await prov.run(req, toolbox)
                used = attempt
                break
            except ProviderError as exc:
                errors.append(f"{attempt.provider}:{attempt.model}: {exc}")
                log.warning("task %s attempt failed: %s", task_id, exc)
                if exc.kind == "rate_limit" and is_cli:
                    until = self.mark_limited(attempt.provider, exc)
                    errors[-1] += f" (plan limit; resets {datetime.fromtimestamp(until):%a %H:%M})"
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
        if result.handoff:
            self.store.save_task(task_id, "waiting", result=result.text, output_path=str(task_dir), cost_usd=0.0,
                                 plan=dict(plan.to_dict(), executed=f"{used.provider}:{used.model}", tool_calls=[],
                                           handoff=dict(result.handoff, watching=None)))
            return self.store.get_task(task_id)
        final = result.text + (f"\n\n[Failover: {len(errors)} earlier attempt(s) failed]" if errors else "")
        self.store.save_task(task_id, "done", result=final, output_path=str(task_dir), cost_usd=result.cost_usd,
                             plan=dict(plan.to_dict(), executed=f"{used.provider}:{used.model}", tool_calls=meta["tool_calls"],
                                       install_requests=self.install_requests.pop(str(task_dir), [])))
        self._spawn(self._mirror(task_dir))
        return self.store.get_task(task_id)

    async def _run_cli(self, prov: CLIProvider, req: RunRequest, task_dir: Path) -> RunResult:
        """Run a plan CLI here, or on the agent machine where it is installed and logged in."""
        require_execution_enabled(self.config)
        where = self.cli_location(prov.name)
        if where is None:
            raise ProviderError(f"{prov.name} is not installed on any connected machine", "unavailable")
        if where == self.config.node_name:
            return await prov.run(req, None)
        payload = {"provider": prov.name, "cfg": prov.cfg, "prompt": req.prompt, "model": req.model.id,
                   "effort": req.effort, "allow_edits": req.allow_edits, "tool_servers": req.tool_servers,
                   "task_rel": req.task_rel, "history": req.history, "read_output_dir": "{OUTPUT}" in req.prompt,"system":req.system}
        res = await self.nodes.submit(where, "cli_run", payload, timeout=float(prov.cfg.get("timeout_seconds", 1800)) + 60)
        if res.get("error"):
            raise ProviderError(f"{res['error']} (on {where})", res.get("error_kind", "error"), retry_at=res.get("retry_at"))
        files = [(f"files-from-{where}/{f['path']}", base64.b64decode(f["b64"])) for f in res.get("files") or []]
        text = res["text"]
        if res.get("created"):
            text += f"\n\n[{len(res['created'])} file(s) written on {where}: {', '.join(res['created'][:10])}]"
        return RunResult(text=text, provider=prov.name, model=req.model.id, input_tokens=res.get("input_tokens", 0),
                         output_tokens=res.get("output_tokens", 0), cost_usd=0.0, requests=1, stop_reason="cli",
                         files=files, notional_cost_usd=res.get("notional_cost_usd"))

    # ---- shared work, handoffs, attached files -------------------------------------------------
    def recent_work_note(self, exclude: str | None = None, limit: int = 5, days: int = 14) -> str:
        """Where earlier tasks put their files, so the next app can pick up where another left off."""
        if not self.config.router.get("share_recent_work", True):
            return ""
        lines = []
        cutoff = time.time() - days * 86400
        for t in self.store.recent_tasks(40):
            if t["id"] == exclude or t["status"] not in ("done", "waiting") or t["ts"] < cutoff or not t.get("output_path"):
                continue
            folder = Path(t["output_path"])
            try:
                rel = folder.relative_to(self.config.output_dir).as_posix()
            except ValueError:
                continue
            names = [p.relative_to(folder).as_posix() for p in sorted(folder.rglob("*"))
                     if p.is_file() and p.name not in ("prompt.md", "response.md", "meta.json", "error.txt")
                     and not p.relative_to(folder).as_posix().startswith(".")][:6] if folder.exists() else []
            when = datetime.fromtimestamp(t["ts"]).strftime("%d %b %H:%M")
            lines.append(f"- {when} \"{(t.get('prompt') or '')[:80]}\" -> {{OUTPUT}}/{rel}/" +
                         (f" (files: {', '.join(names)})" if names else ""))
            if len(lines) >= limit:
                break
        if not lines:
            return ""
        return ("Earlier work in the user's Tazrivo output folder (each folder has prompt.md and response.md). "
                "If this task continues earlier work, read those files first:\n" + "\n".join(lines))

    def _task_dir(self, task_id: str) -> Path:
        t = self.store.get_task(task_id)
        if not t or not t.get("output_path"):
            raise ValueError("no such task")
        return Path(t["output_path"])

    def attach_file(self, task_id: str, name: str, data: bytes, source: str = "you") -> dict:
        """Add a file to a task (an image saved from ChatGPT/Grok, or anything made in another app)."""
        folder = self._task_dir(task_id)
        safe = re.sub(r"[^A-Za-z0-9._ -]", "_", Path(name).name).strip() or "file"
        target = outputs.safe_join(folder, f"files/{safe}")
        target.parent.mkdir(parents=True, exist_ok=True)
        n = 2
        while target.exists():
            target = target.with_name(f"{Path(safe).stem}-{n}{Path(safe).suffix}")
            n += 1
        target.write_bytes(data)
        t = self.store.get_task(task_id)
        if t["status"] == "waiting":
            plan = dict(t.get("plan") or {})
            plan["handoff"] = dict(plan.get("handoff") or {}, watching=None, received=target.name)
            text = (t.get("result") or "") + f"\n\n**Image received** from {source}: `{target.name}`"
            self.store.save_task(task_id, "done", result=text, plan=plan)
            (folder / "response.md").write_text(text, encoding="utf-8")
        self._spawn(self._mirror(folder))
        return {"path": target.relative_to(folder).as_posix(), "size": len(data)}

    async def handoff_open(self, task_id: str, node: str) -> dict:
        """Open the plan's app on one of your computers with the prompt copied, then watch for the image."""
        require_execution_enabled(self.config)
        t = self.store.get_task(task_id)
        h = ((t or {}).get("plan") or {}).get("handoff")
        if not h:
            raise ValueError("this task isn't waiting for an app")
        res = await self.nodes.submit(node, "open_app", h, timeout=60)
        plan = dict(t["plan"])
        plan["handoff"] = dict(h, watching=node, watch_started=time.time())
        self.store.save_task(task_id, t["status"], plan=plan)
        if h.get("task_type") not in ("writing", "chat"):
            self._spawn(self._watch_for_image(task_id, node))
        return res

    def complete_writing(self, task_id: str, text: str) -> dict:
        """Save a user-reviewed writing-service result; never scrape the service's account."""
        task = self.store.get_task(task_id)
        handoff = ((task or {}).get("plan") or {}).get("handoff") or {}
        if not task or task["status"] != "waiting" or handoff.get("task_type") not in ("writing", "chat"):
            raise ValueError("This task is not waiting for a text answer.")
        if not text.strip():
            raise ValueError("Paste the edited text first.")
        folder = self._task_dir(task_id)
        plan = dict(task["plan"])
        plan["handoff"] = dict(handoff, watching=None, received="edited-text.md")
        (folder / "response.md").write_text(text, encoding="utf-8")
        self.store.save_task(task_id, "done", result=text, plan=plan)
        self._spawn(self._mirror(folder))
        return {"ok": True}

    async def _watch_for_image(self, task_id: str, node: str, minutes: int = 20) -> None:
        try:
            res = await self.nodes.submit(node, "watch_folder", {"timeout": minutes * 60}, timeout=minutes * 60 + 60)
        except Exception as exc:
            res = {"error": str(exc)}
        t = self.store.get_task(task_id)
        if not t or t["status"] != "waiting":
            return
        if res.get("b64"):
            self.attach_file(task_id, res["name"], base64.b64decode(res["b64"]), source=f"{res.get('folder', 'Downloads')} on {node}")
            return
        plan = dict(t["plan"])
        plan["handoff"] = dict(plan.get("handoff") or {}, watching=None,
                               watch_note=res.get("error") or f"No new image appeared on {node} within {minutes} minutes.")
        self.store.save_task(task_id, "waiting", plan=plan)

    def reload_catalog(self) -> None:
        self.catalog = Catalog.load(self.config)
        self.router.catalog = self.catalog

    def plans_status(self) -> list[dict]:
        limits = self.limits()
        out = []
        for name, p in self.providers.items():
            if p.kind == "handoff" and p.available() and any("chat" in m.features for m in self.catalog.for_provider(name)):
                out.append({"provider": name, "label": p.cfg.get("label", name), "kind": "app",
                            "where": "App or website", "status": "ready", "can_sign_in": False})
                continue
            if p.kind != "cli":
                continue
            where = self.cli_location(name)
            lim = limits.get(name)
            status = "limit reached" if lim else "ready" if where else "not installed"
            out.append({"provider": name, "label": next((m.label for m in self.catalog.for_provider(name)), name),
                        "where": where, "status": status, "resets": lim["until"] if lim else None,
                        "detail": lim["reason"] if lim else None, "tool_id": self.cli_tool_id(name),
                        "can_sign_in": bool(p.cfg.get("login_args")) or name in ("gemini-cli",),
                        "command": p.cfg.get("command")})
        return out

    async def _mirror(self, task_dir: Path) -> None:
        require_execution_enabled(self.config)
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
        untracked = sorted(p for p in avail if p not in tracked and self.providers[p].kind not in ("mock", "cli", "handoff"))
        month_start = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
        return {"sources": statuses, "alerts": [a.to_dict() for a in self.ledger.alerts()],
                "untracked_providers": untracked, "spend_this_month": self.store.usage_summary(month_start),
                "plans": self.plans_status(), "billing": self.config.router.get("billing", "plans_first")}

    async def refresh_balances(self) -> dict:
        require_execution_enabled(self.config)
        return await balances.refresh_all(self.config, self.store)

    async def scheduler(self) -> None:
        require_execution_enabled(self.config)
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
        self.retention.close()
        self.local_tools.close()
        self.text_gateway.close()
        await self.capabilities.close()
        await self.studio.close()
        for t in list(self.running.values()) + list(self._bg):
            t.cancel()
        await self.mcp.close()
