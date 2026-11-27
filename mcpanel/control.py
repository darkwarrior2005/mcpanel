"""Client side: start/stop servers, send commands, stream the live console.

Each running server is supervised by its own background *runner* process
(mcpanel.runner). The runner holds a lock file while alive and exposes a
Unix socket, so the TUI can be closed and reopened at any time without
touching the servers.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import paths

RUNNING_STATES = {"starting", "running", "stopping", "restarting"}
PKG_PARENT = str(Path(__file__).resolve().parent.parent)


def _iid(x) -> str:
    return x if isinstance(x, str) else x.id


def _key(iid: str) -> str:
    return hashlib.sha1(iid.encode()).hexdigest()[:12]


def sock_path(iid: str) -> Path:
    return paths.RUN_DIR / f"{_key(iid)}.sock"


def lock_path(iid: str) -> Path:
    return paths.RUN_DIR / f"{iid}.lock"


def status_path(iid: str) -> Path:
    return paths.RUN_DIR / f"{iid}.status.json"


DAEMON_SOCK = paths.RUN_DIR / "daemon.sock"
DAEMON_LOCK = paths.RUN_DIR / "daemon.lock"


# ------------------------------------------------------------------ locks
def acquire_lock(path: Path, attempts: int = 30):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
    for _ in range(attempts):
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.set_inheritable(fd, False)
            return fd
        except BlockingIOError:
            time.sleep(0.1)
    os.close(fd)
    return None


def is_locked(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    except OSError:
        return False
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def is_running(inst) -> bool:
    return is_locked(lock_path(_iid(inst)))


def read_status(inst) -> dict:
    iid = _iid(inst)
    st = paths.read_json(status_path(iid), {}) or {}
    alive = is_running(iid)
    if not alive:
        if st.get("state") in RUNNING_STATES or not st.get("state"):
            st["state"] = "stopped"
        st["players"] = []
    st["alive"] = alive
    return st


# ------------------------------------------------------------------ requests
def _request(path: Path, obj: dict, timeout: float = 5.0) -> dict:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(path))
        s.sendall((json.dumps(obj) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        line = buf.split(b"\n", 1)[0]
        return json.loads(line) if line else {"ok": False, "error": "no response"}
    finally:
        s.close()


def request(inst, obj: dict, timeout: float = 5.0) -> dict:
    return _request(sock_path(_iid(inst)), obj, timeout)


def send_command(inst, cmd: str) -> None:
    if not is_running(inst):
        raise RuntimeError("Server is not running")
    r = request(inst, {"op": "cmd", "data": cmd})
    if not r.get("ok"):
        raise RuntimeError(r.get("error") or "Server is not accepting commands yet")


def stop(inst) -> None:
    if is_running(inst):
        request(inst, {"op": "stop"})


def restart(inst) -> None:
    if is_running(inst):
        request(inst, {"op": "restart"})


def kill(inst) -> None:
    if is_running(inst):
        request(inst, {"op": "kill"})


# ------------------------------------------------------------------ starting
_children: list = []


def spawn_runner(iid: str) -> subprocess.Popen:
    paths.ensure_dirs()
    logs = paths.SERVERS_DIR / iid / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["MCPANEL_HOME"] = str(paths.HOME)
    env["PYTHONPATH"] = PKG_PARENT + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    with open(logs / "runner.log", "ab") as log:
        p = subprocess.Popen([sys.executable, "-m", "mcpanel.runner", iid],
                             stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                             start_new_session=True, close_fds=True, cwd=str(paths.HOME), env=env)
    _children.append(p)
    return p


def reap() -> None:
    for p in list(_children):
        if p.poll() is not None:
            _children.remove(p)


def daemon_running() -> bool:
    return is_locked(DAEMON_LOCK)


def daemon_request(obj: dict, timeout: float = 5.0) -> dict:
    return _request(DAEMON_SOCK, obj, timeout)


def start(inst, wait: float = 5.0) -> bool:
    """Start a server (through the always-on daemon if it is running)."""
    iid = _iid(inst)
    if is_running(iid):
        return True
    started = False
    if daemon_running():
        try:
            started = bool(daemon_request({"op": "start", "id": iid}).get("ok"))
        except OSError:
            started = False
    if not started:
        spawn_runner(iid)
    deadline = time.time() + wait
    while time.time() < deadline:
        if is_running(iid):
            return True
        time.sleep(0.1)
    return is_running(iid)


# ------------------------------------------------------------------ console
class ConsoleStream:
    """Background reader for a server's live console.

    on_lines(list[str]) is called with batches of lines (from a thread).
    on_close() is called if the server goes away.
    """

    def __init__(self, inst, on_lines, on_close=None, on_status=None):
        self.iid = _iid(inst)
        self.on_lines = on_lines
        self.on_close = on_close
        self.on_status = on_status
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    @property
    def alive(self) -> bool:
        return self.thread.is_alive()

    def close(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.settimeout(5)
            s.connect(str(sock_path(self.iid)))
            s.sendall(b'{"op": "subscribe"}\n')
            s.settimeout(0.15)
            buf = b""
            pending: list = []
            last = time.monotonic()
            while not self._stop.is_set():
                try:
                    data = s.recv(65536)
                except socket.timeout:
                    data = None
                if data == b"":
                    break
                if data:
                    buf += data
                    *lines, buf = buf.split(b"\n")
                    for raw in lines:
                        try:
                            obj = json.loads(raw)
                        except ValueError:
                            continue
                        if obj.get("t") == "line":
                            pending.append(obj.get("d", ""))
                        elif obj.get("t") == "status" and self.on_status:
                            self.on_status(obj.get("d") or {})
                now = time.monotonic()
                if pending and (data is None or now - last > 0.1 or len(pending) > 400):
                    self.on_lines(pending)
                    pending = []
                    last = now
            if pending and not self._stop.is_set():
                self.on_lines(pending)
        except OSError:
            pass
        finally:
            s.close()
            if self.on_close and not self._stop.is_set():
                try:
                    self.on_close()
                except Exception:
                    pass
