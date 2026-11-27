#!/usr/bin/env bash
# MCPanel installer - run as the user that should own the Minecraft servers.
#
#   ./install.sh               install + ask about the always-on service
#   ./install.sh --service     install + always-on systemd service (no question)
#   ./install.sh --no-service  install only
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${MCPANEL_PREFIX:-$HOME/.local/share/mcpanel}"
BIN_DIR="$HOME/.local/bin"
DATA_DIR="${MCPANEL_HOME:-$HOME/mcpanel}"
SERVICE=ask
for a in "$@"; do
  case "$a" in
    --service) SERVICE=yes ;;
    --no-service) SERVICE=no ;;
    -h|--help) sed -n 2,7p "$0"; exit 0 ;;
  esac
done

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }

command -v python3 >/dev/null || { warn "python3 not found. Install it: sudo apt install python3 python3-venv"; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
  || { warn "Python 3.9+ is required (found $(python3 -V))"; exit 1; }
python3 -c 'import venv, ensurepip' 2>/dev/null \
  || { warn "python3-venv is missing. Install it: sudo apt install python3-venv"; exit 1; }

say "Creating virtual environment in $PREFIX/venv"
mkdir -p "$PREFIX"
python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install --quiet --upgrade pip
say "Installing MCPanel and its dependencies"
"$PREFIX/venv/bin/pip" install --quiet --upgrade "$SRC_DIR"

mkdir -p "$BIN_DIR" "$DATA_DIR"
cat > "$BIN_DIR/mcpanel" <<EOF
#!/usr/bin/env bash
export MCPANEL_HOME="\${MCPANEL_HOME:-$DATA_DIR}"
exec "$PREFIX/venv/bin/python" -m mcpanel "\$@"
EOF
chmod +x "$BIN_DIR/mcpanel"
say "Installed the 'mcpanel' command to $BIN_DIR/mcpanel"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) warn "$BIN_DIR is not in your PATH. Add this to ~/.bashrc:  export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

# ---------------------------------------------------------------- systemd
if [ "$SERVICE" != no ] && command -v systemctl >/dev/null && [ -d /run/systemd/system ]; then
  if [ "$SERVICE" = ask ]; then
    if [ -t 0 ]; then
      read -rp "Install the always-on service (servers keep running after you log out and auto-start on boot)? [Y/n] " ans
      case "${ans:-y}" in [nN]*) SERVICE=no ;; *) SERVICE=yes ;; esac
    else
      SERVICE=no
    fi
  fi
  if [ "$SERVICE" = yes ]; then
    SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"
    UNIT=/etc/systemd/system/mcpanel.service
    say "Installing systemd service ($UNIT) - may ask for your sudo password"
    $SUDO tee "$UNIT" >/dev/null <<EOF
[Unit]
Description=MCPanel - Minecraft modpack servers
Wants=network-online.target
After=network-online.target tailscaled.service

[Service]
Type=simple
User=$(id -un)
Group=$(id -gn)
Environment=MCPANEL_HOME=$DATA_DIR
WorkingDirectory=$DATA_DIR
ExecStart=$PREFIX/venv/bin/python -m mcpanel daemon
# The daemon stops every server gracefully (world saved) when the service stops.
# KillMode=process keeps servers alive if only the daemon itself crashes/restarts.
KillMode=process
TimeoutStopSec=300
Restart=on-failure
RestartSec=5
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF
    $SUDO systemctl daemon-reload
    $SUDO systemctl enable --now mcpanel.service
    say "Service enabled: sudo systemctl status mcpanel   |   logs: journalctl -u mcpanel -f"
  fi
elif [ "$SERVICE" = yes ]; then
  warn "systemd not found - skipping the service. Servers still run in the background,"
  warn "but will not auto-start on boot."
fi

echo
say "Done! Start the panel with:  mcpanel"
echo "    Data folder: $DATA_DIR"
echo "    Tip: run it inside tmux/screen if you like, but servers keep running even if you close it."
