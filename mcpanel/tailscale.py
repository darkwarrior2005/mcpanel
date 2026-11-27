"""Read this machine's Tailscale address so the TUI can show 'connect to' info."""
from __future__ import annotations

import json
import shutil
import socket
import subprocess


def info() -> dict:
    exe = shutil.which("tailscale")
    if not exe:
        return {}
    try:
        out = subprocess.run([exe, "status", "--json"], capture_output=True, text=True, timeout=4)
        d = json.loads(out.stdout or "{}")
        me = d.get("Self") or {}
        ips = me.get("TailscaleIPs") or d.get("TailscaleIPs") or []
        ip4 = next((i for i in ips if "." in i), None)
        dns = (me.get("DNSName") or "").rstrip(".")
        return {"ip4": ip4, "dns": dns, "state": d.get("BackendState", ""),
                "online": me.get("Online", True)}
    except Exception:
        pass
    try:
        out = subprocess.run([exe, "ip", "-4"], capture_output=True, text=True, timeout=4)
        ip = out.stdout.strip().splitlines()[0] if out.stdout.strip() else None
        return {"ip4": ip, "dns": "", "state": "Running" if ip else "", "online": bool(ip)}
    except Exception:
        return {}


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()
