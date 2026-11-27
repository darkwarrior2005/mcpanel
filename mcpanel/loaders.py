"""Install mod loaders (Vanilla, Fabric, Quilt, Forge, NeoForge) and detect how to launch."""
from __future__ import annotations

import glob
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from . import mcversions, net

LOADERS = ["vanilla", "fabric", "forge", "neoforge", "quilt"]


def _vt(v: str):
    return tuple(int(x) for x in re.findall(r"\d+", v or ""))


def run_installer(java, jar, args, cwd, log) -> None:
    cmd = [str(java), "-jar", str(jar), *args]
    log("Running: " + " ".join(cmd))
    p = subprocess.Popen(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, text=True, errors="replace")
    assert p.stdout is not None
    for line in p.stdout:
        line = line.rstrip()
        if line:
            log("  " + line)
    rc = p.wait()
    if rc != 0:
        raise RuntimeError(f"Installer exited with code {rc}")


# ---------------------------------------------------------------- vanilla
def install_vanilla(sd: Path, mc: str, log, progress):
    url, sha1 = mcversions.server_download(mc)
    log(f"Downloading Minecraft {mc} server.jar...")
    net.download(url, sd / "server.jar", progress, sha1=sha1)
    return "", ("jar", "server.jar")


# ---------------------------------------------------------------- fabric
def fabric_latest_loader(mc: str) -> str:
    data = net.get_json(f"https://meta.fabricmc.net/v2/versions/loader/{mc}")
    if not data:
        raise ValueError(f"Fabric does not support Minecraft {mc}")
    stable = [d for d in data if d["loader"].get("stable")]
    return (stable or data)[0]["loader"]["version"]


def install_fabric(sd: Path, mc: str, lv: str, log, progress):
    lv = lv or fabric_latest_loader(mc)
    installers = net.get_json("https://meta.fabricmc.net/v2/versions/installer")
    iv = next((i["version"] for i in installers if i.get("stable")), installers[0]["version"])
    url = f"https://meta.fabricmc.net/v2/versions/loader/{mc}/{lv}/{iv}/server/jar"
    log(f"Downloading Fabric server launcher (loader {lv})...")
    net.download(url, sd / "fabric-server-launch.jar", progress)
    try:
        install_vanilla(sd, mc, log, progress)
    except Exception as e:  # the Fabric launcher can fetch it itself on first start
        log(f"(vanilla server.jar will be downloaded on first start: {e})")
    return lv, ("jar", "fabric-server-launch.jar")


# ---------------------------------------------------------------- quilt
def install_quilt(sd: Path, mc: str, lv: str, java, log, progress):
    if not lv:
        data = net.get_json(f"https://meta.quiltmc.org/v3/versions/loader/{mc}")
        if not data:
            raise ValueError(f"Quilt does not support Minecraft {mc}")
        vers = [d["loader"]["version"] for d in data]
        stable = [v for v in vers if "beta" not in v and "pre" not in v]
        lv = (stable or vers)[0]
    base = "https://maven.quiltmc.org/repository/release/org/quiltmc/quilt-installer"
    root = ET.fromstring(net.get_text(f"{base}/maven-metadata.xml"))
    iv = root.findtext("versioning/release") or root.findtext("versioning/latest")
    jar = sd / "quilt-installer.jar"
    log(f"Downloading Quilt installer {iv}...")
    net.download(f"{base}/{iv}/quilt-installer-{iv}.jar", jar, progress)
    run_installer(java, jar, ["install", "server", mc, lv, "--download-server",
                              f"--install-dir={sd}"], sd, log)
    jar.unlink(missing_ok=True)
    return lv, detect_launch(sd)


# ---------------------------------------------------------------- forge
def forge_version(mc: str, lv: str) -> str:
    if lv:
        return lv if lv.startswith(mc + "-") else f"{mc}-{lv}"
    promos = net.get_json(
        "https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json")["promos"]
    v = promos.get(f"{mc}-recommended") or promos.get(f"{mc}-latest")
    if not v:
        raise ValueError(f"No Forge build found for Minecraft {mc}")
    return f"{mc}-{v}"


def _cleanup_installer(sd: Path, jar: Path) -> None:
    for p in (jar, Path(str(jar) + ".log"), sd / "installer.log"):
        try:
            p.unlink()
        except OSError:
            pass


def install_forge(sd: Path, mc: str, lv: str, java, log, progress):
    full = forge_version(mc, lv)
    url = (f"https://maven.minecraftforge.net/net/minecraftforge/forge/{full}/"
           f"forge-{full}-installer.jar")
    jar = sd / f"forge-{full}-installer.jar"
    log(f"Downloading Forge {full} installer...")
    net.download(url, jar, progress)
    run_installer(java, jar, ["--installServer"], sd, log)
    _cleanup_installer(sd, jar)
    return full.split("-", 1)[1], detect_launch(sd)


# ---------------------------------------------------------------- neoforge
NEO_MAVEN = "https://maven.neoforged.net"


def neoforge_prefix(mc: str) -> str:
    parts = mc.split(".")
    if parts[0] == "1":
        minor = parts[1]
        patch = parts[2] if len(parts) > 2 else "0"
        return f"{minor}.{patch}."
    a, b = parts[0], parts[1] if len(parts) > 1 else "0"
    c = parts[2] if len(parts) > 2 else "0"
    return f"{a}.{b}.{c}."


def neoforge_version(mc: str, lv: str) -> str:
    if lv:
        return lv
    if mc == "1.20.1":
        data = net.get_json(f"{NEO_MAVEN}/api/maven/versions/releases/net/neoforged/forge")
        versions = [v for v in data.get("versions", []) if v.startswith("1.20.1-")]
    else:
        data = net.get_json(f"{NEO_MAVEN}/api/maven/versions/releases/net/neoforged/neoforge")
        prefix = neoforge_prefix(mc)
        versions = [v for v in data.get("versions", []) if v.startswith(prefix)]
    if not versions:
        raise ValueError(f"No NeoForge build found for Minecraft {mc}")
    stable = [v for v in versions if "beta" not in v and "alpha" not in v]
    return max(stable or versions, key=_vt)


def install_neoforge(sd: Path, mc: str, lv: str, java, log, progress):
    v = neoforge_version(mc, lv)
    if mc == "1.20.1" or v.startswith("1.20.1-") or v.startswith("47."):
        artifact = "forge"
        v = v if v.startswith("1.20.1-") else f"1.20.1-{v}"
    else:
        artifact = "neoforge"
    url = f"{NEO_MAVEN}/releases/net/neoforged/{artifact}/{v}/{artifact}-{v}-installer.jar"
    jar = sd / f"neoforge-{v}-installer.jar"
    log(f"Downloading NeoForge {v} installer...")
    net.download(url, jar, progress)
    run_installer(java, jar, ["--installServer"], sd, log)
    _cleanup_installer(sd, jar)
    return v, detect_launch(sd)


# ---------------------------------------------------------------- dispatch
def install(loader: str, sd: Path, mc: str, lv: str, java, log, progress):
    """Returns (loader_version, (launch_mode, launch_target))."""
    loader = (loader or "vanilla").lower()
    if loader == "vanilla":
        return install_vanilla(sd, mc, log, progress)
    if loader == "fabric":
        return install_fabric(sd, mc, lv, log, progress)
    if loader == "quilt":
        return install_quilt(sd, mc, lv, java, log, progress)
    if loader == "forge":
        return install_forge(sd, mc, lv, java, log, progress)
    if loader == "neoforge":
        return install_neoforge(sd, mc, lv, java, log, progress)
    raise ValueError(f"Unknown loader '{loader}'")


# ---------------------------------------------------------------- detection
ARGS_GLOBS = [
    "libraries/net/neoforged/neoforge/*/unix_args.txt",
    "libraries/net/neoforged/forge/*/unix_args.txt",
    "libraries/net/minecraftforge/forge/*/unix_args.txt",
]
SCRIPT_NAMES = ["start.sh", "startserver.sh", "start-server.sh", "serverstart.sh",
                "launch.sh", "run.sh"]


def find_installer(sd: Path):
    for p in sorted(Path(sd).glob("*.jar")):
        n = p.name.lower()
        if "installer" in n and ("forge" in n or "neoforge" in n):
            return p
    return None


def detect_launch(sd):
    """Figure out how to start a server folder -> (mode, target) or (None, None)."""
    sd = Path(sd)
    run_sh = sd / "run.sh"
    if run_sh.exists():
        txt = run_sh.read_text(errors="replace")
        m = re.search(r"@(libraries/\S+?unix_args\.txt)", txt)
        if m and (sd / m.group(1)).exists():
            return "argsfile", m.group(1)
        m = re.search(r"-jar\s+\"?([\w.\-/]+\.jar)", txt)
        if m and (sd / m.group(1)).exists():
            return "jar", m.group(1)
    found = []
    for g in ARGS_GLOBS:
        found += glob.glob(str(sd / g))
    if found:
        best = max(found, key=lambda p: _vt(Path(p).parent.name))
        return "argsfile", str(Path(best).relative_to(sd))
    for name in ("fabric-server-launch.jar", "fabric-server-launcher.jar",
                 "quilt-server-launch.jar"):
        if (sd / name).exists():
            return "jar", name
    jars = sorted(p.name for p in sd.glob("*.jar"))
    forge = [j for j in jars if re.match(r"(neo)?forge-.*\.jar$", j, re.I)
             and "installer" not in j.lower()]
    if forge:
        forge.sort(key=lambda j: ("shim" not in j, "universal" in j, j))
        return "jar", forge[0]
    if "server.jar" in jars:
        return "jar", "server.jar"
    vanilla = [j for j in jars if j.startswith("minecraft_server")]
    if vanilla:
        return "jar", vanilla[0]
    others = [j for j in jars if "installer" not in j.lower()]
    if len(others) == 1:
        return "jar", others[0]
    lower = {p.name.lower(): p.name for p in sd.iterdir() if p.is_file()}
    for s in SCRIPT_NAMES:
        if s in lower:
            return "script", lower[s]
    return None, None
