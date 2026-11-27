"""World / full-server backups (safe while the server is running)."""
from __future__ import annotations

import os
import shutil
import time
import zipfile
from pathlib import Path

from . import control
from .safezip import extract_all

SKIP_FULL = {"logs", "crash-reports", "cache", ".cache"}


def _world_dirs(inst) -> list:
    level = inst.properties().get("level-name", "world") or "world"
    sd = inst.server_dir
    return [d for d in (level, f"{level}_nether", f"{level}_the_end") if (sd / d).is_dir()]


def list_backups(inst) -> list:
    d = inst.backups_dir
    if not d.exists():
        return []
    items = [p for p in d.glob("*.zip") if p.is_file()]
    items.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return items


def backup_kind(p: Path) -> str:
    return "full" if p.stem.endswith("_full") else "world"


def create_backup(inst, kind: str = "world", log=lambda m: None) -> Path:
    sd = inst.server_dir
    inst.backups_dir.mkdir(parents=True, exist_ok=True)
    if kind == "world":
        roots = _world_dirs(inst)
        if not roots:
            raise RuntimeError("No world folder yet - start the server once first")
    else:
        roots = [p.name for p in sd.iterdir() if p.name not in SKIP_FULL]
    running = control.is_running(inst)
    if running:
        try:
            control.send_command(inst, "save-off")
            control.send_command(inst, "save-all flush")
            log("Waiting for the server to flush the world to disk...")
            time.sleep(6)
        except Exception as e:
            log(f"warning: could not pause saving: {e}")
    ts = time.strftime("%Y%m%d-%H%M%S")
    out = inst.backups_dir / f"{ts}_{kind}.zip"
    tmp = out.with_name(out.name + ".part")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6,
                             strict_timestamps=False) as z:
            for r in roots:
                base = sd / r
                items = [base] if base.is_file() else base.rglob("*")
                for p in items:
                    if not p.is_file() or p.is_symlink() or p.name == "session.lock":
                        continue
                    try:
                        z.write(p, p.relative_to(sd).as_posix())
                    except (OSError, ValueError) as e:
                        log(f"skipped {p.name}: {e}")
        os.replace(tmp, out)
    finally:
        if running:
            try:
                control.send_command(inst, "save-on")
            except Exception:
                pass
        if tmp.exists():
            tmp.unlink()
    prune(inst)
    log(f"Backup saved: {out.name}")
    return out


def prune(inst) -> None:
    keep = max(1, int(inst.backup_keep or 1))
    for p in list_backups(inst)[keep:]:
        try:
            p.unlink()
        except OSError:
            pass


def restore_backup(inst, path: Path, log=lambda m: None) -> None:
    if control.is_running(inst):
        raise RuntimeError("Stop the server before restoring a backup")
    sd = inst.server_dir
    if backup_kind(path) == "world":
        with zipfile.ZipFile(path) as z:
            tops = {n.split("/", 1)[0] for n in z.namelist() if n.strip("/")}
        for t in tops:
            cur = sd / t
            if cur.exists():
                old = sd / f"{t}.before-restore"
                if old.exists():
                    shutil.rmtree(old, ignore_errors=True)
                cur.rename(old)
                log(f"Current {t} kept as {old.name}")
    extract_all(path, sd, strip_root=False)
    log(f"Restored {path.name}")


def latest_backup_time(inst) -> float:
    b = list_backups(inst)
    return b[0].stat().st_mtime if b else 0.0
