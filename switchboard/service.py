"""Start Switchboard automatically: Task Scheduler (Windows), launchd (macOS), systemd --user (Linux/Pi)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .paths import os_family

LABEL = "com.switchboard.{role}"


def _cmd(role: str) -> list[str]:
    return ["-m", "switchboard"] + (["--home", os.environ["SWITCHBOARD_HOME"]] if os.environ.get("SWITCHBOARD_HOME") else []) + \
        [("serve" if role == "hub" else "agent"), "--service"]


def _run(argv: list[str], check: bool = True) -> str:
    r = subprocess.run(argv, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(argv)} failed: {r.stderr or r.stdout}")
    return (r.stdout or "") + (r.stderr or "")


def install(role: str) -> str:
    fam = os_family()
    if fam == "windows":
        exe = Path(sys.executable)
        pyw = exe.with_name("pythonw.exe")
        runner = pyw if pyw.exists() else exe
        tr = f'"{runner}" ' + " ".join(f'"{a}"' if " " in a else a for a in _cmd(role))
        name = f"Switchboard {role}"
        _run(["schtasks", "/Create", "/TN", name, "/TR", tr, "/SC", "ONLOGON", "/RL", "LIMITED", "/F"])
        _run(["schtasks", "/Run", "/TN", name], check=False)
        return f"Scheduled task '{name}' created (starts at logon) and started."
    if fam == "macos":
        label = LABEL.format(role=role)
        plist = Path.home() / "Library/LaunchAgents" / f"{label}.plist"
        plist.parent.mkdir(parents=True, exist_ok=True)
        args = "\n".join(f"    <string>{a}</string>" for a in [sys.executable, *_cmd(role)])
        env = ""
        if os.environ.get("SWITCHBOARD_HOME"):
            env = f"  <key>EnvironmentVariables</key><dict><key>SWITCHBOARD_HOME</key><string>{os.environ['SWITCHBOARD_HOME']}</string></dict>\n"
        plist.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key><array>
{args}
  </array>
{env}  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Background</string>
</dict></plist>
""", encoding="utf-8")
        uid = os.getuid()
        _run(["launchctl", "bootout", f"gui/{uid}", str(plist)], check=False)
        _run(["launchctl", "bootstrap", f"gui/{uid}", str(plist)])
        return f"launchd agent {label} installed and started ({plist})."
    unit_dir = Path.home() / ".config/systemd/user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit = unit_dir / f"switchboard-{role}.service"
    env = f"Environment=SWITCHBOARD_HOME={os.environ['SWITCHBOARD_HOME']}\n" if os.environ.get("SWITCHBOARD_HOME") else ""
    unit.write_text(f"""[Unit]
Description=Switchboard {role}
After=network-online.target
Wants=network-online.target

[Service]
ExecStart={sys.executable} {' '.join(_cmd(role))}
{env}Restart=always
RestartSec=5

[Install]
WantedBy=default.target
""", encoding="utf-8")
    _run(["systemctl", "--user", "daemon-reload"])
    _run(["systemctl", "--user", "enable", "--now", unit.name])
    linger = _run(["loginctl", "enable-linger", os.environ.get("USER", "")], check=False)
    note = "" if "denied" not in linger.lower() else "\nRun `sudo loginctl enable-linger $USER` so it runs without you logged in."
    return f"systemd user service {unit.name} enabled and started.{note}"


def uninstall(role: str) -> str:
    fam = os_family()
    if fam == "windows":
        _run(["schtasks", "/End", "/TN", f"Switchboard {role}"], check=False)
        _run(["schtasks", "/Delete", "/TN", f"Switchboard {role}", "/F"], check=False)
        return "Scheduled task removed."
    if fam == "macos":
        plist = Path.home() / "Library/LaunchAgents" / f"{LABEL.format(role=role)}.plist"
        _run(["launchctl", "bootout", f"gui/{os.getuid()}", str(plist)], check=False)
        plist.unlink(missing_ok=True)
        return "launchd agent removed."
    name = f"switchboard-{role}.service"
    _run(["systemctl", "--user", "disable", "--now", name], check=False)
    (Path.home() / ".config/systemd/user" / name).unlink(missing_ok=True)
    _run(["systemctl", "--user", "daemon-reload"], check=False)
    return "systemd user service removed."
