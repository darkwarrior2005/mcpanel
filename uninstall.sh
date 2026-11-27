#!/usr/bin/env bash
# Removes the MCPanel program and service. Your servers/worlds in ~/mcpanel are NOT deleted.
set -euo pipefail
PREFIX="${MCPANEL_PREFIX:-$HOME/.local/share/mcpanel}"
SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"
if [ -f /etc/systemd/system/mcpanel.service ]; then
  echo "Stopping service (servers will be saved and stopped)..."
  $SUDO systemctl disable --now mcpanel.service || true
  $SUDO rm -f /etc/systemd/system/mcpanel.service
  $SUDO systemctl daemon-reload
fi
rm -rf "$PREFIX" "$HOME/.local/bin/mcpanel"
echo "MCPanel removed. Your data is still in ${MCPANEL_HOME:-$HOME/mcpanel} (delete it manually if you want)."
