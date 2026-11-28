#!/usr/bin/env bash
# Update MCPanel in place.
#
#   ./update.sh
#
# * pulls the newest code (if this folder is a git clone)
# * replaces only MCPanel's program files inside the existing install
# * restarts the always-on service WITHOUT stopping your running servers
#
# Your servers, worlds, mods, backups and settings in ~/mcpanel are never touched.
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${MCPANEL_PREFIX:-$HOME/.local/share/mcpanel}"
VENV="$PREFIX/venv"
DATA_DIR="${MCPANEL_HOME:-$HOME/mcpanel}"

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }

if [ ! -x "$VENV/bin/python" ]; then
  warn "MCPanel is not installed yet (no $VENV). Run ./install.sh first."
  exit 1
fi
OLD_VER=$(cd / && "$VENV/bin/python" -c 'import mcpanel; print(mcpanel.__version__)' 2>/dev/null || echo "?")

# 1. get the newest code
if [ -d "$SRC_DIR/.git" ]; then
  say "Downloading the latest version from GitHub"
  if ! git -C "$SRC_DIR" pull --ff-only; then
    warn "git pull failed (did you edit files in $SRC_DIR?)."
    warn "Fix with:  git -C \"$SRC_DIR\" stash && ./update.sh"
    exit 1
  fi
else
  say "Not a git clone - updating from the files in $SRC_DIR"
fi

# 2. replace only MCPanel's own code (dependencies are only touched if they are missing)
say "Updating MCPanel's program files"
"$VENV/bin/pip" install --quiet --no-deps --force-reinstall "$SRC_DIR"
"$VENV/bin/pip" install --quiet "$SRC_DIR"      # adds any newly required dependency
NEW_VER=$(cd / && "$VENV/bin/python" -c 'import mcpanel; print(mcpanel.__version__)')

# 3. restart the always-on service, keeping servers running
if command -v systemctl >/dev/null && systemctl is-active --quiet mcpanel 2>/dev/null; then
  if [ "$OLD_VER" = "1.0.0" ] || [ "$OLD_VER" = "?" ]; then
    warn "One-time note: your old version can't restart its service without stopping servers."
    warn "Running servers will be SAVED and stopped now; autostart ones come right back."
    warn "(Future updates keep servers running.)"
  fi
  SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"
  mkdir -p "$DATA_DIR/run"
  touch "$DATA_DIR/run/keep-servers"
  say "Restarting the background service (your servers keep running)"
  $SUDO systemctl restart mcpanel
  sleep 2
  if systemctl is-active --quiet mcpanel; then
    say "Service is running the new version"
  else
    warn "Service did not come back - check: journalctl -u mcpanel -n 50"
  fi
  rm -f "$DATA_DIR/run/keep-servers"
fi

echo
say "Updated MCPanel $OLD_VER -> $NEW_VER"
echo "    - Close and reopen 'mcpanel' if it is open."
echo "    - Servers that were already running keep going; press Restart (R) on them"
echo "      when convenient so they use the updated code too."
