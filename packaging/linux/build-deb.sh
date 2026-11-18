#!/usr/bin/env bash
# Build switchboard_<version>_<arch>.deb for Raspberry Pi OS / Debian / Ubuntu (Python included).
#   bash packaging/linux/build-deb.sh
# Build on the oldest system you want to support (the app needs that glibc or newer): Raspberry Pi OS
# Bullseye builds run on Bullseye, Bookworm and Trixie. Needs: uv or Python 3.11+, dpkg-deb.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
PYVER="${PYVER:-3.13}"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' switchboard/__init__.py)"
ARCH="$(dpkg --print-architecture)"
VENV="build/venv-linux-$ARCH"

UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
if [ -x "$UV" ]; then
  [ -x "$VENV/bin/python" ] || "$UV" venv -q -p "$PYVER" "$VENV"
  VIRTUAL_ENV="$VENV" "$UV" pip install -q . "pyinstaller>=6.10"
else
  [ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q --upgrade pip
  "$VENV/bin/pip" install -q . "pyinstaller>=6.10"
fi
"$VENV/bin/pyinstaller" --noconfirm --clean --log-level WARN --distpath dist --workpath build/pyinstaller packaging/switchboard.spec

PKG="build/deb/switchboard_${VERSION}_${ARCH}"
rm -rf "$PKG"
mkdir -p "$PKG/DEBIAN" "$PKG/opt" "$PKG/usr/bin"
cp -a dist/switchboard "$PKG/opt/switchboard"
ln -s /opt/switchboard/switchboard "$PKG/usr/bin/switchboard"
SIZE="$(du -sk "$PKG/opt" | cut -f1)"
cat > "$PKG/DEBIAN/control" <<EOF
Package: switchboard
Version: $VERSION
Architecture: $ARCH
Maintainer: Switchboard <noreply@switchboard.local>
Installed-Size: $SIZE
Depends: libc6
Recommends: git, rsync, openssh-client
Section: utils
Priority: optional
Homepage: https://github.com/baguixgroup/switchboard
Description: One control plane for your AI models, credits, tools and machines
 Runs the Switchboard hub (dashboard, router, credit ledger) or an agent that
 connects this computer to your hub. Python is included.
EOF
cat > "$PKG/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = "configure" ]; then
  U="${SUDO_USER:-}"
  echo
  echo "Switchboard is installed. Set it up as your normal user (not root):"
  echo "    switchboard setup"
  echo "It asks whether this computer is your hub or connects to one, then starts by itself at boot."
  if [ -n "$U" ] && command -v loginctl >/dev/null 2>&1; then
    loginctl enable-linger "$U" 2>/dev/null || true  # keep it running when nobody is logged in
  fi
  # Already set up (upgrade): restart the user's service so it runs the new version.
  if [ -n "$U" ] && [ -d "/home/$U/.config/systemd/user" ]; then
    for s in switchboard-hub switchboard-agent; do
      if [ -f "/home/$U/.config/systemd/user/$s.service" ]; then
        su - "$U" -c "XDG_RUNTIME_DIR=/run/user/\$(id -u) systemctl --user restart $s" 2>/dev/null || true
      fi
    done
  fi
fi
EOF
chmod 0755 "$PKG/DEBIAN/postinst"
mkdir -p dist
dpkg-deb --build --root-owner-group "$PKG" "dist/switchboard_${VERSION}_${ARCH}.deb" >/dev/null
echo "Built dist/switchboard_${VERSION}_${ARCH}.deb"
