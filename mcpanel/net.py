"""Small HTTP helpers (JSON + verified, resumable-safe downloads)."""
from __future__ import annotations

import hashlib
import os
import threading
import time
from pathlib import Path

import requests

from . import __version__

UA = f"mcpanel/{__version__} (self-hosted Minecraft modpack server manager)"
_local = threading.local()


class DownloadError(Exception):
    pass


def session() -> requests.Session:
    s = getattr(_local, "s", None)
    if s is None:
        s = requests.Session()
        s.headers["User-Agent"] = UA
        _local.s = s
    return s


def get_json(url, params=None, headers=None, timeout=30):
    r = session().get(url, params=params, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.json()


def get_text(url, params=None, headers=None, timeout=30) -> str:
    r = session().get(url, params=params, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.text


def download(url, dest, progress=None, sha1=None, sha512=None, headers=None,
             retries=3, timeout=60) -> Path:
    """Download url -> dest atomically, optionally verifying a hash.

    progress(done_bytes, total_bytes) is called while downloading.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    last = None
    for attempt in range(retries):
        try:
            with session().get(url, stream=True, headers=headers, timeout=timeout,
                               allow_redirects=True) as r:
                r.raise_for_status()
                total = int(r.headers.get("Content-Length") or 0)
                h1 = hashlib.sha1() if sha1 else None
                h5 = hashlib.sha512() if sha512 else None
                done = 0
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(1 << 16):
                        if not chunk:
                            continue
                        f.write(chunk)
                        done += len(chunk)
                        if h1:
                            h1.update(chunk)
                        if h5:
                            h5.update(chunk)
                        if progress:
                            progress(done, total)
            if h1 and h1.hexdigest() != sha1.lower():
                raise DownloadError(f"SHA1 mismatch for {dest.name}")
            if h5 and h5.hexdigest() != sha512.lower():
                raise DownloadError(f"SHA512 mismatch for {dest.name}")
            os.replace(tmp, dest)
            return dest
        except (requests.RequestException, DownloadError, OSError) as e:
            last = e
            if isinstance(e, requests.HTTPError) and e.response is not None \
                    and e.response.status_code in (401, 403, 404):
                break  # retrying will not help
            time.sleep(1.5 * (attempt + 1))
    try:
        tmp.unlink()
    except OSError:
        pass
    raise DownloadError(f"Failed to download {url}: {last}")
