#!/usr/bin/env bash
# Switchboard installer for Raspberry Pi OS (64-bit recommended) and other Debian/Ubuntu Linux.
#   ./scripts/install-pi.sh hub
#   ./scripts/install-pi.sh agent http://192.168.1.50:8765 <hub token>
set -euo pipefail
ROLE="${1:-hub}"
HUB_URL="${2:-}"
TOKEN="${3:-}"
SRC="$(cd "$(dirname "$0")/.." && pwd)"
BASE="$HOME/.local/share/switchboard"

sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv python3-pip git openssh-client rsync

python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || {
  echo "Python 3.10+ is required (Raspberry Pi OS Bookworm ships 3.11)."; exit 1; }

mkdir -p "$BASE"
python3 -m venv "$BASE/venv"
"$BASE/venv/bin/pip" install --upgrade pip --quiet
"$BASE/venv/bin/pip" install "$SRC" --quiet

mkdir -p "$HOME/.local/bin"
ln -sf "$BASE/venv/bin/switchboard" "$HOME/.local/bin/switchboard"

args=(init --role "$ROLE")
[ -n "$HUB_URL" ] && args+=(--hub-url "$HUB_URL")
[ -n "$TOKEN" ] && args+=(--token "$TOKEN")
"$BASE/venv/bin/switchboard" "${args[@]}"
"$BASE/venv/bin/switchboard" service install --role "$ROLE"
sudo loginctl enable-linger "$USER" || true

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
[ "$ROLE" = "hub" ] && echo "Dashboard: http://${IP:-<pi-ip>}:8765   (agents use --hub-url http://${IP:-<pi-ip>}:8765)"
echo "Output folder: $HOME/switchboard-output"
echo "Check with: ~/.local/bin/switchboard doctor"
