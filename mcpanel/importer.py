"""Import a server from a zip / .mrpack / URL / existing folder, and detect what it is."""
from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import modrinth, net, paths
from .safezip import extract_all


def fetch_source(src: str, workdir: Path, log, progress) -> Path:
    src = src.strip().strip('"').strip("'")
    if src.startswith(("http://", "https://")):
        name = unquote(Path(urlparse(src).path).name) or "pack.zip"
        dest = workdir / name
        log(f"Downloading {src} ...")
        net.download(src, dest, progress)
        return dest
    p = Path(src).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"Not found: {p}")
    return p.resolve()


def import_into(src: Path, sd: Path, log, progress) -> dict:
    """Put the pack/server files into sd. Returns {'kind':..., 'deps':...}."""
    src = Path(src)
    if src.is_dir():
        log(f"Copying folder {src} ...")
        shutil.copytree(src, sd, dirs_exist_ok=True, symlinks=True)
        return {"kind": "dir"}
    if not zipfile.is_zipfile(src):
        raise ValueError("Unsupported file: need a .zip server pack, a .mrpack, or a folder")
    with zipfile.ZipFile(src) as z:
        is_mrpack = "modrinth.index.json" in z.namelist()
    if is_mrpack:
        deps = modrinth.install_mrpack(src, sd, log, progress)
        return {"kind": "mrpack", "deps": deps}
    log(f"Extracting {src.name} ...")
    n = extract_all(src, sd, progress=progress)
    log(f"Extracted {n} files")
    manifest = paths.read_json(sd / "manifest.json")
    if isinstance(manifest, dict) and manifest.get("manifestType") == "minecraftModpack" \
            and not (sd / "mods").exists():
        raise ValueError(
            "This is a CurseForge *client* pack (manifest.json only). Please use the pack's "
            "server pack instead (CurseForge tab, or download 'Server Pack' from its page).")
    return {"kind": "zip"}


def _neo_to_mc(v: str) -> str | None:
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", v)
    if not m:
        return None
    a, b, c = m.groups()
    if int(a) >= 25:          # year-based: 26.1.0.x -> 26.1
        return f"{a}.{b}" if c == "0" else f"{a}.{b}.{c}"
    return f"1.{a}" if b == "0" else f"1.{a}.{b}"


def detect_mc_version(sd: Path) -> str | None:
    sd = Path(sd)
    for p in sd.glob("*.jar"):
        m = re.match(r"forge-(1\.\d+(?:\.\d+)?)-", p.name)
        if m:
            return m.group(1)
        m = re.match(r"neoforge-([\d.]+)", p.name)
        if m and _neo_to_mc(m.group(1)):
            return _neo_to_mc(m.group(1))
        m = re.match(r"minecraft_server\.(.+)\.jar$", p.name)
        if m:
            return m.group(1)
    for d in sorted((sd / "libraries/net/minecraft/server").glob("*")):
        if d.is_dir():
            return d.name.split("-")[0]
    for d in (sd / "libraries/net/minecraftforge/forge").glob("*"):
        return d.name.split("-")[0]
    for d in (sd / "libraries/net/neoforged/neoforge").glob("*"):
        mc = _neo_to_mc(d.name)
        if mc:
            return mc
    var = sd / "variables.txt"
    if var.exists():
        m = re.search(r"^MINECRAFT_VERSION\s*=\s*\"?([\w.\-]+)", var.read_text(errors="replace"), re.M)
        if m:
            return m.group(1)
    manifest = paths.read_json(sd / "manifest.json")
    if isinstance(manifest, dict):
        v = (manifest.get("minecraft") or {}).get("version")
        if v:
            return v
    fprops = sd / ".fabric" / "server"
    for p in fprops.glob("*-server.jar"):
        return p.name.split("-")[0]
    return None


def detect_loader(sd: Path):
    sd = Path(sd)
    for d in (sd / "libraries/net/neoforged/neoforge").glob("*"):
        return "neoforge", d.name
    for d in (sd / "libraries/net/neoforged/forge").glob("*"):
        return "neoforge", d.name
    for d in (sd / "libraries/net/minecraftforge/forge").glob("*"):
        return "forge", d.name.split("-", 1)[-1]
    names = [p.name.lower() for p in sd.glob("*.jar")]
    if any(n.startswith("neoforge") for n in names):
        return "neoforge", ""
    if any(n.startswith("forge") for n in names):
        return "forge", ""
    if any(n.startswith("fabric") for n in names) or (sd / ".fabric").exists():
        return "fabric", ""
    if any(n.startswith("quilt") for n in names):
        return "quilt", ""
    if "server.jar" in names or any(n.startswith("minecraft_server") for n in names):
        return ("unknown" if (sd / "mods").exists() else "vanilla"), ""
    return "unknown", ""
