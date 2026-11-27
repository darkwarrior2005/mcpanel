"""Command line entry point. `mcpanel` with no arguments opens the TUI."""
from __future__ import annotations

import argparse
import sys
import threading
import time

from . import __version__, backups, control, paths
from .instances import find_instance, list_instances


def _get(name: str):
    inst = find_instance(name)
    if not inst:
        sys.exit(f"No server matching '{name}'. Try: mcpanel list")
    return inst


def cmd_list() -> None:
    insts = list_instances()
    if not insts:
        print("No servers yet. Run `mcpanel` to create one.")
        return
    print(f"{'ID':28} {'NAME':28} {'TYPE':22} {'PORT':6} {'STATE':10} PLAYERS")
    for i in insts:
        st = control.read_status(i)
        print(f"{i.id:28} {i.name[:28]:28} {i.describe()[:22]:22} {i.port:<6} "
              f"{st.get('state', '?'):10} {', '.join(st.get('players') or [])}")


def cmd_console(inst) -> None:
    if not control.is_running(inst):
        sys.exit(f"{inst.name} is not running")
    closed = threading.Event()
    s = control.ConsoleStream(inst, lambda lines: print("\n".join(lines), flush=True),
                              on_close=closed.set)
    print(f"--- attached to {inst.name}. Type commands, Ctrl+D or Ctrl+C to detach "
          f"(server keeps running) ---")
    try:
        for line in sys.stdin:
            if closed.is_set():
                break
            if line.strip():
                try:
                    control.send_command(inst, line.strip())
                except Exception as e:
                    print(f"! {e}")
    except KeyboardInterrupt:
        pass
    s.close()


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="mcpanel", description="Minecraft modpack server manager")
    ap.add_argument("--version", action="version", version=f"mcpanel {__version__}")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("tui", help="open the TUI (default)")
    sub.add_parser("daemon", help="run the always-on background service (used by systemd)")
    sub.add_parser("list", help="list servers and their state")
    for c, h in (("start", "start a server"), ("stop", "stop a server gracefully"),
                 ("restart", "restart a server"), ("kill", "force-kill a server"),
                 ("backup", "back up a server's world"),
                 ("console", "attach to a server's live console")):
        p = sub.add_parser(c, help=h)
        p.add_argument("server", help="server id or name")
    p = sub.add_parser("cmd", help="send a console command, e.g. mcpanel cmd mypack say hi")
    p.add_argument("server")
    p.add_argument("command", nargs="+")
    a = ap.parse_args(argv)
    paths.ensure_dirs()

    if a.cmd in (None, "tui"):
        from .tui import run
        run()
    elif a.cmd == "daemon":
        from .daemon import main as dmain
        dmain()
    elif a.cmd == "list":
        cmd_list()
    elif a.cmd == "start":
        inst = _get(a.server)
        if not inst.eula_accepted():
            sys.exit("EULA not accepted for this server - start it once from the TUI.")
        print("started" if control.start(inst) else "failed to start (see logs/runner.log)")
    elif a.cmd == "stop":
        inst = _get(a.server)
        control.stop(inst)
        print("stopping (saving world)...")
        for _ in range(120):
            if not control.is_running(inst):
                print("stopped")
                return
            time.sleep(1)
    elif a.cmd == "restart":
        control.restart(_get(a.server))
        print("restarting")
    elif a.cmd == "kill":
        control.kill(_get(a.server))
        print("killed")
    elif a.cmd == "backup":
        backups.create_backup(_get(a.server), "world", log=print)
    elif a.cmd == "console":
        cmd_console(_get(a.server))
    elif a.cmd == "cmd":
        control.send_command(_get(a.server), " ".join(a.command))
        print("sent")


if __name__ == "__main__":
    main()
