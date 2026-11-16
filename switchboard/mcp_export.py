"""Register Switchboard (and optionally this machine's MCP servers) with other AI apps."""
from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
from pathlib import Path

from .paths import os_family

TARGETS = ["claude-desktop", "claude-code", "vscode", "cursor", "codex", "gemini-cli"]


def switchboard_entry(config) -> dict:
    entry = {"command": sys.executable, "args": ["-m", "switchboard", "mcp", "serve"]}
    if os.environ.get("SWITCHBOARD_HOME"):
        entry["env"] = {"SWITCHBOARD_HOME": str(config.home)}
    return entry


def _servers(config, include_local: bool) -> dict[str, dict]:
    out = {"switchboard": switchboard_entry(config)}
    if include_local:
        for name, cfg in (config.mcp_servers or {}).items():
            if cfg and not cfg.get("disabled"):
                out[name] = {k: v for k, v in cfg.items() if k in ("command", "args", "env", "url", "headers")}
    return out


def _json_path(target: str) -> Path | None:
    fam = os_family()
    home = Path.home()
    if target == "claude-desktop":
        if fam == "windows":
            return Path(os.environ.get("APPDATA", home / "AppData/Roaming")) / "Claude" / "claude_desktop_config.json"
        if fam == "macos":
            return home / "Library/Application Support/Claude/claude_desktop_config.json"
        return home / ".config/Claude/claude_desktop_config.json"
    if target == "cursor":
        return home / ".cursor" / "mcp.json"
    if target == "gemini-cli":
        return home / ".gemini" / "settings.json"
    if target == "vscode":
        return Path.cwd() / ".vscode" / "mcp.json"
    return None


def export(config, target: str, include_local: bool = False, write: bool = False) -> str:
    servers = _servers(config, include_local)
    if target == "claude-code":
        lines = []
        for name, s in servers.items():
            if s.get("url"):
                lines.append(f"claude mcp add --scope user --transport http {name} {s['url']}")
            else:
                env = " ".join(f"-e {k}={shlex.quote(str(v))}" for k, v in (s.get("env") or {}).items())
                cmd = " ".join(shlex.quote(str(x)) for x in [s["command"], *s.get("args", [])])
                lines.append(f"claude mcp add --scope user {env + ' ' if env else ''}{name} -- {cmd}")
        return "Run:\n" + "\n".join(lines)
    if target == "codex":
        blocks = []
        for name, s in servers.items():
            if s.get("url"):
                blocks.append(f'[mcp_servers.{name}]\nurl = "{s["url"]}"')
                continue
            args = ", ".join(json.dumps(str(a)) for a in s.get("args", []))
            b = f'[mcp_servers.{name}]\ncommand = {json.dumps(s["command"])}\nargs = [{args}]'
            if s.get("env"):
                b += "\nenv = { " + ", ".join(f'{k} = {json.dumps(str(v))}' for k, v in s["env"].items()) + " }"
            blocks.append(b)
        return "Add to ~/.codex/config.toml:\n\n" + "\n\n".join(blocks)

    path = _json_path(target)
    if path is None:
        raise ValueError(f"unknown target {target}; choose from {TARGETS}")
    key = "servers" if target == "vscode" else "mcpServers"
    if target == "vscode":
        servers = {n: ({"type": "http", **s} if s.get("url") else {"type": "stdio", **s}) for n, s in servers.items()}
    if not write:
        return f"Merge into {path}:\n\n" + json.dumps({key: servers}, indent=2)
    data = {}
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
        shutil.copyfile(path, path.with_suffix(path.suffix + ".bak"))
    data.setdefault(key, {}).update(servers)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return f"Wrote {len(servers)} server(s) to {path} (backup: {path.name}.bak). Restart the app to load them."
