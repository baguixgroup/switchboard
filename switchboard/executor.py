"""Executes jobs on THIS machine. Used in-process by the hub and by every agent."""
from __future__ import annotations

import asyncio
import base64
import logging
import time

from . import devices
from .inventory import build_inventory, detect_tools, resolve_tool_id, run_shell, tools_catalog
from .outputs import safe_join
from .paths import platform_tag

log = logging.getLogger("switchboard.executor")


class Executor:
    def __init__(self, config, mcp_hub):
        self.config = config
        self.mcp = mcp_hub

    async def execute(self, kind: str, payload: dict) -> dict:
        if kind == "ping":
            return {"pong": True, "node": self.config.node_name}
        if kind == "inventory":
            return await build_inventory(self.config, self.mcp, start_mcp=payload.get("start_mcp", True))
        if kind == "mcp_tools":
            return await self.mcp.all_tools()
        if kind == "mcp_call":
            return await self.mcp.call(payload["server"], payload["tool"], payload.get("args") or {},
                                       timeout=float(payload.get("timeout", 600)))
        if kind == "device_action":
            return await devices.device_action(self.config, payload["device"], payload["action"], payload.get("args"))
        if kind in ("admin_install", "admin_install_check"):
            from .remote_install import execute
            return await execute(self.config, payload, check=kind == "admin_install_check")
        if kind == "install_tool":
            return await self.install_tool(payload["tool"], bool(payload.get("approved")), bool(payload.get("accept_agreements")))
        if kind == "cli_run":
            return await self.cli_run(payload)
        if kind in ("mcp_add", "mcp_remove", "mcp_restart"):
            return await self.mcp_config(kind, payload)
        if kind == "cli_login":
            return await self.cli_login(payload)
        if kind == "open_app":
            return await self.open_app(payload)
        if kind == "watch_folder":
            return await self.watch_folder(payload)
        if kind == "write_output":
            target = safe_join(self.config.output_dir, payload["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(base64.b64decode(payload["content_b64"]))
            return {"written": str(target)}
        raise ValueError(f"unknown job kind: {kind}")

    async def install_tool(self, tool_id: str, approved: bool, accept_agreements: bool = False) -> dict:
        from .runtime import install_node, missing_node_commands, which

        tool_id = resolve_tool_id(tool_id)
        t = tools_catalog().get(tool_id)
        if not t:
            return {"ok": False, "output": f"unknown tool '{tool_id}'"}
        detected = await asyncio.to_thread(detect_tools)
        if detected.get(tool_id, {}).get("installed"):
            return {"ok": True, "already_installed": True, "tool": tool_id, "output": f"{t['name']} is already installed on {self.config.node_name}."}
        plat = platform_tag()
        command = (t.get("install") or {}).get(plat) or ((t.get("install") or {}).get("linux") if plat == "pi" else None)
        if not command:
            return {"ok": False, "output": (t.get("unsupported") or {}).get(plat) or f"no installer for {plat}"}
        if t.get("manual") or command.lower().startswith("install "):
            return {"ok": False, "manual": True, "needs_user_action": True, "output": command}
        agreements = (t.get("agreements") or {}).get(plat, [])
        if approved and agreements and not accept_agreements:
            return {"ok": False, "needs_approval": True, "agreements": agreements,
                    "output": "Explicit acceptance of the displayed installation terms is required."}
        macos_app = plat == "macos" and t.get("installer") == "macos_app"
        launcher = command.split()[0]
        if not macos_app and t.get("installer") != "node" and launcher not in ("npm", "npx") and not which(launcher):
            return {"ok": False, "needs_user_action": True,
                    "output": f"{launcher} is not available on {self.config.node_name}. Install that prerequisite there before installing {t['name']}."}
        node_install = t.get("installer") == "node"
        needs_node = node_install or ("node" in t.get("requires", []) and bool(missing_node_commands()))
        shown = ("Install verified Node.js LTS binaries from https://nodejs.org (includes npm/npx, per-user)"
                 + ("; then " + command if not node_install else "")) if needs_node else command
        if not approved:
            return {"ok": False, "needs_approval": True, "command": shown, "tool": tool_id, "agreements": agreements}
        if agreements and accept_agreements:
            command += " --accept-package-agreements"
        if needs_node:
            try:
                location = await asyncio.to_thread(install_node)
            except Exception as exc:
                return {"ok": False, "command": shown, "output": f"Could not prepare Node/npm: {exc}"}
            if node_install:
                return {"ok": True, "command": shown, "output": f"Node.js, npm and npx are available: {location}"}
        if command.startswith("sudo "):
            command = "sudo -n " + command[5:]  # never hang waiting for a password
        log.info("installing %s: %s", tool_id, command)
        if macos_app:
            from .installers import install_macos_app

            try:
                out = await asyncio.to_thread(install_macos_app, t)
                rc, err = 0, ""
            except Exception as exc:
                return {"ok": False, "command": shown, "output": f"Official app installation failed: {exc}"}
        else:
            rc, out, err = await run_shell(command, timeout=1800)
        text = (out + "\n" + err).strip()[-8000:]
        if rc != 0 and "a password is required" in err:
            text += f"\n\nThis needs your password. Run it yourself on {self.config.node_name}:\n  {command[8:]}"
        if rc != 0:
            return {"ok": False, "command": shown, "output": text or f"Installer exited with code {rc} on {self.config.node_name}."}
        if plat == "windows" and t.get("download_windows"):
            detected = await asyncio.to_thread(detect_tools)
            if not detected.get(tool_id, {}).get("installed"):
                from .installers import install_windows_msix
                try:
                    installed_output = await asyncio.to_thread(install_windows_msix, t)
                    text += "\nOfficial MSIX fallback:\n" + installed_output
                except Exception as exc:
                    return {"ok": False, "needs_user_action": True, "command": shown,
                            "output": text + f"\nOfficial MSIX installation failed: {exc}"}
        for attempt in range(5):
            detected = await asyncio.to_thread(detect_tools)
            if detected.get(tool_id, {}).get("installed"):
                return {"ok": True, "installed": True, "tool": tool_id, "command": shown, "output": text or f"Installed {t['name']}."}
            if attempt < 4:
                await asyncio.sleep(1)
        return {"ok": False, "needs_user_action": True, "command": shown,
                "output": text + f"\nThe installer exited successfully, but {t['name']} is not detected on {self.config.node_name}. Complete any installer/Store prompt there, then rescan."}

    async def cli_run(self, p: dict) -> dict:
        """Run a plan CLI on this machine (where it is installed and logged in) for the hub."""
        from .catalog import ModelInfo
        from .providers import ProviderError, RunRequest
        from .providers.cli_bridge import CLIProvider, new_files

        local = self.config.providers.get(p["provider"]) or {}
        prov = CLIProvider(p["provider"], {**p["cfg"], **local}, self.config)  # this machine's CLI settings win
        prompt = p["prompt"].replace("{OUTPUT}", str(self.config.output_dir))
        workdir = safe_join(self.config.output_dir, p["task_rel"]) if p.get("task_rel") else self.config.output_dir
        workdir.mkdir(parents=True, exist_ok=True)
        started = time.time()
        req = RunRequest(prompt=prompt, model=ModelInfo(provider=p["provider"], id=p["model"]), effort=p.get("effort"),
                         workdir=workdir, allow_edits=bool(p.get("allow_edits")), history=p.get("history") or [],
                         tool_servers=p.get("tool_servers") or [], task_rel=p.get("task_rel"),
                         read_dirs=[str(self.config.output_dir)] if p.get("read_output_dir") else [])
        try:
            res = await prov.run(req, None)
        except ProviderError as exc:
            return {"error": str(exc), "error_kind": exc.kind, "retry_at": exc.retry_at}
        created = new_files(workdir, started, max_bytes=20_000_000 if prov.cfg.get("output_kind") == "image" else 5_000_000)
        files, total = [], 0
        for f in created:
            size = f.stat().st_size
            if total + size > 20_000_000:
                break
            total += size
            files.append({"path": f.relative_to(workdir).as_posix(), "b64": base64.b64encode(f.read_bytes()).decode("ascii")})
        return {"text": res.text, "input_tokens": res.input_tokens, "output_tokens": res.output_tokens,
                "notional_cost_usd": res.notional_cost_usd, "files": files,
                "created": [f.relative_to(workdir).as_posix() for f in created]}

    async def mcp_config(self, kind: str, p: dict) -> dict:
        """Add, remove or restart an MCP server on this machine, then return a fresh inventory."""
        name = p["name"]
        servers = dict(self.config.user_raw.get("mcp_servers") or {})
        await self.mcp.stop(name)
        if kind == "mcp_add":
            servers[name] = p["entry"]
            self.config.set_section("mcp_servers", servers)
        elif kind == "mcp_remove":
            servers.pop(name, None)
            self.config.set_section("mcp_servers", servers)
        if kind != "mcp_remove":
            await self.mcp.ensure(name, timeout=120)
        return await build_inventory(self.config, self.mcp, start_mcp=False)

    async def cli_login(self, p: dict) -> dict:
        """Start a plan app's sign-in on this machine (it opens the browser here) and report any code/link it prints."""
        import asyncio
        import re
        import shutil

        local = self.config.providers.get(p["provider"]) or {}
        cfg = {**p["cfg"], **local}
        from .runtime import which

        exe = which(cfg.get("command", p["provider"]))
        if not exe:
            return {"ok": False, "message": f"{cfg.get('command')} is not installed on {self.config.node_name}."}
        proc = await asyncio.create_subprocess_exec(exe, *cfg["login_args"], stdin=asyncio.subprocess.PIPE,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            proc.stdin.write(b"\n")  # some flows wait for Enter before opening the browser
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        lines: list[str] = []

        async def read_some(deadline: float) -> None:
            while asyncio.get_running_loop().time() < deadline:
                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), 1)
                except asyncio.TimeoutError:
                    continue
                if not line:
                    return
                lines.append(re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", line.decode(errors="replace")).rstrip())

        await read_some(asyncio.get_running_loop().time() + 12)

        async def finish() -> None:  # keep the sign-in running in the background for up to 10 minutes
            try:
                await asyncio.wait_for(proc.wait(), 600)
            except asyncio.TimeoutError:
                proc.kill()

        if proc.returncode is None:
            asyncio.get_running_loop().create_task(finish())
        text = "\n".join(ln for ln in lines if ln.strip())[-3000:]
        done = proc.returncode == 0
        return {"ok": True, "finished": done, "output": text,
                "message": ("Signed in." if done else
                            f"Sign-in started on {self.config.node_name}. Finish it in the browser window that opened "
                            f"there (or use the link/code below).")}

    # ---- handing image tasks to ChatGPT / Grok --------------------------------------------------
    async def open_app(self, h: dict) -> dict:
        """Copy the prompt to this computer's clipboard and open the plan's desktop app (or its website)."""
        import os
        import shutil

        from .inventory import run_cmd
        from .paths import os_family

        fam = os_family()
        prompt = h.get("prompt", "")
        env = dict(os.environ, SB_PROMPT=prompt, SB_APP=h.get("windows_app") or h.get("label", ""))
        copied = False
        try:
            if fam == "windows":
                p = await asyncio.create_subprocess_exec("powershell", "-NoProfile", "-Command", "Set-Clipboard -Value $env:SB_PROMPT", env=env)
                copied = (await asyncio.wait_for(p.wait(), 20)) == 0
            elif fam == "macos":
                p = await asyncio.create_subprocess_exec("pbcopy", stdin=asyncio.subprocess.PIPE)
                await asyncio.wait_for(p.communicate(prompt.encode()), 10)
                copied = p.returncode == 0
            elif shutil.which("wl-copy") or shutil.which("xclip"):
                argv = ["wl-copy"] if shutil.which("wl-copy") else ["xclip", "-selection", "clipboard"]
                p = await asyncio.create_subprocess_exec(*argv, stdin=asyncio.subprocess.PIPE)
                await asyncio.wait_for(p.communicate(prompt.encode()), 10)
                copied = p.returncode == 0
        except (OSError, asyncio.TimeoutError):
            copied = False
        opened = None
        if fam == "windows":
            script = ("$a = Get-StartApps | Where-Object { $_.Name -like ($env:SB_APP + '*') } | Select-Object -First 1; "
                      "if ($a) { Start-Process ('shell:AppsFolder\\' + $a.AppID); exit 0 } else { exit 3 }")
            p = await asyncio.create_subprocess_exec("powershell", "-NoProfile", "-Command", script, env=env)
            if (await asyncio.wait_for(p.wait(), 30)) == 0:
                opened = "app"
        elif fam == "macos":
            rc, _, _ = await run_cmd(["open", "-a", h.get("mac_app") or h.get("label", "")], timeout=20)
            if rc == 0:
                opened = "app"
        if not opened and h.get("url"):
            if fam == "windows":
                os.startfile(h["url"])  # type: ignore[attr-defined]
            else:
                await run_cmd(["open" if fam == "macos" else "xdg-open", h["url"]], timeout=20)
            opened = "browser"
        where = f"the {h.get('label')} app" if opened == "app" else f"{h.get('label')} in the browser" if opened else "nothing"
        return {"opened": opened, "copied": copied,
                "message": f"Opened {where} on {self.config.node_name}." + (" The prompt is on the clipboard: paste it." if copied else "")}

    def watch_dir(self):
        from pathlib import Path

        from .paths import expand

        val = self.config.node.get("watch_folder") or "auto"
        return Path.home() / "Downloads" if val == "auto" else expand(str(val))

    async def watch_folder(self, p: dict) -> dict:
        """Wait for a new image to appear in the watch folder (default: Downloads) and return it."""
        folder = self.watch_dir()
        started = time.time() - 5
        deadline = time.time() + float(p.get("timeout", 1200))
        exts = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
        sizes: dict = {}
        while time.time() < deadline:
            if folder.exists():
                for f in folder.iterdir():
                    try:
                        st = f.stat()
                    except OSError:
                        continue
                    if f.suffix.lower() not in exts or st.st_mtime < started or not f.is_file():
                        continue
                    if sizes.get(f) == st.st_size and st.st_size > 0:  # finished writing
                        if st.st_size > 25_000_000:
                            return {"error": f"{f.name} is larger than 25 MB; add it by hand."}
                        return {"name": f.name, "b64": base64.b64encode(f.read_bytes()).decode("ascii"), "folder": folder.name}
                    sizes[f] = st.st_size
            await asyncio.sleep(2)
        return {"timeout": True}
