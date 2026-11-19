"""Find user-installed commands in background apps, without executing shell startup scripts."""
from __future__ import annotations

import glob
import hashlib
import json
import os
import platform
import re
import shutil
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from .paths import os_family


def node_root() -> Path:
    if os_family() == "windows":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "Switchboard/node"
    return Path.home() / ".local/share/switchboard/node"


def _versions(pattern: str) -> list[str]:
    return sorted(glob.glob(os.path.expanduser(pattern)),
                  key=lambda p: tuple(int(n) for n in re.findall(r"\d+", p)), reverse=True)


def _windows_registry_paths() -> list[str]:
    try:
        import winreg
    except ImportError:
        return []
    paths = []
    for hive, key in ((winreg.HKEY_CURRENT_USER, "Environment"),
                      (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
        try:
            with winreg.OpenKey(hive, key) as handle:
                paths.extend(winreg.QueryValueEx(handle, "Path")[0].split(os.pathsep))
        except OSError:
            pass
    return paths


def mac_app_dirs() -> list[Path]:
    return [Path("/Applications"), Path.home() / "Applications"]


def refresh_path() -> str:
    """Refresh at startup AND before detection/launch, including installs made after this app started."""
    fam = os_family()
    home = Path.home()
    extra = [str(home / ".local/bin"), str(home / ".volta/bin"), str(home / ".npm-global/bin")]
    if fam == "windows":
        extra += _windows_registry_paths()
        extra += [os.path.join(os.environ.get("APPDATA", str(home / "AppData/Roaming")), "npm"),
                  os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"), "nodejs"),
                  os.path.join(os.environ.get("LOCALAPPDATA", str(home / "AppData/Local")), "Programs", "nodejs"),
                  str(node_root())]
        extra += [os.environ.get(k, "") for k in ("NVM_SYMLINK", "NVM_HOME", "VOLTA_HOME")]
    else:
        extra += ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", str(node_root() / "bin")]
        extra += _versions(str(home / ".nvm/versions/node/*/bin"))
        extra += _versions(str(home / ".local/share/fnm/node-versions/*/installation/bin"))
        extra += _versions(str(home / "Library/Application Support/fnm/node-versions/*/installation/bin"))
        if fam == "macos":
            # ChatGPT includes a Codex CLI in current Mac builds; do not confuse the app with the CLI.
            for apps in mac_app_dirs():
                extra += [str(apps / "ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS")]
    paths, seen = [], set()
    for entry in os.environ.get("PATH", "").split(os.pathsep) + extra:
        path = os.path.expandvars(os.path.expanduser(entry.strip().strip('"')))
        key = path.casefold() if fam == "windows" else path
        if path and key not in seen and Path(path).is_dir():
            paths.append(path)
            seen.add(key)
    os.environ["PATH"] = os.pathsep.join(paths)
    return os.environ["PATH"]


def which(command: str) -> str | None:
    refresh_path()
    return shutil.which(command)


def missing_node_commands() -> list[str]:
    refresh_path()
    return [name for name in ("node", "npm", "npx") if not shutil.which(name)]


def install_node() -> str:
    """Approved per-user install of official Node LTS binaries, checked against official SHA256 sums."""
    if not missing_node_commands():
        return str(which("node"))
    fam = {"macos": "darwin", "windows": "win", "linux": "linux"}[os_family()]
    arch = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine().lower())
    if not arch:
        raise RuntimeError("No official Node binary installer is configured for this CPU.")
    base = "https://nodejs.org/dist/"
    with urllib.request.urlopen(base + "index.json", timeout=30) as response:
        releases = json.load(response)
    version = next(r["version"] for r in releases if r.get("lts") and re.fullmatch(r"v\d+\.\d+\.\d+", r["version"]))
    folder = f"node-{version}-{fam}-{arch}"
    archive = folder + (".zip" if fam == "win" else ".tar.gz")
    with urllib.request.urlopen(base + version + "/SHASUMS256.txt", timeout=30) as response:
        sums = response.read().decode()
    expected = next((line.split()[0] for line in sums.splitlines() if len(line.split()) == 2 and line.split()[1] == archive), None)
    if not expected or not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
        raise RuntimeError("The official Node release has no checksum for this platform.")
    target = node_root()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise RuntimeError(f"A partial Node installation exists at {target}. Repair it before trying again.")
    with tempfile.TemporaryDirectory(prefix="node-download-", dir=target.parent) as temp:
        downloaded = Path(temp) / archive
        digest = hashlib.sha256()
        with urllib.request.urlopen(base + version + "/" + archive, timeout=120) as response, downloaded.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
                digest.update(chunk)
        if digest.hexdigest().lower() != expected.lower():
            raise RuntimeError("Node download checksum did not match. Nothing was installed.")
        extracted = Path(temp) / "extracted"
        if fam == "win":
            with zipfile.ZipFile(downloaded) as bundle:
                for member in bundle.namelist():
                    if not (extracted / member).resolve().is_relative_to(extracted.resolve()):
                        raise RuntimeError("Unsafe path in Node archive.")
                bundle.extractall(extracted)
        else:
            with tarfile.open(downloaded) as bundle:
                bundle.extractall(extracted, filter="data")
        os.replace(extracted / folder, target)
    refresh_path()
    if missing_node_commands():
        raise RuntimeError("Node unpacked, but node/npm/npx are not executable.")
    return str(target)
