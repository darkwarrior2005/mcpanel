"""Operators, whitelist and bans - via live commands when running, via JSON files otherwise."""
from __future__ import annotations

import hashlib
import re
import time
import uuid

from . import control, net, paths

FILES = {"ops": "ops.json", "whitelist": "whitelist.json", "bans": "banned-players.json"}
NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,32}$")


def offline_uuid(name: str) -> str:
    h = hashlib.md5(("OfflinePlayer:" + name).encode("utf-8")).digest()
    return str(uuid.UUID(bytes=h, version=3))


def lookup_player(name: str, online_mode: bool = True):
    if not online_mode:
        return offline_uuid(name), name
    try:
        d = net.get_json(f"https://api.mojang.com/users/profiles/minecraft/{name}", timeout=10)
        return str(uuid.UUID(d["id"])), d.get("name", name)
    except Exception as e:
        raise ValueError(f"Could not find a Minecraft account named '{name}' ({e})")


def read_list(inst, kind: str) -> list:
    data = paths.read_json(inst.server_dir / FILES[kind], [])
    return data if isinstance(data, list) else []


def _write_list(inst, kind: str, data: list) -> None:
    paths.atomic_write_json(inst.server_dir / FILES[kind], data)


def _without(entries: list, name: str) -> list:
    low = name.lower()
    return [e for e in entries if str(e.get("name", "")).lower() != low]


def player_action(inst, action: str, name: str, level: int = 4, reason: str = "") -> str:
    """action: op, deop, wladd, wlrm, kick, ban, pardon"""
    name = name.strip()
    if not NAME_RE.match(name):
        raise ValueError("Invalid player name")
    reason = reason.strip()
    if control.is_running(inst):
        cmd = {
            "op": f"op {name}",
            "deop": f"deop {name}",
            "wladd": f"whitelist add {name}",
            "wlrm": f"whitelist remove {name}",
            "kick": f"kick {name} {reason}".strip(),
            "ban": f"ban {name} {reason}".strip(),
            "pardon": f"pardon {name}",
        }[action]
        control.send_command(inst, cmd)
        extra = " (live servers use the default op level from Settings)" if action == "op" else ""
        return f"Sent /{cmd}{extra}"

    if action == "kick":
        raise ValueError("The server is not running, nobody to kick")
    online = inst.properties().get("online-mode", "true").lower() == "true"
    if action == "deop":
        _write_list(inst, "ops", _without(read_list(inst, "ops"), name))
        return f"Removed {name} from operators"
    if action == "wlrm":
        _write_list(inst, "whitelist", _without(read_list(inst, "whitelist"), name))
        return f"Removed {name} from the whitelist"
    if action == "pardon":
        _write_list(inst, "bans", _without(read_list(inst, "bans"), name))
        return f"Unbanned {name}"

    pid, proper = lookup_player(name, online)
    if action == "op":
        data = _without(read_list(inst, "ops"), proper)
        data.append({"uuid": pid, "name": proper, "level": int(level),
                     "bypassesPlayerLimit": False})
        _write_list(inst, "ops", data)
        return f"{proper} is now an operator (level {level})"
    if action == "wladd":
        data = _without(read_list(inst, "whitelist"), proper)
        data.append({"uuid": pid, "name": proper})
        _write_list(inst, "whitelist", data)
        return f"Added {proper} to the whitelist"
    if action == "ban":
        data = _without(read_list(inst, "bans"), proper)
        data.append({"uuid": pid, "name": proper,
                     "created": time.strftime("%Y-%m-%d %H:%M:%S +0000", time.gmtime()),
                     "source": "Server", "expires": "forever",
                     "reason": reason or "Banned by an operator."})
        _write_list(inst, "bans", data)
        return f"Banned {proper}"
    raise ValueError(f"Unknown action {action}")
