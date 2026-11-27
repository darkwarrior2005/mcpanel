"""Per-server supervisor process.

Started detached (by the TUI or the daemon) as:  python -m mcpanel.runner <server-id>

* launches the Minecraft server with the right Java/memory/flags
* keeps console output in a backlog + logs/console.log
* serves a Unix socket: live console, commands, stop/restart/kill
* tracks online players and state (starting/running/stopping/...)
* graceful stop ("stop" command, then SIGTERM, then SIGKILL)
* auto-restarts the server after a crash (with limits)
"""
from __future__ import annotations

import collections
import json
import os
import re
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import control, javamgr, loaders, paths
from .instances import Instance

AIKAR_FLAGS = (
    "-XX:+IgnoreUnrecognizedVMOptions -XX:+UseG1GC -XX:+ParallelRefProcEnabled -XX:MaxGCPauseMillis=200 "
    "-XX:+UnlockExperimentalVMOptions -XX:+DisableExplicitGC -XX:+AlwaysPreTouch "
    "-XX:G1NewSizePercent=30 -XX:G1MaxNewSizePercent=40 -XX:G1HeapRegionSize=8M "
    "-XX:G1ReservePercent=20 -XX:G1HeapWastePercent=5 -XX:G1MixedGCCountTarget=4 "
    "-XX:InitiatingHeapOccupancyPercent=15 -XX:G1MixedGCLiveThresholdPercent=90 "
    "-XX:G1RSetUpdatingPauseTimePercent=5 -XX:SurvivorRatio=32 "
    "-XX:+PerfDisableSharedMem -XX:MaxTenuringThreshold=1"
).split()

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
RE_DONE = re.compile(r"Done \([\d.,]+\s*s\)!")
RE_JOIN = re.compile(r"\]: ([A-Za-z0-9_.\-]{1,32}) joined the game\s*$")
RE_LEFT = re.compile(r"\]: ([A-Za-z0-9_.\-]{1,32}) left the game\s*$")
RE_STOPPING = re.compile(r"Stopping (the )?server")


def build_command(inst: Instance, java: str):
    sd = inst.server_dir
    env = os.environ.copy()
    jbin = Path(java).parent
    env["JAVA_HOME"] = str(jbin.parent)
    env["PATH"] = str(jbin) + os.pathsep + env.get("PATH", "")
    xmx = max(256, int(inst.memory_mb))
    xms = max(128, min(int(inst.min_memory_mb or xmx), xmx))
    mem = [f"-Xms{xms}M", f"-Xmx{xmx}M"]
    flags = AIKAR_FLAGS if inst.aikar_flags else []
    extra = shlex.split(inst.jvm_args or "")
    mode, target = inst.launch_mode, inst.launch_target
    if not mode or not target:
        mode, target = loaders.detect_launch(sd)
        if mode:
            inst.launch_mode, inst.launch_target = mode, target
            inst.save()
    if not mode:
        raise RuntimeError("Don't know how to start this server. Set 'Launch mode' and "
                           "'Launch target' in the Settings tab.")
    if not (sd / target).exists():
        raise RuntimeError(f"Launch target not found: {target}")
    if mode == "argsfile":
        argv = [java, *mem, *flags, *extra, "@" + target, "nogui"]
    elif mode == "jar":
        argv = [java, *mem, *flags, *extra, "-jar", target, "nogui"]
    elif mode == "script":
        argv = ["bash", target]
    else:
        raise RuntimeError(f"Unknown launch mode {mode}")
    return argv, env


class Runner:
    def __init__(self, inst: Instance):
        self.inst = inst
        self.cfg = paths.load_config()
        self.backlog: collections.deque = collections.deque(maxlen=1500)
        self.clients: list = []
        self.clients_lock = threading.Lock()
        self.stdin_lock = threading.Lock()
        self.proc = None
        self.state = "starting"
        self.players: set = set()
        self.stop_requested = False
        self.restart_requested = False
        self.wake = threading.Event()
        self.restart_times: list = []
        self.started_at = None
        self.exit_code = None
        self.server_sock = None
        inst.logs_dir.mkdir(parents=True, exist_ok=True)
        log = inst.logs_dir / "console.log"
        try:
            if log.exists() and log.stat().st_size > 10 * 1024 * 1024:
                os.replace(log, inst.logs_dir / "console.log.1")
        except OSError:
            pass
        self.logf = open(log, "a", encoding="utf-8", buffering=1)

    # ------------------------------------------------------------ state
    def status(self) -> dict:
        p = self.proc
        return {"state": self.state, "runner_pid": os.getpid(),
                "server_pid": p.pid if p and p.poll() is None else None,
                "started_at": self.started_at, "players": sorted(self.players),
                "exit_code": self.exit_code, "restarts": len(self.restart_times),
                "updated": time.time()}

    def write_status(self) -> None:
        st = self.status()
        try:
            paths.atomic_write_json(control.status_path(self.inst.id), st)
        except OSError:
            pass
        self.broadcast({"t": "status", "d": st})

    def set_state(self, s: str) -> None:
        if s != self.state:
            self.state = s
            self.write_status()

    # ------------------------------------------------------------ output
    def broadcast(self, obj: dict) -> None:
        data = (json.dumps(obj) + "\n").encode()
        with self.clients_lock:
            for c in list(self.clients):
                try:
                    c.sendall(data)
                except OSError:
                    self.clients.remove(c)
                    try:
                        c.close()
                    except OSError:
                        pass

    def emit(self, line: str, panel: bool = False) -> None:
        if panel:
            line = f"[mcpanel] {line}"
        self.backlog.append(line)
        try:
            self.logf.write(line + "\n")
        except (OSError, ValueError):
            pass
        self.broadcast({"t": "line", "d": line})
        if not panel:
            self.parse(line)

    def parse(self, line: str) -> None:
        plain = ANSI.sub("", line)
        if self.state in ("starting", "restarting") and RE_DONE.search(plain):
            self.set_state("running")
            return
        m = RE_JOIN.search(plain)
        if m:
            self.players.add(m.group(1))
            self.write_status()
            return
        m = RE_LEFT.search(plain)
        if m:
            self.players.discard(m.group(1))
            self.write_status()
            return
        if self.state == "running" and RE_STOPPING.search(plain):
            self.set_state("stopping")

    def pump(self, p: subprocess.Popen) -> None:
        assert p.stdout is not None
        for raw in iter(p.stdout.readline, b""):
            self.emit(raw.decode("utf-8", "replace").rstrip("\r\n"))

    # ------------------------------------------------------------ control
    def send_stdin(self, text: str, echo: bool = True) -> bool:
        p = self.proc
        if not p or p.poll() is not None or not p.stdin:
            return False
        text = text.replace("\r", "").replace("\n", " ").strip()
        if text.startswith("/"):
            text = text[1:]
        if not text:
            return False
        with self.stdin_lock:
            try:
                p.stdin.write((text + "\n").encode("utf-8"))
                p.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                return False
        if echo:
            self.emit(f"> {text}", panel=False)
        return True

    def _signal(self, p: subprocess.Popen, sig) -> None:
        try:
            os.killpg(p.pid, sig)
        except OSError:
            try:
                p.send_signal(sig)
            except OSError:
                pass

    def _stop_watchdog(self, p: subprocess.Popen) -> None:
        timeout = int(self.cfg.get("stop_timeout_s", 90))
        try:
            p.wait(timeout)
            return
        except subprocess.TimeoutExpired:
            self.emit(f"Server did not stop within {timeout}s, sending SIGTERM", panel=True)
            self._signal(p, signal.SIGTERM)
        try:
            p.wait(20)
        except subprocess.TimeoutExpired:
            self.emit("Still alive, killing it", panel=True)
            self._signal(p, signal.SIGKILL)

    def request_stop(self, restart: bool = False) -> None:
        if restart:
            self.restart_requested = True
        else:
            self.stop_requested = True
        self.wake.set()
        p = self.proc
        if p and p.poll() is None:
            self.set_state("stopping")
            self.emit("Restart requested - stopping server..." if restart
                      else "Stop requested - saving and stopping server...", panel=True)
            if not self.send_stdin("stop", echo=False):
                self._signal(p, signal.SIGTERM)
            threading.Thread(target=self._stop_watchdog, args=(p,), daemon=True).start()

    def kill(self) -> None:
        self.stop_requested = True
        self.wake.set()
        p = self.proc
        if p and p.poll() is None:
            self.emit("KILL requested", panel=True)
            self._signal(p, signal.SIGKILL)

    # ------------------------------------------------------------ socket
    def setup_socket(self) -> None:
        sp = control.sock_path(self.inst.id)
        try:
            sp.unlink()
        except FileNotFoundError:
            pass
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(str(sp))
        os.chmod(sp, 0o600)
        s.listen(16)
        self.server_sock = s
        threading.Thread(target=self.accept_loop, daemon=True).start()

    def accept_loop(self) -> None:
        while True:
            try:
                conn, _ = self.server_sock.accept()
            except OSError:
                return
            conn.settimeout(10)
            threading.Thread(target=self.handle_client, args=(conn,), daemon=True).start()

    def reply(self, conn, obj: dict) -> None:
        with self.clients_lock:
            try:
                conn.sendall((json.dumps(obj) + "\n").encode())
            except OSError:
                pass

    def handle_client(self, conn) -> None:
        buf = b""
        try:
            while True:
                try:
                    data = conn.recv(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not data:
                    break
                buf += data
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    self.handle_msg(conn, raw)
        finally:
            with self.clients_lock:
                if conn in self.clients:
                    self.clients.remove(conn)
            try:
                conn.close()
            except OSError:
                pass

    def handle_msg(self, conn, raw: bytes) -> None:
        try:
            msg = json.loads(raw)
        except ValueError:
            self.reply(conn, {"ok": False, "error": "bad json"})
            return
        op = msg.get("op")
        if op == "cmd":
            ok = self.send_stdin(str(msg.get("data", "")))
            self.reply(conn, {"ok": ok} if ok else {"ok": False, "error": "server not ready"})
        elif op == "subscribe":
            with self.clients_lock:
                blob = "".join(json.dumps({"t": "line", "d": l}) + "\n" for l in list(self.backlog))
                blob += json.dumps({"t": "status", "d": self.status()}) + "\n"
                try:
                    conn.sendall(blob.encode())
                    self.clients.append(conn)
                except OSError:
                    pass
        elif op == "status":
            self.reply(conn, {"ok": True, "status": self.status()})
        elif op == "stop":
            self.request_stop()
            self.reply(conn, {"ok": True})
        elif op == "restart":
            self.request_stop(restart=True)
            self.reply(conn, {"ok": True})
        elif op == "kill":
            self.kill()
            self.reply(conn, {"ok": True})
        else:
            self.reply(conn, {"ok": False, "error": f"unknown op {op}"})

    # ------------------------------------------------------------ main loop
    def start_server(self) -> None:
        java = javamgr.ensure_java_for(self.inst, log=lambda m: self.emit(m, panel=True))
        argv, env = build_command(self.inst, java)
        self.emit("Launching: " + " ".join(shlex.quote(a) for a in argv), panel=True)
        self.proc = subprocess.Popen(argv, cwd=str(self.inst.server_dir), stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                                     start_new_session=True, close_fds=True)
        self.started_at = time.time()
        self.exit_code = None
        self.write_status()
        self.reader = threading.Thread(target=self.pump, args=(self.proc,), daemon=True)
        self.reader.start()

    def run(self) -> int:
        lock_fd = control.acquire_lock(control.lock_path(self.inst.id))
        if lock_fd is None:
            print(f"{self.inst.id} is already running", file=sys.stderr)
            return 1
        try:
            self.setup_socket()
            signal.signal(signal.SIGTERM, lambda *a: self.request_stop())
            signal.signal(signal.SIGINT, lambda *a: self.request_stop())
            signal.signal(signal.SIGHUP, signal.SIG_IGN)
            self.write_status()
            while not self.stop_requested:
                self.restart_requested = False
                try:
                    self.inst = Instance.load(self.inst.id)   # pick up settings changes
                except Exception:
                    pass
                self.set_state("starting")
                try:
                    self.start_server()
                except Exception as e:
                    self.emit(f"Failed to start: {e}", panel=True)
                    self.set_state("crashed")
                    break
                rc = self.proc.wait()
                self.reader.join(timeout=5)
                self.exit_code = rc
                self.players.clear()
                self.proc = None
                self.emit(f"Server process exited (code {rc})", panel=True)
                if self.restart_requested and not self.stop_requested:
                    self.emit("Restarting...", panel=True)
                    continue
                if self.stop_requested or rc == 0:
                    self.set_state("stopped")
                    break
                if not self.inst.auto_restart:
                    self.set_state("crashed")
                    break
                now = time.time()
                window = float(self.cfg.get("restart_window_s", 600))
                self.restart_times = [t for t in self.restart_times if now - t < window]
                if len(self.restart_times) >= int(self.cfg.get("max_restarts", 3)):
                    self.emit("Crashed too many times in a row - giving up. Check the log above.",
                              panel=True)
                    self.set_state("crashed")
                    break
                self.restart_times.append(now)
                delay = float(self.cfg.get("restart_delay_s", 10))
                self.emit(f"Server crashed! Auto-restarting in {delay:.0f}s "
                          f"(attempt {len(self.restart_times)})", panel=True)
                self.set_state("restarting")
                self.wake.clear()
                self.wake.wait(delay)
            if self.state not in ("stopped", "crashed"):
                self.set_state("stopped")
        finally:
            self.write_status()
            try:
                self.server_sock.close()
            except Exception:
                pass
            try:
                control.sock_path(self.inst.id).unlink()
            except OSError:
                pass
            with self.clients_lock:
                for c in self.clients:
                    try:
                        c.close()
                    except OSError:
                        pass
            self.logf.close()
            os.close(lock_fd)
        return 0


def main(argv=None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("usage: python -m mcpanel.runner <server-id>", file=sys.stderr)
        sys.exit(2)
    paths.ensure_dirs()
    inst = Instance.load(argv[0])
    sys.exit(Runner(inst).run())


if __name__ == "__main__":
    main()
