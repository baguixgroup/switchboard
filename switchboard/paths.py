"""Per-OS locations for config, data and the output folder."""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP = "Switchboard"


def os_family() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def is_raspberry_pi() -> bool:
    try:
        return "raspberry pi" in Path("/proc/device-tree/model").read_text(errors="ignore").lower()
    except OSError:
        return False


def platform_tag() -> str:
    """windows | macos | pi | linux"""
    fam = os_family()
    if fam == "linux" and is_raspberry_pi():
        return "pi"
    return fam


def config_dir() -> Path:
    env = os.environ.get("SWITCHBOARD_HOME")
    if env:
        return Path(env).expanduser()
    fam = os_family()
    if fam == "windows":
        return Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / APP
    if fam == "macos":
        return Path.home() / "Library" / "Application Support" / APP
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "switchboard"


def data_dir() -> Path:
    d = config_dir() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def default_output_dir() -> Path:
    if os_family() in ("windows", "macos"):
        return Path.home() / "Switchboard" / "Output"
    return Path.home() / "switchboard-output"


def expand(p: str) -> Path:
    """Expand ~ and %VARS% / $VARS in a path string."""
    return Path(os.path.expandvars(os.path.expanduser(p)))


def is_frozen() -> bool:
    """True inside the packaged app (PyInstaller), where there is no `python -m switchboard`."""
    return bool(getattr(sys, "frozen", False))


def self_command(*args: str, gui: bool = False) -> list[str]:
    """argv that runs this Tazrivo with `args`, from source or from the packaged app.

    gui=True picks the window-less program on Windows (Tazrivo.exe, no console flash) for
    background services; terminal use and MCP stdio servers need the console one.
    """
    if not is_frozen():
        return [sys.executable, "-m", "switchboard", *args]
    exe = Path(sys.executable)
    if os_family() == 'macos' and exe.name == 'Laith' and len(exe.parents)>4 and exe.parents[3].name=='Helpers':
        for name in ('Tazrivo','Switchboard'):
            main=exe.parents[4]/'MacOS'/name
            if main.exists():exe=main;break
    if os_family() == "windows":
        names = ('Tazrivo.exe','Switchboard.exe') if gui else ('tazrivo-cli.exe','switchboard-cli.exe')
        for name in names:
            want=exe.with_name(name)
            if want.exists():exe=want;break
    return [str(exe), *args]
