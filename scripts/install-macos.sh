#!/usr/bin/env bash
# Switchboard installer for macOS.
#   ./scripts/install-macos.sh agent http://192.168.1.50:8765 <hub token>
#   ./scripts/install-macos.sh hub
set -euo pipefail
ROLE="${1:-agent}"
HUB_URL="${2:-}"
TOKEN="${3:-}"
SRC="$(cd "$(dirname "$0")/.." && pwd)"
BASE="$HOME/Library/Application Support/Switchboard"

py_ok() { "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; }
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null && py_ok "$c"; then PY="$(command -v "$c")"; break; fi
done
if [ -z "$PY" ]; then
  if command -v brew >/dev/null; then
    brew install python@3.12
    PY="$(brew --prefix)/bin/python3.12"
  else
    echo "Python 3.10+ is required. Install Homebrew (https://brew.sh) or Python from python.org, then rerun."
    exit 1
  fi
fi

mkdir -p "$BASE"
"$PY" -m venv "$BASE/venv"
"$BASE/venv/bin/pip" install --upgrade pip --quiet
"$BASE/venv/bin/pip" install "$SRC[tray]" --quiet

mkdir -p "$HOME/.local/bin"
ln -sf "$BASE/venv/bin/switchboard" "$HOME/.local/bin/switchboard"
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.zprofile"; echo "Added ~/.local/bin to PATH in ~/.zprofile";; esac

args=(init --role "$ROLE")
[ -n "$HUB_URL" ] && args+=(--hub-url "$HUB_URL")
[ -n "$TOKEN" ] && args+=(--token "$TOKEN")
"$BASE/venv/bin/switchboard" "${args[@]}"
"$BASE/venv/bin/switchboard" service install --role "$ROLE"

echo
echo "Done. Output folder: $HOME/Switchboard/Output"
echo "Check with: switchboard doctor"
