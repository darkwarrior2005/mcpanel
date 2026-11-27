"""Minecraft version metadata (from Mojang) and Java version requirements."""
from __future__ import annotations

import re
import time

from . import net, paths

MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"


def manifest(max_age_s: int = 6 * 3600) -> dict:
    cache = paths.CACHE_DIR / "version_manifest.json"
    try:
        if cache.exists() and time.time() - cache.stat().st_mtime < max_age_s:
            data = paths.read_json(cache)
            if data:
                return data
    except OSError:
        pass
    try:
        data = net.get_json(MANIFEST_URL, timeout=15)
        paths.atomic_write_json(cache, data)
        return data
    except Exception:
        data = paths.read_json(cache)
        if data:
            return data
        raise


def latest_release() -> str:
    return manifest()["latest"]["release"]


def version_json(mc: str) -> dict:
    cache = paths.CACHE_DIR / "versions" / f"{mc}.json"
    data = paths.read_json(cache)
    if data:
        return data
    entry = next((v for v in manifest()["versions"] if v["id"] == mc), None)
    if not entry:
        raise ValueError(f"Unknown Minecraft version '{mc}'")
    data = net.get_json(entry["url"], timeout=20)
    paths.atomic_write_json(cache, data)
    return data


def server_download(mc: str):
    vj = version_json(mc)
    srv = (vj.get("downloads") or {}).get("server")
    if not srv:
        raise ValueError(f"Minecraft {mc} has no official server download")
    return srv["url"], srv.get("sha1")


def heuristic_java(mc: str) -> int:
    m = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?", mc or "")
    if not m:
        return 21
    a, b, c = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    if a == 1:
        if b <= 16:
            return 8
        if b == 17:
            return 17
        if b < 20 or (b == 20 and c <= 4):
            return 17
        return 21
    return 25  # year-based versions (26.x and newer)


def java_major_for(mc: str) -> int:
    if not mc:
        return 21
    try:
        major = int(version_json(mc)["javaVersion"]["majorVersion"])
        # Java 16 builds are hard to find; 17 runs 1.17.x fine.
        return 17 if major == 16 else major
    except Exception:
        return heuristic_java(mc)
