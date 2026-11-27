"""Filesystem layout and global configuration.

Everything lives under MCPANEL_HOME (default: ~/mcpanel):

    config.json          global settings (CurseForge API key, defaults)
    servers/<id>/        one folder per server
        instance.json    MCPanel settings for this server
        server/          the actual Minecraft server files
        backups/         world / full backups
        logs/            MCPanel console + runner logs
    java/<major>/        auto-downloaded Java runtimes
    run/                 sockets, lock files and status files
    cache/               downloads in progress, version manifests
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def _home() -> Path:
    raw = os.environ.get("MCPANEL_HOME") or str(Path.home() / "mcpanel")
    return Path(raw).expanduser().resolve()


HOME = _home()
SERVERS_DIR = HOME / "servers"
JAVA_DIR = HOME / "java"
RUN_DIR = HOME / "run"
CACHE_DIR = HOME / "cache"
CONFIG_FILE = HOME / "config.json"

DEFAULT_CONFIG = {
    "curseforge_api_key": "",
    "default_memory_mb": 4096,
    "stop_timeout_s": 90,       # how long to wait for "stop" before SIGTERM
    "restart_delay_s": 10,      # wait before auto-restarting a crashed server
    "max_restarts": 3,          # crash restarts allowed ...
    "restart_window_s": 600,    # ... within this many seconds
}


def ensure_dirs() -> None:
    for d in (HOME, SERVERS_DIR, JAVA_DIR, RUN_DIR, CACHE_DIR):
        d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(RUN_DIR, 0o700)
    except OSError:
        pass


def atomic_write_text(path, text: str, mode=None) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix="." + path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        if mode is None:
            # keep the permissions of the file we replace (or a sane default)
            try:
                mode = os.stat(path).st_mode & 0o777
            except OSError:
                mode = 0o644
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path, data, mode=None) -> None:
    atomic_write_text(path, json.dumps(data, indent=2), mode)


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    data = read_json(CONFIG_FILE, {})
    if isinstance(data, dict):
        cfg.update(data)
    return cfg


def save_config(cfg: dict) -> None:
    atomic_write_json(CONFIG_FILE, cfg, mode=0o600)
