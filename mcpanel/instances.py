"""Server instances: one folder + instance.json per server."""
from __future__ import annotations

import re
import shutil
import socket
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from . import paths, props


@dataclass
class Instance:
    id: str
    name: str
    mc_version: str = ""
    loader: str = ""            # vanilla / fabric / quilt / forge / neoforge / unknown
    loader_version: str = ""
    source: dict = field(default_factory=dict)
    java_major: int = 21
    java_path: str = ""         # optional override, otherwise managed/system Java
    memory_mb: int = 4096       # -Xmx
    min_memory_mb: int = 1024   # -Xms
    jvm_args: str = ""
    aikar_flags: bool = True
    launch_mode: str = ""       # argsfile / jar / script
    launch_target: str = ""
    autostart: bool = False     # start automatically when the machine boots
    auto_restart: bool = True   # restart after a crash
    backup_interval_h: float = 0.0
    backup_keep: int = 5
    created: float = field(default_factory=time.time)

    # ---- paths -------------------------------------------------------
    @property
    def dir(self) -> Path:
        return paths.SERVERS_DIR / self.id

    @property
    def server_dir(self) -> Path:
        return self.dir / "server"

    @property
    def backups_dir(self) -> Path:
        return self.dir / "backups"

    @property
    def logs_dir(self) -> Path:
        return self.dir / "logs"

    @property
    def file(self) -> Path:
        return self.dir / "instance.json"

    @property
    def props_file(self) -> Path:
        return self.server_dir / "server.properties"

    # ---- persistence -------------------------------------------------
    def save(self) -> None:
        paths.atomic_write_json(self.file, asdict(self))

    @classmethod
    def load(cls, iid: str) -> "Instance":
        data = paths.read_json(paths.SERVERS_DIR / iid / "instance.json")
        if not isinstance(data, dict):
            raise KeyError(iid)
        known = {f.name for f in fields(cls)}
        data = {k: v for k, v in data.items() if k in known}
        data["id"] = iid
        return cls(**data)

    def reload(self) -> "Instance":
        return Instance.load(self.id)

    # ---- helpers -----------------------------------------------------
    def properties(self) -> dict:
        return props.read_props(self.props_file)

    @property
    def port(self) -> int:
        try:
            return int(self.properties().get("server-port", "25565"))
        except ValueError:
            return 25565

    def eula_accepted(self) -> bool:
        try:
            txt = (self.server_dir / "eula.txt").read_text(errors="replace")
        except OSError:
            return False
        return re.search(r"^\s*eula\s*=\s*true\s*$", txt, re.M | re.I) is not None

    def accept_eula(self) -> None:
        (self.server_dir / "eula.txt").write_text(
            "#Accepted via MCPanel (https://aka.ms/MinecraftEULA)\neula=true\n")

    def describe(self) -> str:
        loader = (self.loader or "?").title()
        if self.loader == "neoforge":
            loader = "NeoForge"
        return f"{loader} {self.mc_version or '?'}"


def list_instances() -> list:
    out = []
    if not paths.SERVERS_DIR.exists():
        return out
    for d in paths.SERVERS_DIR.iterdir():
        if (d / "instance.json").exists():
            try:
                out.append(Instance.load(d.name))
            except Exception:
                pass
    out.sort(key=lambda i: i.name.lower())
    return out


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32].strip("-")
    return s or "server"


def new_id(name: str) -> str:
    base = slugify(name)
    iid, n = base, 2
    while (paths.SERVERS_DIR / iid).exists():
        iid = f"{base}-{n}"
        n += 1
    return iid


def find_instance(key: str):
    insts = list_instances()
    for i in insts:
        if i.id == key:
            return i
    low = key.lower()
    for i in insts:
        if i.name.lower() == low:
            return i
    matches = [i for i in insts if i.id.startswith(low) or i.name.lower().startswith(low)]
    return matches[0] if len(matches) == 1 else None


def port_in_use(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("0.0.0.0", port))
        return False
    except OSError:
        return True
    finally:
        s.close()


def used_ports(exclude_id: str = "") -> dict:
    return {i.port: i for i in list_instances() if i.id != exclude_id}


def next_free_port(start: int = 25565) -> int:
    used = used_ports()
    p = start
    while p in used or port_in_use(p):
        p += 1
    return p


def delete_instance(inst: Instance) -> None:
    shutil.rmtree(inst.dir)
