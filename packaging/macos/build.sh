#!/usr/bin/env bash
# Build Tazrivo.app and Tazrivo-<version>-macos-<arch>.dmg (Python included).
#   bash packaging/macos/build.sh
# Needs: uv (https://docs.astral.sh/uv) or Python 3.11+ on PATH. Builds for this Mac's CPU (x86_64 or arm64).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
PYVER="${PYVER:-3.13}"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' switchboard/__init__.py)"
ARCH="$(uname -m)"
VENV="build/venv-macos-$ARCH"

if command -v uv >/dev/null || [ -x "$HOME/.local/bin/uv" ]; then
  UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
  [ -x "$VENV/bin/python" ] || "$UV" venv -q -p "$PYVER" "$VENV"
  VIRTUAL_ENV="$VENV" "$UV" pip install -q --only-binary cryptography ".[tray,documents]" "pyinstaller>=6.10"
else
  [ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q --upgrade pip
  "$VENV/bin/pip" install -q --only-binary cryptography ".[tray,documents]" "pyinstaller>=6.10"
fi

"$VENV/bin/python" packaging/make_icons.py
"$VENV/bin/pyinstaller" --noconfirm --clean --log-level WARN --distpath dist --workpath build/pyinstaller packaging/switchboard.spec

APP="dist/Tazrivo.app"
mkdir -p "$APP/Contents/Helpers"
ditto dist/Laith.app "$APP/Contents/Helpers/Laith.app"
# Ad-hoc signature with a fixed identifier: no Apple account needed, and macOS keeps the app's
# Local Network permission across updates. Use DEVELOPER_ID="Developer ID Application: ..." to sign properly.
# (Hardened runtime only with a real identity: with an ad-hoc signature it blocks loading the bundled Python.)
if [ -n "${DEVELOPER_ID:-}" ]; then
  codesign --force --deep --options runtime --timestamp --sign "$DEVELOPER_ID" --identifier com.baguixgroup.switchboard "$APP"
else
  codesign --force --deep --sign - --identifier com.baguixgroup.switchboard "$APP"
fi
codesign --verify --deep --strict "$APP"

DMG="dist/Tazrivo-$VERSION-macos-$ARCH.dmg"
STAGE="build/dmg"
rm -rf "$STAGE" "$DMG"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -quiet -volname "Tazrivo $VERSION" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
rm -rf "$STAGE"
echo "Built $DMG"
