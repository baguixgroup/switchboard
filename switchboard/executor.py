"""Executes jobs on THIS machine. Used in-process by the hub and by every agent."""
from __future__ import annotations

import base64
import logging

from . import devices
from .inventory import build_inventory, run_shell, tools_catalog
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
        if kind == "install_tool":
            return await self.install_tool(payload["tool"], bool(payload.get("approved")))
        if kind == "write_output":
            target = safe_join(self.config.output_dir, payload["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(base64.b64decode(payload["content_b64"]))
            return {"written": str(target)}
        raise ValueError(f"unknown job kind: {kind}")

    async def install_tool(self, tool_id: str, approved: bool) -> dict:
        t = tools_catalog().get(tool_id)
        if not t:
            return {"ok": False, "output": f"unknown tool '{tool_id}'"}
        plat = platform_tag()
        command = (t.get("install") or {}).get(plat) or ((t.get("install") or {}).get("linux") if plat == "pi" else None)
        if not command:
            return {"ok": False, "output": (t.get("unsupported") or {}).get(plat) or f"no installer for {plat}"}
        if t.get("manual") or command.lower().startswith("install "):
            return {"ok": False, "manual": True, "output": command}
        if not approved:
            return {"ok": False, "needs_approval": True, "command": command}
        if command.startswith("sudo "):
            command = "sudo -n " + command[5:]  # never hang waiting for a password
        log.info("installing %s: %s", tool_id, command)
        rc, out, err = await run_shell(command, timeout=1800)
        text = (out + "\n" + err).strip()[-8000:]
        if rc != 0 and "a password is required" in err:
            text += f"\n\nThis needs your password. Run it yourself on {self.config.node_name}:\n  {command[8:]}"
        return {"ok": rc == 0, "command": command, "output": text}
