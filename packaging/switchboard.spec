# PyInstaller build for the Switchboard app (one folder, Python included).
#   pyinstaller --noconfirm --clean packaging/switchboard.spec
# Output: dist/Switchboard.app (macOS), dist/Switchboard/ (Windows), dist/switchboard/ (Linux/Pi).
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent
VERSION = re.search(r'__version__ = "([^"]+)"', (ROOT / "switchboard" / "__init__.py").read_text()).group(1)
ICONS = ROOT / "build" / "icons"
WIN, MAC = sys.platform.startswith("win"), sys.platform == "darwin"

hidden = (collect_submodules("switchboard") + collect_submodules("uvicorn") + collect_submodules("mcp", filter=lambda n: not n.startswith("mcp.cli"))
          + collect_submodules("pystray") + (collect_submodules("webview") if (WIN or MAC) else []))
datas = collect_data_files("switchboard")
for dist in ("switchboard-ai", "mcp", "fastapi", "starlette", "pydantic", "anthropic", "openai", "httpx", "uvicorn"):
    try:
        datas += copy_metadata(dist)  # packages that read their own version via importlib.metadata
    except Exception:
        pass

a = Analysis([str(ROOT / "packaging" / "entry.py")], pathex=[str(ROOT)], hiddenimports=hidden, datas=datas,
             excludes=["tkinter", "pytest", "IPython", "matplotlib", "numpy"], noarchive=False)
pyz = PYZ(a.pure)


def exe(name, console):
    return EXE(pyz, a.scripts, [], exclude_binaries=True, name=name, console=console, strip=False, upx=False,
               icon=str(ICONS / ("switchboard.ico" if WIN else "switchboard.icns")) if (WIN or MAC) else None,
               argv_emulation=False, codesign_identity=None, entitlements_file=None)


if WIN:
    # Switchboard.exe: no console window (Start menu, tray, start-at-login). switchboard-cli.exe: for terminals.
    coll = COLLECT(exe("Switchboard", False), exe("switchboard-cli", True), a.binaries, a.datas,
                   strip=False, upx=False, name="Switchboard")
elif MAC:
    coll = COLLECT(exe("Switchboard", False), a.binaries, a.datas, strip=False, upx=False, name="Switchboard")
    app = BUNDLE(coll, name="Switchboard.app", icon=str(ICONS / "switchboard.icns"),
                 bundle_identifier="com.baguixgroup.switchboard", version=VERSION,
                 info_plist={
                     "CFBundleName": "Switchboard", "CFBundleDisplayName": "Switchboard",
                     "CFBundleShortVersionString": VERSION, "CFBundleVersion": VERSION,
                     # Dock icon while the window is open; the background menu-bar process hides itself.
                     "LSMinimumSystemVersion": "11.0",
                     "NSHumanReadableCopyright": "Switchboard",
                     # macOS asks once before the app may talk to the hub on your network.
                     "NSLocalNetworkUsageDescription": "Switchboard connects to your Switchboard hub and other computers on your network.",
                     "NSAppleEventsUsageDescription": "Switchboard opens apps such as ChatGPT when a task needs them.",
                 })
else:
    coll = COLLECT(exe("switchboard", True), a.binaries, a.datas, strip=False, upx=False, name="switchboard")
