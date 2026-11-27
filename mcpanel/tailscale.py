"""Read this machine's Tailscale address so the TUI can show 'connect to' info.

info() returns {"ip4", "dns", "state"} when connected, otherwise
{"error": <why>, "fix": <command that fixes it>}.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess

COMMON_PATHS = ["/usr/bin/tailscale", "/usr/sbin/tailscale", "/usr/local/bin/tailscale",
                "/snap/bin/tailscale", "/opt/homebrew/bin/tailscale"]

STATE_HELP = {
    "NeedsLogin": ("logged out", "sudo tailscale up"),
    "NeedsMachineAuth": ("waiting for admin approval", "approve it at login.tailscale.com/admin"),
    "Stopped": ("turned off", "sudo tailscale up"),
    "Starting": ("starting...", ""),
    "NoState": ("not started", "sudo systemctl enable --now tailscaled"),
}


def find_cli():
    exe = shutil.which("tailscale")
    if exe:
        return exe
    for p in COMMON_PATHS:
        if os.access(p, os.X_OK):
            return p
    return None


def interface_ip() -> str | None:
    """IPv4 of the tailscale0 interface (works even without the CLI)."""
    try:
        out = subprocess.run(["ip", "-o", "-4", "addr", "show", "dev", "tailscale0"],
                             capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"inet (100\.\d+\.\d+\.\d+)", out)
    return m.group(1) if m else None


def info() -> dict:
    exe = find_cli()
    if not exe:
        ip = interface_ip()
        if ip:
            return {"ip4": ip, "dns": "", "state": "Running"}
        return {"error": "not installed on this machine",
                "fix": "curl -fsSL https://tailscale.com/install.sh | sh"}
    try:
        out = subprocess.run([exe, "status", "--json"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as e:
        return {"error": f"could not run tailscale ({e})", "fix": ""}
    try:
        d = json.loads(out.stdout or "{}")
    except ValueError:
        d = {}
    if not d:
        err = (out.stderr or out.stdout or "").lower()
        if "tailscaled" in err or "connect" in err or "socket" in err:
            return {"error": "the tailscaled service is not running",
                    "fix": "sudo systemctl enable --now tailscaled"}
        ip = interface_ip()
        if ip:
            return {"ip4": ip, "dns": "", "state": "Running"}
        return {"error": (out.stderr or "no answer from tailscale").strip()[:80], "fix": ""}
    state = d.get("BackendState", "")
    me = d.get("Self") or {}
    ips = me.get("TailscaleIPs") or d.get("TailscaleIPs") or []
    ip4 = next((i for i in ips if "." in i), None)
    if state == "Running" and ip4:
        return {"ip4": ip4, "dns": (me.get("DNSName") or "").rstrip("."), "state": state}
    why, fix = STATE_HELP.get(state, (f"state: {state or 'unknown'}", "sudo tailscale up"))
    return {"error": why, "fix": fix, "state": state}


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()
