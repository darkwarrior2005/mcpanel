"""Modrinth modpack search + .mrpack server installation."""
from __future__ import annotations

import json
import shutil
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import moddeps, net, paths
from .safezip import extract_member, safe_join

API = "https://api.modrinth.com/v2"


def search(query: str = "", limit: int = 25, offset: int = 0) -> list:
    params = {
        "query": query,
        "facets": json.dumps([["project_type:modpack"]]),
        "limit": limit,
        "offset": offset,
        "index": "relevance" if query else "downloads",
    }
    return net.get_json(f"{API}/search", params=params).get("hits", [])


def versions(project_id: str) -> list:
    return net.get_json(f"{API}/project/{project_id}/version")


def version(version_id: str) -> dict:
    return net.get_json(f"{API}/version/{version_id}")


def primary_file(ver: dict) -> dict:
    files = ver.get("files") or []
    mr = [f for f in files if f["filename"].endswith(".mrpack")]
    prim = [f for f in mr if f.get("primary")]
    if not (prim or mr or files):
        raise ValueError("This version has no downloadable files")
    return (prim or mr or files)[0]


def install_mrpack(mrpack_path, sd: Path, log, progress) -> dict:
    """Install the server side of an .mrpack into sd. Returns its dependencies dict."""
    sd = Path(sd)
    with zipfile.ZipFile(mrpack_path) as z:
        index = json.loads(z.read("modrinth.index.json"))
        files = [f for f in index.get("files", [])
                 if (f.get("env") or {}).get("server", "required") != "unsupported"]
        skipped = len(index.get("files", [])) - len(files)
        log(f"Pack: {index.get('name', '?')} {index.get('versionId', '')} - "
            f"{len(files)} server files ({skipped} client-only skipped)")

        def fetch(f):
            target = safe_join(sd, f["path"])
            hashes = f.get("hashes") or {}
            err = None
            for url in f.get("downloads") or []:
                try:
                    net.download(url, target, sha1=hashes.get("sha1"),
                                 sha512=None if hashes.get("sha1") else hashes.get("sha512"),
                                 retries=2)
                    return f["path"]
                except Exception as e:  # try the next mirror
                    err = e
            raise RuntimeError(f"{f['path']}: {err or 'no download URL'}")

        failed = []
        done = 0
        lock = threading.Lock()
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = [ex.submit(fetch, f) for f in files]
            for fut in as_completed(futs):
                try:
                    path = fut.result()
                    log(f"  + {path}")
                except Exception as e:
                    failed.append(str(e))
                    log(f"  ! {e}")
                with lock:
                    done += 1
                    progress(done, len(files))
        if failed:
            raise RuntimeError(f"{len(failed)} file(s) failed to download")

        for prefix in ("overrides/", "server-overrides/"):
            infos = [i for i in z.infolist() if i.filename.startswith(prefix) and not i.is_dir()]
            if infos:
                log(f"Applying {prefix} ({len(infos)} files)...")
            for info in infos:
                rel = info.filename[len(prefix):]
                if rel:
                    extract_member(z, info, safe_join(sd, rel))

    skipped_mods = [f for f in index.get("files", [])
                    if (f.get("env") or {}).get("server") == "unsupported"
                    and f["path"].startswith("mods/") and f["path"].endswith(".jar")]
    if skipped_mods:
        restore_needed_libraries(skipped_mods, sd, log)
    return index.get("dependencies", {})


def restore_needed_libraries(skipped: list, sd: Path, log) -> None:
    """Some packs mark libraries as client-only although server mods require them.
    Download the skipped mods, and put back any that provide a missing dependency."""
    installed = moddeps.scan_dir(sd / "mods")
    missing = moddeps.missing_deps(installed)
    if not missing:
        return
    log(f"Server mods need {', '.join(sorted(missing))} - checking the pack's client-only files...")
    cache = paths.CACHE_DIR / "skipped-mods"
    cache.mkdir(parents=True, exist_ok=True)

    def fetch(f):
        target = cache / Path(f["path"]).name
        if not target.exists():
            for url in f.get("downloads") or []:
                try:
                    net.download(url, target, sha1=(f.get("hashes") or {}).get("sha1"), retries=2)
                    break
                except Exception:
                    continue
        return f, (moddeps.jar_info(target) if target.exists() else None)

    with ThreadPoolExecutor(max_workers=8) as ex:
        candidates = [r for r in ex.map(fetch, skipped) if r[1]]
    provided = set().union(*(i["ids"] for i in installed.values()))
    changed = True
    while missing and changed:
        changed = False
        for f, info in candidates:
            if info["ids"] & missing and not (sd / f["path"]).exists():
                target = safe_join(sd, f["path"])
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(cache / Path(f["path"]).name, target)
                log(f"  + {f['path']}  (pack marks it client-only, but the server needs "
                    f"{', '.join(sorted(info['ids'] & missing))})")
                provided |= info["ids"]
                missing = (missing | (info["deps"] - moddeps.IGNORE)) - provided
                changed = True
    if missing:
        log(f"WARNING: still missing dependencies: {', '.join(sorted(missing))}")
