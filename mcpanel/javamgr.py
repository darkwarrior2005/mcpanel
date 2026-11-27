"""Find or auto-download the right Java version (Eclipse Temurin via Adoptium)."""
from __future__ import annotations

import glob
import os
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

from . import net, paths

JAVA_CHOICES = [8, 11, 17, 21, 25]


def _arch() -> str:
    m = platform.machine().lower()
    return {"x86_64": "x64", "amd64": "x64", "aarch64": "aarch64", "arm64": "aarch64",
            "armv7l": "arm", "ppc64le": "ppc64le", "s390x": "s390x",
            "riscv64": "riscv64"}.get(m, m)


def _os() -> str:
    return "alpine-linux" if Path("/etc/alpine-release").exists() else "linux"


def managed_java(major: int):
    p = paths.JAVA_DIR / str(major) / "bin" / "java"
    return p if p.exists() else None


def java_version_of(java) -> int | None:
    try:
        out = subprocess.run([str(java), "-version"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r'version "(\d+)(?:\.(\d+))?', out.stderr + out.stdout)
    if not m:
        return None
    first = int(m.group(1))
    if first == 1 and m.group(2):
        return int(m.group(2))
    return first


def find_system_java(major: int):
    candidates = []
    for base in ("/usr/lib/jvm", "/usr/java", "/opt/java", "/usr/local/lib/jvm", "/opt"):
        candidates += glob.glob(os.path.join(base, "*", "bin", "java"))
        candidates += glob.glob(os.path.join(base, "*", "jre", "bin", "java"))
    w = shutil.which("java")
    if w:
        candidates.append(w)
    seen = set()
    for c in candidates:
        real = os.path.realpath(c)
        if real in seen or not os.access(real, os.X_OK):
            continue
        seen.add(real)
        if java_version_of(real) == major:
            return Path(real)
    return None


def java_for(inst):
    """Java binary for an instance without downloading (None if missing)."""
    if inst.java_path:
        p = Path(inst.java_path).expanduser()
        return p if p.exists() else None
    return managed_java(inst.java_major) or find_system_java(inst.java_major)


def ensure_java(major: int, log=print, progress=None) -> str:
    p = managed_java(major) or find_system_java(major)
    if p:
        return str(p)
    return str(download_java(major, log, progress))


def ensure_java_for(inst, log=print, progress=None) -> str:
    if inst.java_path:
        p = Path(inst.java_path).expanduser()
        if not p.exists():
            raise RuntimeError(f"Custom Java path does not exist: {p}")
        return str(p)
    return ensure_java(inst.java_major, log, progress)


def _safe_extract_tar(tf: tarfile.TarFile, dest: Path) -> None:
    if hasattr(tarfile, "data_filter"):
        tf.extractall(dest, filter="data")
        return
    root = dest.resolve()
    for m in tf.getmembers():
        target = (root / m.name).resolve()
        if target != root and root not in target.parents:
            raise RuntimeError(f"Unsafe path in archive: {m.name}")
        if m.issym() or m.islnk():
            link = (target.parent / m.linkname).resolve()
            if root not in link.parents and link != root:
                raise RuntimeError(f"Unsafe link in archive: {m.name}")
    tf.extractall(dest)


def download_java(major: int, log=print, progress=None) -> Path:
    arch, osn = _arch(), _os()
    dest = paths.JAVA_DIR / str(major)
    paths.JAVA_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=str(paths.JAVA_DIR)) as td:
        tgz = Path(td) / "java.tar.gz"
        last = None
        for image in ("jre", "jdk"):
            url = (f"https://api.adoptium.net/v3/binary/latest/{major}/ga/"
                   f"{osn}/{arch}/{image}/hotspot/normal/eclipse")
            log(f"Downloading Java {major} ({image}, {arch}) from Adoptium...")
            try:
                net.download(url, tgz, progress=progress, retries=2, timeout=120)
                break
            except net.DownloadError as e:
                last = e
        else:
            raise RuntimeError(
                f"Could not download Java {major}: {last}\n"
                f"Install it yourself (e.g. 'sudo apt install openjdk-{major}-jre-headless') "
                f"or set a custom Java path in the server settings.")
        log("Extracting Java...")
        ex = Path(td) / "x"
        ex.mkdir()
        with tarfile.open(tgz) as tf:
            _safe_extract_tar(tf, ex)
        dirs = [p for p in ex.iterdir() if p.is_dir()]
        root = dirs[0] if len(dirs) == 1 else ex
        if not (root / "bin" / "java").exists():
            raise RuntimeError("Downloaded Java archive has an unexpected layout")
        if dest.exists():
            shutil.rmtree(dest)
        shutil.move(str(root), str(dest))
    log(f"Java {major} installed at {dest}")
    return dest / "bin" / "java"
