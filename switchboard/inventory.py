"""What this machine has: installed tools, connected devices, MCP servers."""
from __future__ import annotations

import asyncio
import glob
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

from . import __version__
from .config import load_default
from .paths import os_family, platform_tag
from .runtime import refresh_path, which


async def run_cmd(argv: list[str], timeout: float = 30, cwd: str | None = None) -> tuple[int, str, str]:
    exe = which(argv[0]) or argv[0]
    try:
        proc = await asyncio.create_subprocess_exec(exe, *argv[1:], cwd=cwd, stdin=asyncio.subprocess.DEVNULL,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except (FileNotFoundError, PermissionError, OSError) as exc:
        return 127, "", f"{argv[0]}: {exc}"
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, "", f"{argv[0]} timed out after {timeout:.0f}s"
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


async def run_shell(command: str, timeout: float = 1800) -> tuple[int, str, str]:
    refresh_path()
    try:
        proc = await asyncio.create_subprocess_shell(command, stdin=asyncio.subprocess.DEVNULL,
                                                     stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except OSError as exc:
        return 127, "", str(exc)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, "", f"timed out after {timeout:.0f}s"
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


def tools_catalog() -> dict:
    return load_default("tools_catalog.yaml")


def mcp_catalog() -> dict:
    return load_default("mcp_catalog.yaml")


def _windows_desktop_apps() -> dict[str, str]:
    """App registration catches Microsoft Store apps even when their executables are protected."""
    script = ("$ErrorActionPreference='SilentlyContinue'; "
              "$start=@(Get-StartApps | Where-Object {$_.Name -match 'Claude|ChatGPT'} | Select-Object Name,AppID); "
              "$packages=@(Get-AppxPackage | Where-Object {$_.Name -match 'Claude|ChatGPT'} | Select-Object Name,InstallLocation); "
              "@{start=$start;packages=$packages}|ConvertTo-Json -Compress -Depth 3")
    try:
        result = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True,
                                text=True, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        data = json.loads(result.stdout)
        out = {r["Name"].casefold(): "shell:AppsFolder\\" + r["AppID"] for r in data.get("start", [])}
        for row in data.get("packages", []):
            out.setdefault(row["Name"].casefold(), row.get("InstallLocation") or row["Name"])
        return out
    except (OSError, ValueError, subprocess.TimeoutExpired, TypeError, KeyError):
        return {}


def detect_tools() -> dict[str, dict]:
    refresh_path()
    plat = platform_tag()
    path_key = "linux" if plat == "pi" else plat
    registered = _windows_desktop_apps() if plat == "windows" else {}
    out: dict[str, dict] = {}
    for tid, t in tools_catalog().items():
        detect = t.get("detect") or {}
        where = None
        for b in detect.get("bin") or []:
            found = which(b)
            if found:
                where = found
                break
        if not where:
            for p in (detect.get("paths") or {}).get(path_key, []) or []:
                hits = glob.glob(os.path.expandvars(os.path.expanduser(p)))
                if hits:
                    where = hits[0]
                    break
        if not where and plat == "windows":
            names = [n.casefold() for n in detect.get("app_names", [])]
            for name, location in registered.items():
                if any(name == wanted or name.endswith("." + wanted) or name.startswith(wanted + " ") for wanted in names):
                    where = location
                    break
        missing = [b for b in detect.get("all_bins", []) if not which(b)]
        if missing:
            where = None
        unsupported = (t.get("unsupported") or {}).get(plat)
        out[tid] = {"name": t.get("name", tid), "installed": bool(where), "where": where,
                    "supported": not unsupported, "desktop": bool(t.get("desktop")),
                    "note": unsupported or ("Missing commands: " + ", ".join(missing) if missing else "")}
    return out


async def _android_devices() -> list[dict]:
    if not shutil.which("adb"):
        return []
    rc, out, _ = await run_cmd(["adb", "devices", "-l"], timeout=10)
    devices = []
    for line in out.splitlines()[1:]:
        if not line.strip() or line.startswith("*"):
            continue
        parts = line.split()
        serial, state = parts[0], parts[1] if len(parts) > 1 else "?"
        props = dict(p.split(":", 1) for p in parts[2:] if ":" in p)
        devices.append({"id": f"android:{serial}", "kind": "android", "name": props.get("model", serial).replace("_", " "),
                        "state": state, "serial": serial})
    return devices


async def _apple_devices() -> list[dict]:
    if os_family() != "macos" or not shutil.which("xcrun"):
        return []
    devices = []
    rc, out, _ = await run_cmd(["xcrun", "simctl", "list", "devices", "booted", "-j"], timeout=15)
    if rc == 0:
        try:
            for runtime, items in json.loads(out).get("devices", {}).items():
                for d in items:
                    devices.append({"id": f"sim:{d['udid']}", "kind": "ios-simulator", "name": d.get("name"),
                                    "state": d.get("state", "Booted"), "runtime": runtime.rsplit(".", 1)[-1]})
        except ValueError:
            pass
    with tempfile.TemporaryDirectory() as tmp:
        jpath = Path(tmp) / "devices.json"
        rc, _, _ = await run_cmd(["xcrun", "devicectl", "list", "devices", "--json-output", str(jpath)], timeout=20)
        if rc == 0 and jpath.exists():
            try:
                for d in json.loads(jpath.read_text()).get("result", {}).get("devices", []):
                    props = d.get("deviceProperties", {})
                    hw = d.get("hardwareProperties", {})
                    conn = d.get("connectionProperties", {})
                    devices.append({"id": f"ios:{hw.get('udid') or d.get('identifier')}", "kind": "ios",
                                    "name": props.get("name") or hw.get("marketingName"),
                                    "state": conn.get("tunnelState") or conn.get("pairingState", "?"),
                                    "identifier": d.get("identifier")})
            except ValueError:
                pass
    return devices


async def tcp_probe(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        fut = asyncio.open_connection(host, port)
        reader, writer = await asyncio.wait_for(fut, timeout)
        writer.close()
        return True
    except (OSError, asyncio.TimeoutError):
        return False


async def ping(host: str) -> bool:
    flag = "-n" if os_family() == "windows" else "-c"
    rc, _, _ = await run_cmd(["ping", flag, "1", host], timeout=5)
    return rc == 0


async def _network_devices(config) -> list[dict]:
    async def one(name: str, d: dict) -> dict:
        host = d.get("host", "")
        port = d.get("probe_port")
        up = await (tcp_probe(host, int(port)) if port else ping(host))
        return {"id": f"net:{name}", "kind": d.get("kind", "network"), "name": d.get("label", name), "host": host,
                "state": "reachable" if up else "unreachable"}

    return list(await asyncio.gather(*(one(n, d or {}) for n, d in (config.devices or {}).items())))


async def _servers(config) -> list[dict]:
    async def one(name: str, s: dict) -> dict:
        up = await tcp_probe(s.get("host", ""), int(s.get("port", 22)), timeout=3)
        return {"id": f"server:{name}", "kind": "server", "name": s.get("label", name), "host": s.get("host"),
                "state": "ssh port open" if up else "unreachable", "deploy_path": s.get("deploy_path")}

    return list(await asyncio.gather(*(one(n, s or {}) for n, s in (config.servers or {}).items())))


async def detect_devices(config) -> list[dict]:
    groups = await asyncio.gather(_android_devices(), _apple_devices(), _network_devices(config), _servers(config),
                                  return_exceptions=True)
    out: list[dict] = []
    for g in groups:
        if isinstance(g, list):
            out += g
    return out


async def build_inventory(config, mcp_hub=None, start_mcp: bool = True) -> dict:
    tools = await asyncio.to_thread(detect_tools)
    devices = await detect_devices(config)
    mcp: dict[str, dict] = {}
    if mcp_hub is not None:
        all_tools = await mcp_hub.all_tools(start=start_mcp)
        status = mcp_hub.status()
        for name, st in status.items():
            mcp[name] = dict(st, tool_list=all_tools.get(name, []))
    return {
        "node": config.node_name, "role": config.role, "platform": platform_tag(), "os": platform.platform(),
        "arch": platform.machine(), "python": sys.version.split()[0], "version": __version__,
        "hostname": socket.gethostname(), "output_dir": str(config.output_dir),
        "mirror_outputs": bool(config.node.get("mirror_outputs", True)),
        "tools": tools, "devices": devices, "mcp": mcp,
        "cli_providers": sorted(n for n, c in (config.providers or {}).items()
                                if c and c.get("kind") == "cli" and not c.get("disabled")
                                and which(c.get("command", n))),
    }
