"""Safe zip extraction (no path traversal) shared by modpack installers."""
from __future__ import annotations

import os
import shutil
import zipfile
from pathlib import Path


def safe_join(base, rel: str) -> Path:
    base = Path(base).resolve()
    rel = rel.replace("\\", "/").lstrip("/")
    target = (base / rel).resolve()
    if target != base and base not in target.parents:
        raise RuntimeError(f"Unsafe path in archive: {rel}")
    return target


def extract_member(z: zipfile.ZipFile, info: zipfile.ZipInfo, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with z.open(info) as src, open(target, "wb") as out:
        shutil.copyfileobj(src, out, 1 << 20)
    mode = (info.external_attr >> 16) & 0o777
    if mode & 0o111:
        os.chmod(target, 0o755)


def common_root(z: zipfile.ZipFile) -> str:
    """If every entry sits in one top-level folder, return 'folder/'."""
    names = [n for n in z.namelist() if n.strip("/") and not n.startswith("__MACOSX")]
    tops = {n.split("/", 1)[0] for n in names}
    if len(tops) == 1:
        top = next(iter(tops))
        if all(n.startswith(top + "/") for n in names):
            return top + "/"
    return ""


def extract_all(zip_path, dest, prefix: str = "", strip_root: bool = True, progress=None) -> int:
    """Extract (optionally only entries under `prefix`) into dest. Returns file count."""
    dest = Path(dest)
    n = 0
    with zipfile.ZipFile(zip_path) as z:
        root = common_root(z) if (strip_root and not prefix) else ""
        cut = prefix or root
        infos = [i for i in z.infolist() if not i.is_dir() and i.filename.startswith(cut)
                 and not i.filename.startswith("__MACOSX")]
        for idx, info in enumerate(infos):
            rel = info.filename[len(cut):]
            if not rel:
                continue
            extract_member(z, info, safe_join(dest, rel))
            n += 1
            if progress:
                progress(idx + 1, len(infos))
    return n
