"""Real end-to-end test of MCPanel on a cloud machine (GitHub Actions).

Uses the real internet: Mojang, Fabric/NeoForge/Forge, Modrinth, Adoptium (Java).
Writes a Markdown report + logs to $REPORT_DIR (default ./report).
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
import traceback
import zipfile
from pathlib import Path

os.environ.setdefault("MCPANEL_HOME", str(Path.home() / "mcpanel"))

from mcpanel import (backups, control, instances, javamgr, mcversions, modrinth,  # noqa: E402
                     paths, players, props, provision)

REPORT = Path(os.environ.get("REPORT_DIR", "report")).resolve()
REPORT.mkdir(parents=True, exist_ok=True)
RESULTS: list = []
T0 = time.time()


# ------------------------------------------------------------------ helpers
def log(msg: str) -> None:
    print(f"[{time.time() - T0:6.0f}s] {msg}", flush=True)


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(ok), detail))
    log(("PASS " if ok else "FAIL ") + name + (f" - {detail}" if detail else ""))
    return bool(ok)


def step(name: str):
    """Decorator: run a test step, record an exception as a failure."""
    def deco(fn):
        def run(*a, **kw):
            log(f"===== {name} =====")
            try:
                return fn(*a, **kw)
            except Exception as e:
                check(f"{name}: no exception", False, f"{type(e).__name__}: {e}")
                traceback.print_exc()
                return None
        return run
    return deco


def wait_for(pred, timeout: float, interval: float = 1.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(interval)
    return pred()


def state(inst) -> str:
    return control.read_status(inst).get("state", "?")


def wait_running(inst, timeout=600) -> bool:
    # give up early once the runner has given up (crashed for good)
    wait_for(lambda: state(inst) in ("running", "crashed"), timeout, 2)
    return state(inst) == "running"


def wait_stopped(inst, timeout=180) -> bool:
    return wait_for(lambda: not control.is_running(inst), timeout, 1)


def console_text(inst) -> str:
    try:
        return (inst.logs_dir / "console.log").read_text(errors="replace")
    except OSError:
        return ""


def ping(port: int, host: str = "127.0.0.1", tries: int = 6) -> dict:
    """Server List Ping, exactly what the Minecraft multiplayer screen does (via mcstatus)."""
    from mcstatus import JavaServer
    last = None
    for i in range(tries):
        try:
            return JavaServer(host, port, timeout=10).status().raw
        except Exception as e:
            last = e
            log(f"   ping attempt {i + 1} failed: {type(e).__name__}: {e}")
            time.sleep(5)
    raise RuntimeError(f"no ping answer after {tries} tries: {type(last).__name__}: {last}")


def create(name, kind, params, mem=3072):
    port = instances.next_free_port()
    lines = []
    inst = provision.create_server(name, kind, params, mem, port, True,
                                   log=lambda m: (lines.append(str(m)), print("   ", m, flush=True)),
                                   progress=lambda d, t: None)
    (REPORT / f"install-{inst.id}.log").write_text("\n".join(lines))
    return inst


def start_and_wait(inst, timeout=600) -> bool:
    t = time.time()
    ok = control.start(inst, wait=15)
    ok = ok and wait_running(inst, timeout)
    check(f"{inst.name}: starts and reaches 'Done' ({time.time() - t:.0f}s)", ok,
          f"state={state(inst)}")
    return ok


def stop_and_check_saved(inst) -> bool:
    level = inst.properties().get("level-name", "world")
    ld = inst.server_dir / level / "level.dat"
    before = ld.stat().st_mtime if ld.exists() else 0
    time.sleep(1.5)
    t = time.time()
    control.stop(inst)
    stopped = wait_stopped(inst, 240)
    st = control.read_status(inst)
    check(f"{inst.name}: graceful stop ({time.time() - t:.0f}s)", stopped and st.get("exit_code") == 0,
          f"state={st.get('state')} exit={st.get('exit_code')}")
    txt = console_text(inst)
    tail = txt[-20000:]
    saved_msgs = any(k in tail for k in ("All dimensions are saved", "Saving chunks for level",
                                         "Saving worlds", "ThreadedAnvilChunkStorage"))
    after = ld.stat().st_mtime if ld.exists() else 0
    return check(f"{inst.name}: world saved on stop", saved_msgs and after >= before and ld.exists(),
                 f"save messages in log={saved_msgs}, level.dat updated={after > before}")


# ------------------------------------------------------------------ steps
@step("Environment")
def env_info():
    log(f"MCPANEL_HOME={paths.HOME}")
    latest = mcversions.latest_release()
    check("Mojang version manifest reachable", bool(latest), f"latest release {latest}")
    check("Java requirement lookup", mcversions.java_major_for(latest) >= 17,
          f"MC {latest} -> Java {mcversions.java_major_for(latest)}")
    return latest


@step("Fabric server (latest Minecraft)")
def test_fabric():
    inst = create("Cloud Fabric", "basic", {"loader": "fabric"})
    check("Fabric: created", inst is not None and (inst.server_dir / "fabric-server-launch.jar").exists(),
          f"MC {inst.mc_version}, loader {inst.loader_version}, Java {inst.java_major}")
    java = javamgr.java_for(inst)
    check("Fabric: Java auto-downloaded", bool(java) and Path(java).exists(), str(java))
    # settings edited "from the TUI": motd, max players, offline mode for test players
    props.write_props(inst.props_file, {"motd": "MCPanel cloud test", "max-players": "7",
                                        "online-mode": "false"})
    if not start_and_wait(inst, 300):
        return inst
    try:
        info = ping(inst.port)
        desc = info.get("description")
        motd = desc.get("text", "") if isinstance(desc, dict) else str(desc)
        if isinstance(desc, dict) and not motd:
            motd = "".join(x.get("text", "") for x in desc.get("extra", []))
        check("Fabric: answers multiplayer ping on its port",
              info.get("players", {}).get("max") == 7 and "MCPanel cloud test" in motd,
              f"version={info.get('version', {}).get('name')} motd={motd!r} "
              f"max={info.get('players', {}).get('max')}")
    except Exception as e:
        check("Fabric: answers multiplayer ping on its port", False, str(e))

    # console commands
    control.send_command(inst, "say hello from MCPanel")
    control.send_command(inst, "list")
    time.sleep(3)
    txt = console_text(inst)
    check("Fabric: console commands work", "hello from MCPanel" in txt and "max of 7 players" in txt)

    # operators / whitelist live
    players.player_action(inst, "op", "CloudTester")
    players.player_action(inst, "wladd", "CloudTester")
    time.sleep(3)
    ops = [e["name"] for e in players.read_list(inst, "ops")]
    wl = [e["name"] for e in players.read_list(inst, "whitelist")]
    check("Fabric: make operator (live)", "CloudTester" in ops, f"ops.json={ops}")
    check("Fabric: whitelist add (live)", "CloudTester" in wl, f"whitelist.json={wl}")

    # backup while running
    b = backups.create_backup(inst, "world", log=lambda m: log("   " + m))
    with zipfile.ZipFile(b) as z:
        names = z.namelist()
    check("Fabric: world backup while running", any(n.endswith("level.dat") for n in names),
          f"{b.name}, {len(names)} files, {b.stat().st_size // 1024} KB")

    # restart
    old = control.read_status(inst).get("started_at")
    control.restart(inst)
    ok = wait_for(lambda: control.read_status(inst).get("started_at") != old and state(inst) == "running",
                  300, 2)
    check("Fabric: restart", ok, f"state={state(inst)}")

    # crash -> auto restart
    pid = control.read_status(inst).get("server_pid")
    old = control.read_status(inst).get("started_at")
    if pid:
        os.kill(pid, signal.SIGKILL)
        ok = wait_for(lambda: control.read_status(inst).get("started_at") != old and state(inst) == "running",
                      300, 2)
        check("Fabric: auto-restart after crash (java killed)", ok, f"state={state(inst)}")

    stop_and_check_saved(inst)

    # offline edits + restore
    players.player_action(inst, "op", "OfflineAdmin", level=2)
    players.player_action(inst, "ban", "Griefer", reason="testing bans")
    ops = {e["name"]: e.get("level") for e in players.read_list(inst, "ops")}
    bans = [e["name"] for e in players.read_list(inst, "bans")]
    check("Fabric: op/ban while offline (file edit)", ops.get("OfflineAdmin") == 2 and "Griefer" in bans,
          f"ops={ops} bans={bans}")
    backups.restore_backup(inst, b, log=lambda m: log("   " + m))
    check("Fabric: restore backup", (inst.server_dir / "world" / "level.dat").exists()
          and (inst.server_dir / "world.before-restore").exists())
    if start_and_wait(inst, 300):
        time.sleep(2)
        ops = {e["name"]: e.get("level") for e in players.read_list(inst, "ops")}
        check("Fabric: offline op survives server start", ops.get("OfflineAdmin") == 2, f"ops={ops}")
        stop_and_check_saved(inst)
    return inst


@step("COBBLEVERSE modpack from Modrinth (full real install)")
def test_modrinth():
    # find it exactly like the TUI does: search Modrinth, click the result, pick newest version
    hits = modrinth.search("cobbleverse")
    check("Modrinth: search 'cobbleverse'", len(hits) > 0,
          ", ".join(h["title"] for h in hits[:5]))
    hit = next((h for h in hits if h.get("slug") == "cobbleverse"), None) \
        or next((h for h in hits if "cobbleverse" in h["title"].lower()), None)
    if not check("Modrinth: Cobbleverse found", hit is not None):
        return None
    vers = modrinth.versions(hit["project_id"])
    rel = [v for v in vers if v.get("version_type") == "release"] or vers
    ver = rel[0]
    log(f"   picked {hit['title']} {ver['version_number']} "
        f"(MC {ver.get('game_versions')}, {ver.get('loaders')})")
    t = time.time()
    inst = create("Cobbleverse", "modrinth", {"project_id": hit["project_id"], "version_id": ver["id"],
                                              "title": hit["title"]}, mem=8192)
    mods = sorted(p.name for p in (inst.server_dir / "mods").glob("*.jar"))
    has_cobblemon = any("cobblemon" in m.lower() for m in mods)
    check(f"Cobbleverse: installed {ver['version_number']} ({time.time() - t:.0f}s)",
          len(mods) > 10 and has_cobblemon,
          f"{len(mods)} server mods incl. Cobblemon={has_cobblemon}, MC {inst.mc_version}, "
          f"{inst.loader} {inst.loader_version}, Java {inst.java_major}")
    (REPORT / "cobbleverse-mods.txt").write_text("\n".join(mods))
    if start_and_wait(inst, 1500):
        try:
            info = ping(inst.port)
            check("Cobbleverse: answers multiplayer ping", "version" in info,
                  f"version={info.get('version', {}).get('name')}")
        except Exception as e:
            check("Cobbleverse: answers multiplayer ping", False, str(e))
        control.send_command(inst, "list")
        time.sleep(3)
        txt = console_text(inst)
        check("Cobbleverse: Cobblemon loaded on the server", "cobblemon" in txt.lower())
        crash = list((inst.server_dir / "crash-reports").glob("*.txt")) \
            if (inst.server_dir / "crash-reports").exists() else []
        check("Cobbleverse: no crash reports", not crash, ", ".join(p.name for p in crash))
        b = backups.create_backup(inst, "world", log=lambda m: log("   " + m))
        check("Cobbleverse: world backup while running", b.exists(),
              f"{b.name} {b.stat().st_size // (1024 * 1024)} MB")
        stop_and_check_saved(inst)
    else:
        for p in (inst.server_dir / "crash-reports").glob("*.txt") \
                if (inst.server_dir / "crash-reports").exists() else []:
            shutil.copy(p, REPORT / f"cobbleverse-{p.name}")
    return inst


@step("NeoForge server (installer + args file)")
def test_neoforge():
    inst = create("Cloud NeoForge", "basic", {"loader": "neoforge", "mc_version": "1.21.1"})
    check("NeoForge: installed", inst.launch_mode == "argsfile",
          f"{inst.loader_version}, launch {inst.launch_mode} {inst.launch_target}")
    if start_and_wait(inst, 600):
        stop_and_check_saved(inst)
    return inst


@step("Forge server (installer + args file)")
def test_forge():
    inst = create("Cloud Forge", "basic", {"loader": "forge", "mc_version": "1.20.1"})
    check("Forge: installed", inst.launch_mode in ("argsfile", "jar"),
          f"{inst.loader_version}, launch {inst.launch_mode} {inst.launch_target}")
    if start_and_wait(inst, 600):
        stop_and_check_saved(inst)
    return inst


@step("Always-on daemon (boot autostart + graceful shutdown)")
def test_daemon(inst):
    if not inst:
        check("Daemon: skipped (no Fabric server)", False)
        return
    inst = inst.reload()
    inst.autostart = True
    inst.save()
    d = subprocess.Popen([sys.executable, "-m", "mcpanel", "daemon"],
                         stdout=open(REPORT / "daemon.log", "w"), stderr=subprocess.STDOUT)
    check("Daemon: autostarts server", wait_running(inst, 300), f"state={state(inst)}")
    d.send_signal(signal.SIGTERM)
    try:
        d.wait(240)
    except subprocess.TimeoutExpired:
        d.kill()
    check("Daemon: SIGTERM (reboot) stops servers cleanly",
          not control.is_running(inst) and control.read_status(inst).get("exit_code") == 0,
          f"state={state(inst)} exit={control.read_status(inst).get('exit_code')}")
    stop_tail = console_text(inst)[-8000:]
    check("Daemon: world saved during shutdown", "All dimensions are saved" in stop_tail
          or "Saving chunks" in stop_tail)


@step("TUI renders with real servers")
def test_tui(inst):
    import asyncio
    from mcpanel.tui import MCPanelApp

    async def run():
        app = MCPanelApp()
        async with app.run_test(size=(160, 46)) as pilot:
            await pilot.pause(2)
            if inst:
                ids = list(app.insts)
                app.query_one("#server-list").index = ids.index(inst.id)
                await pilot.pause(1)
                await pilot.click("#btn-start")
                ok = await asyncio.to_thread(wait_running, inst, 300)
                await pilot.pause(3)
                app.save_screenshot(str(REPORT / "tui-running.svg"))
                check("TUI: Start button launches the server", ok)
                await pilot.click("#tabs Tab#--content-tab-tab-players")
                await pilot.pause(1)
                app.save_screenshot(str(REPORT / "tui-players.svg"))
                await pilot.click("#btn-stop")
                ok = await asyncio.to_thread(wait_stopped, inst, 240)
                check("TUI: Stop button stops + saves the server", ok)
                await pilot.pause(1)
            app.save_screenshot(str(REPORT / "tui-list.svg"))
    asyncio.run(run())


def write_report():
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    lines = [f"# MCPanel cloud test - {passed}/{len(RESULTS)} checks passed",
             f"Runtime: {time.time() - T0:.0f}s on {os.uname().nodename}", "",
             "| | Check | Details |", "|---|---|---|"]
    for name, ok, detail in RESULTS:
        lines.append(f"| {'✅' if ok else '❌'} | {name} | {detail.replace('|', '/')} |")
    (REPORT / "REPORT.md").write_text("\n".join(lines) + "\n")
    for inst in instances.list_instances():
        dst = REPORT / "servers" / inst.id
        dst.mkdir(parents=True, exist_ok=True)
        for f in ("console.log", "runner.log"):
            src = inst.logs_dir / f
            if src.exists():
                txt = src.read_text(errors="replace")
                (dst / f).write_text(txt[-300_000:])
        shutil.copy(inst.file, dst / "instance.json")
        if inst.props_file.exists():
            shutil.copy(inst.props_file, dst / "server.properties")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write((REPORT / "REPORT.md").read_text())
    print((REPORT / "REPORT.md").read_text())
    return passed == len(RESULTS)


def main():
    paths.ensure_dirs()
    env_info()
    fabric = test_fabric()
    test_modrinth()
    test_neoforge()
    test_forge()
    test_daemon(fabric)
    test_tui(fabric)
    for i in instances.list_instances():       # make sure nothing is left running
        if control.is_running(i):
            control.kill(i)
    sys.exit(0 if write_report() else 1)


if __name__ == "__main__":
    main()
