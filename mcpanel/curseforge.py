"""CurseForge modpack search + server-pack download (needs a free API key)."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from . import net

API = "https://api.curseforge.com/v1"
GAME_MINECRAFT = 432
CLASS_MODPACK = 4471


def _h(key: str) -> dict:
    if not key:
        raise ValueError("A CurseForge API key is required (get one at console.curseforge.com)")
    return {"x-api-key": key, "Accept": "application/json"}


def search(key: str, query: str) -> list:
    params = {"gameId": GAME_MINECRAFT, "classId": CLASS_MODPACK, "searchFilter": query,
              "sortField": 2, "sortOrder": "desc", "pageSize": 25}
    return net.get_json(f"{API}/mods/search", params=params, headers=_h(key))["data"]


def files(key: str, mod_id: int) -> list:
    data = net.get_json(f"{API}/mods/{mod_id}/files", params={"pageSize": 50},
                        headers=_h(key))["data"]
    data.sort(key=lambda f: f.get("fileDate", ""), reverse=True)
    return data


def file_info(key: str, mod_id: int, file_id: int) -> dict:
    return net.get_json(f"{API}/mods/{mod_id}/files/{file_id}", headers=_h(key))["data"]


def server_pack_files(key: str, mod_id: int) -> list:
    """Files that are, or have, a server pack."""
    return [f for f in files(key, mod_id) if f.get("serverPackFileId") or f.get("isServerPack")]


def _urls(fi: dict) -> list:
    urls = []
    if fi.get("downloadUrl"):
        urls.append(fi["downloadUrl"])
    fid = int(fi["id"])
    name = quote(fi["fileName"])
    for host in ("https://mediafilez.forgecdn.net", "https://edge.forgecdn.net"):
        urls.append(f"{host}/files/{fid // 1000}/{fid % 1000}/{name}")
    return urls


def download_server_pack(key: str, mod_id: int, file: dict, dest_dir: Path, log, progress) -> Path:
    if file.get("isServerPack"):
        fi = file
    elif file.get("serverPackFileId"):
        fi = file_info(key, mod_id, int(file["serverPackFileId"]))
    else:
        raise ValueError("This modpack version has no server pack. Download one manually "
                         "and use the Import tab.")
    dest = Path(dest_dir) / fi["fileName"]
    log(f"Downloading server pack {fi['fileName']}...")
    last = None
    for url in _urls(fi):
        try:
            return net.download(url, dest, progress, retries=2)
        except net.DownloadError as e:
            last = e
    raise RuntimeError(f"Could not download server pack: {last}")
