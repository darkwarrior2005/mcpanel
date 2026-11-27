"""Read Fabric/Quilt mod metadata from jars: mod ids, required dependencies, Java version.

Used to (a) pick a Java version high enough for every mod in a pack, and
(b) restore library mods that a pack wrongly marks as client-only although
server-side mods depend on them.
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path

# ids that are not mods you can install
IGNORE = {"minecraft", "java", "fabricloader", "fabric-loader", "quilt_loader", "quilt_base",
          "quilt_loader_stub"}


def java_from_predicate(pred) -> int | None:
    preds = pred if isinstance(pred, list) else [pred]
    best = None
    for p in preds:
        if not isinstance(p, str):
            continue
        m = re.search(r"(?:>=|>|\^|~|=)?\s*(\d+)", p)
        if m:
            v = int(m.group(1))
            if p.strip().startswith(">") and not p.strip().startswith(">="):
                v += 1
            best = v if best is None else min(best, v)  # any satisfying alternative
    return best


def _loads(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8", "replace"), strict=False)
    except ValueError:
        return None


def jar_info(src, depth: int = 0) -> dict:
    """src: path or bytes. Returns {'ids': set, 'deps': set, 'java': int|None}."""
    info = {"ids": set(), "deps": set(), "java": None}
    try:
        z = zipfile.ZipFile(io.BytesIO(src) if isinstance(src, (bytes, bytearray)) else src)
    except (zipfile.BadZipFile, OSError):
        return info
    with z:
        names = set(z.namelist())
        nested = []
        if "fabric.mod.json" in names:
            d = _loads(z.read("fabric.mod.json"))
            if isinstance(d, dict):
                if d.get("id"):
                    info["ids"].add(d["id"])
                info["ids"].update(p for p in d.get("provides", []) if isinstance(p, str))
                for k, v in (d.get("depends") or {}).items():
                    if k == "java":
                        info["java"] = java_from_predicate(v)
                    elif k not in IGNORE:
                        info["deps"].add(k)
                nested = [j.get("file") for j in d.get("jars", []) if isinstance(j, dict)]
        elif "quilt.mod.json" in names:
            d = _loads(z.read("quilt.mod.json"))
            ql = (d or {}).get("quilt_loader") or {}
            if ql.get("id"):
                info["ids"].add(ql["id"])
            for p in ql.get("provides", []):
                pid = p.get("id") if isinstance(p, dict) else p
                if pid:
                    info["ids"].add(pid)
            for dep in ql.get("depends", []):
                if isinstance(dep, str):
                    did, opt = dep, False
                elif isinstance(dep, dict):
                    did, opt = dep.get("id"), dep.get("optional", False)
                else:
                    continue
                if not did or opt:
                    continue
                if did == "java":
                    info["java"] = java_from_predicate(dep.get("versions", "")) \
                        if isinstance(dep, dict) else None
                elif did not in IGNORE:
                    info["deps"].add(did)
            nested = [j if isinstance(j, str) else j.get("file") for j in ql.get("jars", [])]
        if depth < 2:
            for n in nested:
                if n and n in names:
                    # Bundled (jar-in-jar) modules are optional for the loader: if their own
                    # requirements fail they are simply not loaded. So they only *provide* ids.
                    info["ids"] |= jar_info(z.read(n), depth + 1)["ids"]
    return info


def scan_dir(mods_dir) -> dict:
    """{filename: info} for every enabled jar in a mods folder."""
    out = {}
    d = Path(mods_dir)
    if d.is_dir():
        for p in sorted(d.glob("*.jar")):
            out[p.name] = jar_info(p)
    return out


def required_java(mods_dir) -> int | None:
    vals = [i["java"] for i in scan_dir(mods_dir).values() if i["java"]]
    return max(vals) if vals else None


def missing_deps(infos: dict) -> set:
    provided = set().union(*(i["ids"] for i in infos.values())) if infos else set()
    needed = set().union(*(i["deps"] for i in infos.values())) if infos else set()
    return needed - provided - IGNORE
