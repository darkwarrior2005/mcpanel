"""Create a new server end-to-end: download pack, install loader + Java, configure."""
from __future__ import annotations

import shutil

from . import (curseforge, importer, javamgr, loaders, mcversions, modrinth, net, paths,
               props)
from .instances import Instance, new_id

DEP_LOADERS = (("neoforge", "neoforge"), ("forge", "forge"),
               ("fabric-loader", "fabric"), ("quilt-loader", "quilt"))


def create_server(name: str, kind: str, params: dict, memory_mb: int, port: int,
                  eula: bool, log=print, progress=lambda d, t: None) -> Instance:
    """kind: modrinth | curseforge | import | basic"""
    paths.ensure_dirs()
    name = name.strip() or "Minecraft Server"
    iid = new_id(name)
    inst = Instance(id=iid, name=name, memory_mb=memory_mb,
                    min_memory_mb=min(1024, memory_mb))
    sd = inst.server_dir
    for d in (sd, inst.logs_dir, inst.backups_dir):
        d.mkdir(parents=True, exist_ok=True)
    work = paths.CACHE_DIR / f"dl-{iid}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        mc = loader = lv = ""
        deps = None
        if kind == "modrinth":
            ver = modrinth.version(params["version_id"])
            f = modrinth.primary_file(ver)
            log(f"Downloading {f['filename']} ...")
            mp = work / f["filename"]
            net.download(f["url"], mp, progress, sha1=(f.get("hashes") or {}).get("sha1"))
            deps = modrinth.install_mrpack(mp, sd, log, progress)
            inst.source = {"type": "modrinth", "project_id": params.get("project_id", ""),
                           "version_id": ver["id"], "title": params.get("title", ""),
                           "version": ver.get("version_number", "")}
        elif kind == "curseforge":
            zp = curseforge.download_server_pack(params["api_key"], params["mod_id"],
                                                 params["file"], work, log, progress)
            info = importer.import_into(zp, sd, log, progress)
            deps = info.get("deps")
            inst.source = {"type": "curseforge", "mod_id": params["mod_id"],
                           "file_id": params["file"].get("id"), "title": params.get("title", ""),
                           "version": params["file"].get("displayName", "")}
        elif kind == "import":
            sp = importer.fetch_source(params["source"], work, log, progress)
            info = importer.import_into(sp, sd, log, progress)
            deps = info.get("deps")
            inst.source = {"type": "import", "source": params["source"]}
        elif kind == "basic":
            loader = params.get("loader") or "vanilla"
            mc = params.get("mc_version") or mcversions.latest_release()
            lv = params.get("loader_version") or ""
            inst.source = {"type": "basic"}
        else:
            raise ValueError(f"unknown kind {kind}")

        if deps:
            mc = deps.get("minecraft", mc)
            loader = "vanilla"
            for key, ln in DEP_LOADERS:
                if key in deps:
                    loader, lv = ln, deps[key]
                    break
        if not mc:
            mc = importer.detect_mc_version(sd) or ""
        inst.mc_version = mc
        inst.java_major = mcversions.java_major_for(mc) if mc else 21
        log(f"Minecraft {mc or 'unknown'} -> needs Java {inst.java_major}")
        java = javamgr.ensure_java(inst.java_major, log, progress)

        if loader:
            lv2, launch = loaders.install(loader, sd, mc, lv, java, log, progress)
            inst.loader, inst.loader_version = loader, lv2 or lv
        else:
            mode, _ = loaders.detect_launch(sd)
            ins = loaders.find_installer(sd)
            if ins and mode in (None, "script"):
                log(f"Running the pack's bundled installer {ins.name} ...")
                loaders.run_installer(java, ins, ["--installServer"], sd, log)
            launch = loaders.detect_launch(sd)
            inst.loader, inst.loader_version = importer.detect_loader(sd)

        inst.launch_mode, inst.launch_target = (launch if launch and launch[0] else ("", ""))
        if inst.launch_mode:
            log(f"Launch: {inst.launch_mode} -> {inst.launch_target}")
        else:
            log("WARNING: could not detect how to start this server. "
                "Set 'Launch mode/target' in the Settings tab.")

        existing = props.read_props(inst.props_file)
        updates = {"server-port": str(port), "query.port": str(port)}
        if "motd" not in existing:
            updates["motd"] = name
        if inst.loader not in ("vanilla", ""):
            updates.setdefault("allow-flight", existing.get("allow-flight", "true"))
        props.write_props(inst.props_file, updates)
        if eula:
            inst.accept_eula()
        inst.save()
        log(f"Server '{name}' created! Select it and press Start.")
        return inst
    except BaseException:
        shutil.rmtree(inst.dir, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
