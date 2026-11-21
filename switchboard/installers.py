"""Native desktop installers: official downloads, normal user installation, no security bypasses."""
from __future__ import annotations

import platform
import plistlib
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from . import __version__


def official_download(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": f"Tazrivo/{__version__}"})
    return urllib.request.urlopen(request, timeout=120)


def install_macos_app(entry: dict) -> str:
    with tempfile.TemporaryDirectory(prefix="switchboard-app-") as temporary:
        image = Path(temporary) / "installer.dmg"
        with official_download(entry["download_macos"]) as response, image.open("wb") as output:
            shutil.copyfileobj(response, output)
        attached = subprocess.run(["hdiutil", "attach", "-nobrowse", "-readonly", "-plist", str(image)],
                                  capture_output=True, check=True)
        mounts = [Path(e["mount-point"]) for e in plistlib.loads(attached.stdout).get("system-entities", []) if e.get("mount-point")]
        try:
            for mount in mounts:
                for candidate in mount.glob("*.app"):
                    with (candidate / "Contents/Info.plist").open("rb") as source:
                        info = plistlib.load(source)
                    if info.get("CFBundleIdentifier") not in entry["bundle_ids"]:
                        continue
                    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(candidate)], capture_output=True, check=True)
                    subprocess.run(["spctl", "--assess", "--type", "execute", str(candidate)], capture_output=True, check=True)
                    binary = candidate / "Contents/MacOS" / info["CFBundleExecutable"]
                    archs = subprocess.run(["lipo", "-archs", str(binary)], capture_output=True, text=True, check=True).stdout.split()
                    if platform.machine() not in archs:
                        raise RuntimeError("The official download does not support this Mac's CPU architecture.")
                    destination = Path.home() / "Applications" / entry["bundle_name"]
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if destination.exists():
                        raise RuntimeError(f"An app already exists at {destination}; refusing to overwrite it.")
                    shutil.copytree(candidate, destination, symlinks=True)
                    return str(destination)
            raise RuntimeError("The disk image did not contain the expected official application.")
        finally:
            for mount in mounts:
                subprocess.run(["hdiutil", "detach", str(mount)], capture_output=True, check=False)


def install_windows_msix(entry: dict) -> str:
    """Install the vendor's Store-signed package for the current user; Windows validates it."""
    import base64
    import zipfile

    arch = 'arm64' if platform.machine().casefold() in ('arm64', 'aarch64') else 'x64'
    url = entry['download_windows'][arch]
    temporary = tempfile.mkdtemp(prefix='switchboard-app-')
    package = Path(temporary) / 'ChatGPT.msix'
    with official_download(url) as response, package.open('wb') as output:
        shutil.copyfileobj(response, output)
    from xml.etree import ElementTree
    with zipfile.ZipFile(package) as archive:
        manifest = ElementTree.fromstring(archive.read('AppxManifest.xml'))
    identity = next((e for e in manifest if e.tag.endswith('}Identity') or e.tag == 'Identity'), None)
    if identity is None or identity.get('Name') != entry['windows_package']:
        raise RuntimeError('Official package download has an unexpected application identity.')
    quoted = str(package).replace("'", "''")
    script = "$ErrorActionPreference='Stop'; Add-AppxPackage -Path '" + quoted + "' -ErrorAction Stop; Get-AppxPackage -Name '" + entry['windows_package'] + "' | Select-Object Name,Version | Format-List"
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    result = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                            capture_output=True, text=True, timeout=1200,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        error = result.stderr
        if '#< CLIXML' in error:
            try:
                root = ElementTree.fromstring(error[error.index('<Objs'):])
                error = '\n'.join(e.text or '' for e in root.iter() if e.get('S') == 'Error')
                error = error.replace('_x000D__x000A_', '\n').replace('_x005F_', '_')
            except (ValueError, ElementTree.ParseError):
                pass
        if '0x80073D28' in error or 'administrator privileges' in error.casefold():
            error = ("Windows requires administrator approval to install ChatGPT's packaged service. "
                     "The agent runs as your normal user. Open Windows PowerShell as administrator on this PC and run:\n"
                     + "Add-AppxPackage -Path '" + quoted + "'\nThen rescan this machine in Tazrivo.")
        # Windows may hold a package handle after deployment fails. Keep the signed package for
        # the administrator instead of blocking error reporting on synchronous temporary cleanup.
        raise RuntimeError(error.strip()[-8000:] + f"\nOfficial package retained at {package}")
    import threading
    threading.Thread(target=shutil.rmtree, args=(temporary,), kwargs={'ignore_errors': True}, daemon=True).start()
    return result.stdout.strip() or 'Official package installed for the current Windows user.'
