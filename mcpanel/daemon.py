"""Always-on background service (run by systemd: `mcpanel daemon`).

* starts every server marked "autostart" when the machine boots
* starts servers on request from the TUI, so they live inside the service
  (they keep running after you log out of SSH)
* runs scheduled backups
* on shutdown/reboot, stops all servers gracefully (worlds are saved)
"""
from __future__ import annotations

import json
import os
import signal
import socket
import sys
import threading
import time

from . import backups, control, paths
from .instances import Instance, list_instances


def log(msg: str) -> None:
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


class Daemon:
    def __init__(self):
        self.stopping = threading.Event()
        self.backing_up: set = set()
        self.started = time.time()

    def serve(self) -> None:
        try:
            control.DAEMON_SOCK.unlink()
        except FileNotFoundError:
            pass
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(str(control.DAEMON_SOCK))
        os.chmod(control.DAEMON_SOCK, 0o600)
        s.listen(8)
        self.sock = s
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn) -> None:
        conn.settimeout(10)
        try:
            buf = b""
            while b"\n" not in buf:
                d = conn.recv(4096)
                if not d:
                    return
                buf += d
            msg = json.loads(buf.split(b"\n", 1)[0])
            op = msg.get("op")
            if op == "ping":
                resp = {"ok": True}
            elif op == "start" and not self.stopping.is_set():
                iid = str(msg.get("id", ""))
                Instance.load(iid)  # validates
                if not control.is_running(iid):
                    log(f"starting {iid} (requested)")
                    control.spawn_runner(iid)
                resp = {"ok": True}
            else:
                resp = {"ok": False, "error": "unsupported"}
        except Exception as e:
            resp = {"ok": False, "error": str(e)}
        try:
            conn.sendall((json.dumps(resp) + "\n").encode())
        except OSError:
            pass
        finally:
            conn.close()

    def autostart(self) -> None:
        for inst in list_instances():
            if inst.autostart and not control.is_running(inst):
                log(f"autostart: {inst.name} ({inst.id})")
                control.spawn_runner(inst.id)
                time.sleep(2)  # stagger heavy modpack startups a little

    def scheduled_backups(self) -> None:
        now = time.time()
        for inst in list_instances():
            if inst.backup_interval_h <= 0 or inst.id in self.backing_up:
                continue
            if not control.is_running(inst):
                continue
            last = max(backups.latest_backup_time(inst), self.started)
            if now - last >= inst.backup_interval_h * 3600:
                self.backing_up.add(inst.id)
                threading.Thread(target=self._backup, args=(inst,), daemon=True).start()

    def _backup(self, inst) -> None:
        try:
            log(f"scheduled backup: {inst.name}")
            try:
                control.send_command(inst, "say [MCPanel] Automatic backup in progress...")
            except Exception:
                pass
            backups.create_backup(inst, "world", log=lambda m: log(f"  {m}"))
        except Exception as e:
            log(f"backup of {inst.name} failed: {e}")
        finally:
            self.backing_up.discard(inst.id)

    def shutdown_all(self) -> None:
        running = [i for i in list_instances() if control.is_running(i)]
        if not running:
            return
        log(f"stopping {len(running)} server(s) gracefully...")
        for i in running:
            try:
                control.stop(i)
            except Exception as e:
                log(f"stop {i.id}: {e}")
        cfg = paths.load_config()
        deadline = time.time() + int(cfg.get("stop_timeout_s", 90)) + 40
        while time.time() < deadline and any(control.is_running(i) for i in running):
            control.reap()
            time.sleep(1)
        log("all servers stopped")

    def run(self) -> int:
        paths.ensure_dirs()
        lock = control.acquire_lock(control.DAEMON_LOCK, attempts=20)
        if lock is None:
            log("another mcpanel daemon is already running")
            return 1
        signal.signal(signal.SIGTERM, lambda *a: self.stopping.set())
        signal.signal(signal.SIGINT, lambda *a: self.stopping.set())
        self.serve()
        log(f"mcpanel daemon started (home: {paths.HOME})")
        self.autostart()
        while not self.stopping.is_set():
            try:
                control.reap()
                self.scheduled_backups()
            except Exception as e:
                log(f"loop error: {e}")
            self.stopping.wait(30)
        log("shutting down")
        self.shutdown_all()
        try:
            self.sock.close()
            control.DAEMON_SOCK.unlink()
        except OSError:
            pass
        os.close(lock)
        return 0


def main() -> None:
    sys.exit(Daemon().run())


if __name__ == "__main__":
    main()
