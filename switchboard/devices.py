"""Actions on connected devices and servers: Android (adb), iOS devices and simulators, SSH servers.

Device ids: android:<serial>  ios:<udid>  sim:<udid>  net:<name>  server:<name>
"""
from __future__ import annotations

import asyncio
import shlex
import shutil
import time
from pathlib import Path

from .inventory import ping, run_cmd, tcp_probe

READ_ONLY_ACTIONS = {"info", "screenshot", "logs", "check", "ping", "list"}
ACTIONS = {
    "android": ["info", "screenshot", "logs", "install", "launch", "open_url", "shell"],
    "ios": ["install", "launch"],
    "sim": ["screenshot", "install", "launch", "open_url"],
    "net": ["ping"],
    "server": ["check", "deploy", "run"],
}


def _ok(output: str = "", files: list[str] | None = None) -> dict:
    return {"ok": True, "output": output.strip()[-20000:], "files": files or []}


def _fail(output: str) -> dict:
    return {"ok": False, "output": output.strip()[-20000:], "files": []}


async def _run(argv: list[str], timeout: float = 120) -> dict:
    rc, out, err = await run_cmd(argv, timeout=timeout)
    return _ok(out + err) if rc == 0 else _fail(f"exit {rc}: {err or out}")


async def device_action(config, device_id: str, action: str, args: dict | None = None,
                        save_dir: Path | None = None) -> dict:
    args = args or {}
    kind, _, ident = device_id.partition(":")
    if action not in ACTIONS.get(kind, []):
        return _fail(f"'{action}' is not supported for {kind} devices. Supported: {ACTIONS.get(kind, [])}")
    needs = {"android": ("adb", "adb"), "ios": ("xcrun", "xcode"), "sim": ("xcrun", "xcode"), "server": ("ssh", "git")}
    if kind in needs and not shutil.which(needs[kind][0]):
        binary, tool = needs[kind]
        return _fail(f"'{binary}' is not installed on {config.node_name}. "
                     f"Install it with: switchboard tools install {tool} --node {config.node_name}")
    save_dir = save_dir or (config.output_dir / "device-captures")
    save_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")

    if kind == "android":
        adb = ["adb", "-s", ident]
        if action == "info":
            return await _run(adb + ["shell", "getprop", "ro.product.model"])
        if action == "screenshot":
            target = save_dir / f"android-{ident}-{stamp}.png"
            exe = shutil.which("adb") or "adb"
            proc = await asyncio.create_subprocess_exec(exe, "-s", ident, "exec-out", "screencap", "-p",
                                                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, err = await asyncio.wait_for(proc.communicate(), 60)
            if proc.returncode != 0 or not out:
                return _fail(err.decode(errors="replace"))
            target.write_bytes(out)
            return _ok(f"Saved {target}", [str(target)])
        if action == "logs":
            return await _run(adb + ["logcat", "-d", "-t", str(int(args.get("lines", 300)))])
        if action == "install":
            return await _run(adb + ["install", "-r", str(args["path"])], timeout=600)
        if action == "launch":
            return await _run(adb + ["shell", "monkey", "-p", str(args["package"]), "-c",
                                     "android.intent.category.LAUNCHER", "1"])
        if action == "open_url":
            return await _run(adb + ["shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", str(args["url"])])
        if action == "shell":
            return await _run(adb + ["shell", str(args["command"])])

    if kind == "ios":
        if action == "install":
            return await _run(["xcrun", "devicectl", "device", "install", "app", "--device", ident, str(args["path"])], 900)
        if action == "launch":
            return await _run(["xcrun", "devicectl", "device", "process", "launch", "--device", ident, str(args["bundle_id"])])

    if kind == "sim":
        if action == "screenshot":
            target = save_dir / f"sim-{stamp}.png"
            res = await _run(["xcrun", "simctl", "io", ident, "screenshot", str(target)])
            if res["ok"]:
                res["files"] = [str(target)]
            return res
        if action == "install":
            return await _run(["xcrun", "simctl", "install", ident, str(args["path"])], 600)
        if action == "launch":
            return await _run(["xcrun", "simctl", "launch", ident, str(args["bundle_id"])])
        if action == "open_url":
            return await _run(["xcrun", "simctl", "openurl", ident, str(args["url"])])

    if kind == "net":
        d = (config.devices or {}).get(ident) or {}
        port = d.get("probe_port")
        up = await (tcp_probe(d.get("host", ""), int(port)) if port else ping(d.get("host", "")))
        return _ok(f"{ident} is {'reachable' if up else 'unreachable'}")

    if kind == "server":
        s = (config.servers or {}).get(ident)
        if not s:
            return _fail(f"server '{ident}' is not configured")
        port = str(s.get("port", 22))
        target = f"{s['user']}@{s['host']}" if s.get("user") else s["host"]
        ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-p", port]
        if s.get("key"):
            ssh += ["-i", str(Path(s["key"]).expanduser())]
        if action == "check":
            return await _run(ssh + [target, "echo connected: $(uname -a)"], 30)
        if action == "run":
            return await _run(ssh + [target, str(args["command"])], float(args.get("timeout", 600)))
        if action == "deploy":
            local = Path(str(args["path"])).expanduser()
            remote = str(args.get("remote_path") or s.get("deploy_path") or "")
            if not local.exists():
                return _fail(f"{local} does not exist")
            if not remote:
                return _fail("no remote_path given and no deploy_path configured for this server")
            if shutil.which("rsync"):
                src = str(local) + ("/" if local.is_dir() else "")
                ssh_cmd = " ".join(shlex.quote(x) for x in ssh)
                return await _run(["rsync", "-az", "--human-readable", "--stats", "-e", ssh_cmd, src,
                                   f"{target}:{remote}"], 1800)
            scp = ["scp", "-o", "BatchMode=yes", "-P", port] + (["-r"] if local.is_dir() else [])
            if s.get("key"):
                scp += ["-i", str(Path(s["key"]).expanduser())]
            src = [str(p) for p in local.iterdir()] if local.is_dir() else [str(local)]
            return await _run(scp + src + [f"{target}:{remote}"], 1800)
    return _fail("unsupported")
