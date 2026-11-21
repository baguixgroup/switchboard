# PyInstaller build for the Tazrivo app (one folder, Python included).
#   pyinstaller --noconfirm --clean packaging/switchboard.spec
# Output: dist/Tazrivo.app (macOS), dist/Tazrivo/ (Windows), dist/switchboard/ (Linux/Pi).
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent
VERSION = re.search(r'__version__ = "([^"]+)"', (ROOT / "switchboard" / "__init__.py").read_text()).group(1)
ICONS = ROOT / "build" / "icons"
WIN, MAC = sys.platform.startswith("win"), sys.platform == "darwin"


def package_data():
    # Build from this checkout, not an older installed switchboard wheel in the
    # build environment. Otherwise new manifests/UI assets can silently vanish.
    return [(str(p), str(p.parent.relative_to(ROOT)))
            for pattern in ('defaults/*.yaml', 'web/static/*')
            for p in (ROOT/'switchboard').glob(pattern) if p.is_file()]

hidden = (collect_submodules("switchboard") + collect_submodules("pypdf") + collect_submodules("uvicorn") + collect_submodules("mcp", filter=lambda n: not n.startswith("mcp.cli"))
          + collect_submodules("pystray") + (collect_submodules("webview") if (WIN or MAC) else []))
datas = package_data()
datas += [(str(p),'licenses') for p in (ROOT/'packaging/licenses').glob('*.txt')]
for dist in ("switchboard-ai", "pypdf", "mcp", "fastapi", "starlette", "pydantic", "anthropic", "openai", "httpx", "uvicorn"):
    try:
        datas += copy_metadata(dist)  # packages that read their own version via importlib.metadata
    except Exception:
        pass

a = Analysis([str(ROOT / "packaging" / "entry.py")], pathex=[str(ROOT)], hiddenimports=hidden, datas=datas,
             excludes=["tkinter", "pytest", "IPython", "matplotlib", "numpy", "PySide6", "switchboard.desktop_pet_ui"], noarchive=False)
pyz = PYZ(a.pure)

pet_a=pet_pyz=None
if WIN or MAC:
    pet_datas=package_data()
    pet_datas += [(str(p),'licenses') for p in (ROOT/'packaging/licenses').glob('*.txt')]
    for package in ('PySide6_Essentials','shiboken6'):
        pet_datas+=copy_metadata(package)
    pet_a=Analysis([str(ROOT/'packaging/pet_entry.py')],pathex=[str(ROOT)],datas=pet_datas,
                   excludes=['webview','pystray','tkinter','pytest','IPython','matplotlib','numpy'],noarchive=False)
    pet_pyz=PYZ(pet_a.pure)


def exe(name, console, analysis=a,archive=pyz):
    icon=('laith' if name=='Laith' else 'tazrivo')+('.ico' if WIN else '.icns')
    return EXE(archive, analysis.scripts, [], exclude_binaries=True, name=name, console=console, strip=False, upx=False,
               icon=str(ICONS/icon) if (WIN or MAC) else None,
               argv_emulation=False, codesign_identity=None, entitlements_file=None)


if WIN:
    # Switchboard.exe: no console window (Start menu, tray, start-at-login). switchboard-cli.exe: for terminals.
    coll = COLLECT(exe("Tazrivo", False), exe("tazrivo-cli", True), exe("Switchboard", False), exe("switchboard-cli", True),exe('Laith',False,pet_a,pet_pyz), a.binaries, a.datas,pet_a.binaries,pet_a.datas,
                   strip=False, upx=False, name="Tazrivo")
elif MAC:
    pet_coll=COLLECT(exe('Laith',False,pet_a,pet_pyz),pet_a.binaries,pet_a.datas,strip=False,upx=False,name='Laith')
    pet_app=BUNDLE(pet_coll,name='Laith.app',icon=str(ICONS/'laith.icns'),bundle_identifier='com.baguixgroup.switchboard.laith',version=VERSION,
                  info_plist={'CFBundleName':'Laith','CFBundleDisplayName':'Laith','CFBundleShortVersionString':VERSION,'CFBundleVersion':VERSION,
                              'LSUIElement':True,'LSMinimumSystemVersion':'14.4',
                              'NSMicrophoneUsageDescription':'Laith uses your microphone only when you start a voice conversation.',
                              'NSLocalNetworkUsageDescription':'Laith connects to your Tazrivo hub to handle the tasks you send.'})
    coll = COLLECT(exe("Tazrivo", False), exe("Switchboard", False), a.binaries, a.datas, strip=False, upx=False, name="Tazrivo")
    app = BUNDLE(coll, name="Tazrivo.app", icon=str(ICONS / "tazrivo.icns"),
                 bundle_identifier="com.baguixgroup.switchboard", version=VERSION,
                 info_plist={
                     "CFBundleName": "Tazrivo", "CFBundleDisplayName": "Tazrivo", "CFBundleExecutable": "Tazrivo",
                     "CFBundleShortVersionString": VERSION, "CFBundleVersion": VERSION,
                     # Dock icon while the window is open; the background menu-bar process hides itself.
                     "LSMinimumSystemVersion": "11.0",
                     "NSHumanReadableCopyright": "Tazrivo",
                     # macOS asks once before the app may talk to the hub on your network.
                     "NSLocalNetworkUsageDescription": "Tazrivo connects to your Tazrivo hub and other computers on your network.",
                     "NSAppleEventsUsageDescription": "Tazrivo opens apps such as ChatGPT when a task needs them.",
                     "NSMicrophoneUsageDescription": "Tazrivo uses your microphone only when you start a voice conversation with Laith.",
                 })
else:
    coll = COLLECT(exe("tazrivo", True),exe("switchboard", True), a.binaries, a.datas, strip=False, upx=False, name="switchboard")
