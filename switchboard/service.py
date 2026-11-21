"""Start Tazrivo automatically at login.

Windows: Task Scheduler.  macOS: launchd.  Linux/Pi: systemd --user.
On Windows and macOS (when the tray extras are installed) an agent runs inside the tray icon app,
and a hub gets a separate tray icon next to its background service.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .paths import is_frozen, os_family, self_command
from .tray import tray_available

COMMAND = {"hub": "serve", "agent": "agent", "tray": "tray"}


def _argv(kind: str) -> list[str]:
    """Full command line for a background program (works from source and from the packaged app)."""
    home = ["--home", os.environ["SWITCHBOARD_HOME"]] if os.environ.get("SWITCHBOARD_HOME") else []
    argv = self_command(*home, COMMAND[kind], "--service", gui=True)
    if not is_frozen() and os_family() == "windows":
        pyw = Path(argv[0]).with_name("pythonw.exe")  # no console window
        if pyw.exists():
            argv[0] = str(pyw)
    return argv


def _run(argv: list[str], check: bool = True) -> str:
    r = subprocess.run(argv, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(argv)} failed: {r.stderr or r.stdout}")
    return (r.stdout or "") + (r.stderr or "")


def _plan(role: str) -> list[str]:
    """Which background programs to register for this role on this OS."""
    desktop = os_family() in ("windows", "macos") and tray_available()
    if role == "agent":
        return ["tray"] if desktop else ["agent"]
    return ["hub", "tray"] if desktop else ["hub"]


# ---- Windows -----------------------------------------------------------------------------------
def _win_install(kind: str) -> str:
    tr = " ".join(f'"{a}"' if (" " in a or i == 0) else a for i, a in enumerate(_argv(kind)))
    name = f"Switchboard {kind}"
    _run(["schtasks", "/Create", "/TN", name, "/TR", tr, "/SC", "ONLOGON", "/RL", "LIMITED", "/F"])
    _run(["schtasks", "/Run", "/TN", name], check=False)
    return f"Windows: '{name}' starts at logon (started now)."


def _win_remove(kind: str) -> None:
    _run(["schtasks", "/End", "/TN", f"Switchboard {kind}"], check=False)
    _run(["schtasks", "/Delete", "/TN", f"Switchboard {kind}", "/F"], check=False)


# ---- macOS -------------------------------------------------------------------------------------------
def _plist_path(kind: str) -> Path:
    return Path.home() / "Library/LaunchAgents" / f"com.switchboard.{kind}.plist"


def _mac_install(kind: str) -> str:
    label = f"com.switchboard.{kind}"
    plist = _plist_path(kind)
    plist.parent.mkdir(parents=True, exist_ok=True)
    args = "\n".join(f"    <string>{a}</string>" for a in _argv(kind))
    env = ""
    if os.environ.get("SWITCHBOARD_HOME"):
        env = (f"  <key>EnvironmentVariables</key><dict><key>SWITCHBOARD_HOME</key>"
               f"<string>{os.environ['SWITCHBOARD_HOME']}</string></dict>\n")
    gui = kind == "tray"
    plist.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key><array>
{args}
  </array>
{env}  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>{'Interactive' if gui else 'Background'}</string>
{'  <key>LimitLoadToSessionType</key><string>Aqua</string>' + chr(10) if gui else ''}</dict></plist>
""", encoding="utf-8")
    uid = os.getuid()
    _run(["launchctl", "bootout", f"gui/{uid}", str(plist)], check=False)
    _run(["launchctl", "bootstrap", f"gui/{uid}", str(plist)])
    return f"macOS: {label} starts at login (started now)."


def _mac_remove(kind: str) -> None:
    plist = _plist_path(kind)
    _run(["launchctl", "bootout", f"gui/{os.getuid()}", str(plist)], check=False)
    plist.unlink(missing_ok=True)


# ---- Linux / Raspberry Pi ---------------------------------------------------------------------------
def _linux_install(kind: str) -> str:
    unit_dir = Path.home() / ".config/systemd/user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit = unit_dir / f"switchboard-{kind}.service"
    env = f"Environment=SWITCHBOARD_HOME={os.environ['SWITCHBOARD_HOME']}\n" if os.environ.get("SWITCHBOARD_HOME") else ""
    unit.write_text(f"""[Unit]
Description=Tazrivo {kind}
After=network-online.target
Wants=network-online.target

[Service]
ExecStart={' '.join(_argv(kind))}
{env}Restart=always
RestartSec=5

[Install]
WantedBy=default.target
""", encoding="utf-8")
    _run(["systemctl", "--user", "daemon-reload"])
    _run(["systemctl", "--user", "enable", "--now", unit.name])
    linger = _run(["loginctl", "enable-linger", os.environ.get("USER", "")], check=False)
    note = "" if "denied" not in linger.lower() else " Run `sudo loginctl enable-linger $USER` so it runs without you logged in."
    return f"Linux: {unit.name} enabled and started.{note}"


def _linux_remove(kind: str) -> None:
    name = f"switchboard-{kind}.service"
    _run(["systemctl", "--user", "disable", "--now", name], check=False)
    (Path.home() / ".config/systemd/user" / name).unlink(missing_ok=True)
    _run(["systemctl", "--user", "daemon-reload"], check=False)


def install(role: str) -> str:
    fam = os_family()
    do = {"windows": _win_install, "macos": _mac_install}.get(fam, _linux_install)
    msgs = [do(kind) for kind in _plan(role)]
    if role == "agent" and "tray" in _plan(role):
        msgs.append("The agent runs inside the tray icon: look for the Tazrivo icon in your "
                    + ("system tray." if fam == "windows" else "menu bar."))
    return "\n".join(msgs)


def uninstall(role: str) -> str:
    fam = os_family()
    undo = {"windows": _win_remove, "macos": _mac_remove}.get(fam, _linux_remove)
    for kind in {"hub", "agent", "tray"} if role == "hub" else {"agent", "tray"}:
        undo(kind)
    return "Removed Tazrivo's start-at-login entries."
