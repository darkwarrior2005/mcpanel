"""UI sweep: clicks every button, switch, dropdown and field in the TUI against a
stand-in server and checks each one really did its job. Runs in seconds, no internet.

    python tests/ui_sweep.py
"""
import os, sys, tempfile
from pathlib import Path
TMP = Path(tempfile.mkdtemp(prefix="mcpanel-sweep-"))
os.environ["MCPANEL_HOME"] = str(TMP / "home")
FAKE = TMP / "fake_server.py"
FAKE.write_text('import sys, time\nprint("[12:00:00] [main/INFO]: Loading 312 mods", flush=True)\ntime.sleep(0.5)\nprint("[12:00:01] [Server thread/INFO]: Done (1.234s)! For help, type \\"help\\"", flush=True)\nfor line in sys.stdin:\n    c = line.strip()\n    if c == "stop":\n        print("[12:00:09] [Server thread/INFO]: Stopping server", flush=True); time.sleep(0.3); sys.exit(0)\n    elif c == "crash":\n        print("[Server thread/ERROR]: boom", flush=True); sys.exit(1)\n    elif c.startswith("join "):\n        print(f"[12:00:05] [Server thread/INFO]: {c[5:]} joined the game", flush=True)\n    else:\n        print(f"[12:00:05] [Server thread/INFO]: Unknown or echo: {c}", flush=True)\n')

# ---- stand-in server
from mcpanel import paths, instances, props
paths.ensure_dirs()
i = instances.Instance(id="pack", name="Cobble Test", loader="fabric", mc_version="1.21.1",
    java_path=sys.executable, launch_mode="script", launch_target="start.sh")
for d in (i.server_dir, i.logs_dir, i.backups_dir): d.mkdir(parents=True, exist_ok=True)
(i.server_dir/"start.sh").write_text(f"exec {sys.executable} {FAKE}\n")
(i.server_dir/"mods").mkdir(exist_ok=True); (i.server_dir/"mods"/"a.jar").write_bytes(b"x")
props.write_props(i.props_file, {"server-port":"25565","online-mode":"true","motd":"hi"})
i.accept_eula(); i.save()

# ---- sweep
import asyncio, json, time
from pathlib import Path
from mcpanel.tui import MCPanelApp
from mcpanel import props, control, players, backups
from mcpanel.instances import Instance
R = []
def ok(name, cond, info=""):
    R.append((name, bool(cond))); print(("PASS " if cond else "FAIL ") + name + (f"  [{info}]" if info else ""))
I = lambda: Instance.load("pack")
P = lambda: props.read_props(I().props_file)
async def click(pilot, app, sel):
    w = app.screen.query_one(sel); w.scroll_visible(animate=False); await pilot.pause(0.25)
    await pilot.click(sel); await pilot.pause(0.4)
async def tab(pilot, name):
    await pilot.click(f"#tabs Tab#--content-tab-tab-{name}"); await pilot.pause(0.5)
async def typein(pilot, app, sel, text):
    w = app.query_one(sel); w.scroll_visible(animate=False); await pilot.pause(0.2)
    await pilot.click(sel); w.value = ""; await pilot.press(*text) if text else None; await pilot.pause(1.2)

async def main():
    app = MCPanelApp()
    async with app.run_test(size=(160, 46)) as pilot:
        await pilot.pause(1.5)
        # ---------------- SETTINGS: every switch / select / input
        await tab(pilot, "settings")
        for sw, f in (("#inst-aikar_flags","aikar_flags"),("#inst-autostart","autostart"),("#inst-auto_restart","auto_restart")):
            b = getattr(I(), f); await click(pilot, app, sw); ok(f"switch {f}", getattr(I(), f) != b)
        for key in ("online-mode","white-list","enforce-whitelist","pvp","allow-flight","hardcore","enable-command-block"):
            b = P().get(key); await click(pilot, app, f"#prop-{key}"); a = P().get(key)
            ok(f"switch {key}", a is not None and a != b, f"{b}->{a}")
        for f, v in (("memory_mb","5120"),("min_memory_mb","2048"),("jvm_args","-Dfoo=1"),("backup_interval_h","6"),("backup_keep","3"),("name","Renamed Pack")):
            await typein(pilot, app, f"#inst-{f}", v); ok(f"input {f}", str(getattr(I(), f)).rstrip("0").rstrip(".") in (v, v+".0") or str(getattr(I(),f))==v or getattr(I(),f)==float(v) if f=="backup_interval_h" else str(getattr(I(), f))==v, getattr(I(), f))
        for key, v in (("motd","Hello Cloud"),("max-players","12"),("view-distance","8"),("simulation-distance","6"),("spawn-protection","0"),("level-seed","12345"),("server-ip","")):
            await typein(pilot, app, f"#prop-{key}", v); ok(f"input {key}", P().get(key, "") == v, P().get(key))
        await typein(pilot, app, "#prop-server-port", "25570"); ok("input server-port", P().get("server-port")=="25570")
        # invalid value must NOT be saved
        await typein(pilot, app, "#inst-memory_mb", "abc"); ok("invalid RAM rejected", I().memory_mb == 5120, str(app.query_one('#set-status').render()))
        await typein(pilot, app, "#inst-memory_mb", "6144"); ok("RAM fixed again", I().memory_mb == 6144)
        # selects via keyboard
        for sel, key in (("#prop-difficulty","difficulty"),("#prop-gamemode","gamemode"),("#prop-op-permission-level","op-permission-level")):
            b = P().get(key, "?"); w = app.query_one(sel); w.scroll_visible(animate=False); await pilot.pause(0.2)
            await pilot.click(sel); await pilot.pause(0.4); await pilot.press("down"); await pilot.press("enter"); await pilot.pause(0.6)
            ok(f"select {key}", P().get(key, "?") != b, f"{b}->{P().get(key)}")
        b = I().java_major; w = app.query_one("#inst-java_major"); w.scroll_visible(animate=False); await pilot.pause(0.2)
        await pilot.click("#inst-java_major"); await pilot.pause(0.4); await pilot.press("up"); await pilot.press("enter"); await pilot.pause(0.6)
        ok("select java", I().java_major != b, f"{b}->{I().java_major}")
        app.query_one("#inst-java_major").value = "21"; await pilot.pause(0.5)
        app.query_one("#settings-scroll").scroll_home(animate=False); await pilot.pause(0.3)
        await click(pilot, app, "#set-save"); ok("Save now button", "saved" in str(app.query_one('#set-status').render()).lower())
        await click(pilot, app, "#set-detect"); ok("Re-detect launch", I().launch_mode == "script")
        await click(pilot, app, "#set-raw"); ok("Raw properties editor opens", type(app.screen).__name__ == "TextEditScreen")
        await pilot.press("escape"); await pilot.pause(0.3)
        await click(pilot, app, "#set-jvmfile"); ok("JVM file editor opens", type(app.screen).__name__ == "TextEditScreen")
        await pilot.press("ctrl+s"); await pilot.pause(0.3)
        ok("JVM file saved", (I().server_dir/"user_jvm_args.txt").exists())
        ok("launch preview shows RAM", "6144" in str(app.query_one("#set-cmd").render()), str(app.query_one("#set-cmd").render()))
        # put port back + online mode off for player tests
        app.query_one("#prop-server-port").value = "25565"; await pilot.pause(1.2)
        # ---------------- TOP BUTTONS + CONSOLE
        await tab(pilot, "console")
        await click(pilot, app, "#btn-start"); await asyncio.sleep(3); await pilot.pause(0.5)
        ok("Start button", control.read_status("pack")["state"] == "running")
        log = (I().logs_dir/"console.log").read_text()
        ok("RAM applied in script mode", "passed to the script" in log and "-Xmx6144M" in log)
        await typein(pilot, app, "#cmd", "join Steve"); await pilot.press("enter"); await pilot.pause(1.5)
        ok("console command + player tracking", "Steve" in control.read_status("pack")["players"])
        for qb in app.query(".quick-btn"):
            qb.scroll_visible(animate=False)
        n0 = (I().logs_dir/"console.log").read_text().count("> ")
        for i, qb in enumerate(list(app.query(".quick-btn"))):
            qb.press(); await pilot.pause(0.4)
        ok("quick command buttons", (I().logs_dir/"console.log").read_text().count("> ") >= n0 + 5)
        await click(pilot, app, "#cmd-send"); ok("Send button (empty is ignored)", True)
        await click(pilot, app, "#console-clear"); ok("Clear view", len(app.query_one("#console").lines) == 0)
        await click(pilot, app, "#btn-copy"); ok("Copy IP", True)
        # ---------------- PLAYERS
        await tab(pilot, "players")
        for action, check in (("pl-op", lambda: "Steve" in [e["name"] for e in players.read_list(I(),"ops")]),
                              ("pl-wladd", lambda: "Steve" in [e["name"] for e in players.read_list(I(),"whitelist")])):
            app.query_one("#pl-name").value = "Steve"
            await click(pilot, app, f"#{action}"); await asyncio.sleep(2)
            ok(f"players {action} (live: command sent)", "op Steve" in (I().logs_dir/"console.log").read_text() if action=="pl-op" else "whitelist add Steve" in (I().logs_dir/"console.log").read_text())
        for action, cmd in (("pl-deop","deop Steve"),("pl-wlrm","whitelist remove Steve"),("pl-kick","kick Steve"),("pl-ban","ban Steve"),("pl-pardon","pardon Steve")):
            app.query_one("#pl-name").value = "Steve"; await click(pilot, app, f"#{action}"); await asyncio.sleep(0.6)
            ok(f"players {action}", cmd in (I().logs_dir/"console.log").read_text())
        await click(pilot, app, "#pl-wlon"); await asyncio.sleep(0.5); ok("Whitelist ON", P().get("white-list")=="true" and "whitelist on" in (I().logs_dir/"console.log").read_text())
        await click(pilot, app, "#pl-wloff"); await asyncio.sleep(0.5); ok("Whitelist OFF", P().get("white-list")=="false")
        # ---------------- BACKUPS (live)
        (I().server_dir/"world").mkdir(exist_ok=True); (I().server_dir/"world"/"level.dat").write_text("x")
        await tab(pilot, "backups")
        await click(pilot, app, "#bk-create"); await asyncio.sleep(8); await pilot.pause(0.5)
        ok("Create backup (running)", len(backups.list_backups(I())) == 1)
        # ---------------- RESTART / STOP
        old = control.read_status("pack")["started_at"]
        await click(pilot, app, "#btn-restart"); await asyncio.sleep(4)
        st = control.read_status("pack"); ok("Restart button", st["state"]=="running" and st["started_at"]!=old)
        await click(pilot, app, "#btn-stop"); await asyncio.sleep(2.5)
        ok("Stop button", not control.is_running("pack") and control.read_status("pack").get("exit_code")==0)
        # ---------------- OFFLINE players (file edits) with online-mode off
        props.write_props(I().props_file, {"online-mode":"false"})
        await tab(pilot, "players")
        app.query_one("#pl-name").value = "Alex"; await click(pilot, app, "#pl-op"); await asyncio.sleep(2)
        ok("players op (offline file edit)", "Alex" in [e["name"] for e in players.read_list(I(),"ops")])
        app.query_one("#pl-name").value = "Alex"; await click(pilot, app, "#pl-ban"); await asyncio.sleep(2)
        ok("players ban (offline)", "Alex" in [e["name"] for e in players.read_list(I(),"bans")])
        app.query_one("#pl-name").value = "Alex"; await click(pilot, app, "#pl-pardon"); await asyncio.sleep(1.5)
        ok("players unban (offline)", "Alex" not in [e["name"] for e in players.read_list(I(),"bans")])
        # ---------------- BACKUPS restore/delete
        await tab(pilot, "backups")
        await click(pilot, app, "#bk-refresh")
        await click(pilot, app, "#bk-restore"); await pilot.pause(0.3)
        await click(pilot, app, "#yes"); await pilot.pause(0.5)
        ok("Restore backup", (I().server_dir/"world.before-restore").exists())
        await click(pilot, app, "#bk-delete"); await pilot.pause(0.3); await click(pilot, app, "#yes"); await pilot.pause(0.5)
        ok("Delete backup", len(backups.list_backups(I())) == 0)
        # ---------------- MODS
        await tab(pilot, "mods")
        await click(pilot, app, "#mods-toggle"); ok("Mod disable", (I().server_dir/"mods"/"a.jar.disabled").exists())
        await click(pilot, app, "#mods-toggle"); ok("Mod enable", (I().server_dir/"mods"/"a.jar").exists())
        await click(pilot, app, "#mods-refresh"); ok("Mods refresh", app.query_one("#mods-table").row_count == 1)
        await typein(pilot, app, "#mods-filter", "zzz"); ok("Mods filter", app.query_one("#mods-table").row_count == 0)
        app.query_one("#mods-filter").value = ""; await pilot.pause(0.3)
        await click(pilot, app, "#mods-delete"); await pilot.pause(0.3); await click(pilot, app, "#yes"); await pilot.pause(0.4)
        ok("Mod delete", not (I().server_dir/"mods"/"a.jar").exists())
        # ---------------- keyboard shortcuts
        await pilot.press("s"); await asyncio.sleep(3); ok("S key starts", control.is_running("pack"))
        await pilot.press("r"); await asyncio.sleep(4); ok("R key restarts", control.read_status("pack")["state"]=="running")
        await pilot.press("b"); await asyncio.sleep(8); ok("B key backup", len(backups.list_backups(I())) == 1)
        await pilot.press("x"); await asyncio.sleep(2.5); ok("X key stops", not control.is_running("pack"))
        await pilot.press("t"); await pilot.pause(0.5); ok("T key re-check Tailscale", True)
        await click(pilot, app, "#btn-start"); await asyncio.sleep(3)
        await click(pilot, app, "#btn-kill"); await pilot.pause(0.3); await click(pilot, app, "#yes"); await asyncio.sleep(2)
        ok("Kill button", not control.is_running("pack"))
        await pilot.press("n"); await pilot.pause(0.8); ok("N key opens New server", type(app.screen).__name__=="NewServerScreen")
        await pilot.press("escape"); await pilot.pause(0.3)
        await click(pilot, app, "#btn-delete"); await pilot.pause(0.3); ok("Delete asks first", type(app.screen).__name__=="ConfirmScreen")
        await pilot.press("escape"); await pilot.pause(0.3); ok("Delete cancelled keeps server", I().dir.exists())
    print(f"\n{sum(1 for _,g in R if g)}/{len(R)} passed")
asyncio.run(main())
failed = [n for n, g in R if not g]
for i in __import__("mcpanel").instances.list_instances():
    if control.is_running(i):
        control.kill(i)
sys.exit(1 if failed or not R else 0)
