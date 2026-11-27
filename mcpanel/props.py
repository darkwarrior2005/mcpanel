"""Read/write Java .properties files (server.properties) while keeping comments."""
from __future__ import annotations

from pathlib import Path

from . import paths


def _unescape(s: str) -> str:
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            n = s[i + 1]
            if n == "u" and i + 6 <= len(s):
                try:
                    out.append(chr(int(s[i + 2:i + 6], 16)))
                    i += 6
                    continue
                except ValueError:
                    pass
            out.append({"n": "\n", "t": "\t", "r": "\r", "f": "\f"}.get(n, n))
            i += 2
            continue
        out.append(c)
        i += 1
    # join UTF-16 surrogate pairs produced by \uXXXX escapes
    return "".join(out).encode("utf-16", "surrogatepass").decode("utf-16", "replace")


def _escape(v: str, is_key: bool = False) -> str:
    out = []
    for ch in v:
        if ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif is_key and ch in " =:#!":
            out.append("\\" + ch)
        elif ord(ch) > 126 or ord(ch) < 32:
            data = ch.encode("utf-16-be")
            for j in range(0, len(data), 2):
                out.append("\\u%04x" % int.from_bytes(data[j:j + 2], "big"))
        else:
            out.append(ch)
    return "".join(out)


def _split(line: str):
    i = 0
    while i < len(line):
        c = line[i]
        if c == "\\":
            i += 2
            continue
        if c in "=:":
            return line[:i].strip(), line[i + 1:].lstrip()
        i += 1
    return line.strip(), ""


def read_props(path) -> dict:
    result: dict = {}
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return result
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line[0] in "#!":
            continue
        k, v = _split(line)
        result[_unescape(k)] = _unescape(v)
    return result


def write_props(path, updates: dict, remove=()) -> None:
    path = Path(path)
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = ["#Minecraft server properties"]
    pending = {k: ("" if v is None else str(v)) for k, v in updates.items()}
    remove = set(remove)
    out = []
    for raw in lines:
        line = raw.strip()
        if line and line[0] not in "#!":
            k = _unescape(_split(line)[0])
            if k in remove:
                continue
            if k in pending:
                out.append(f"{_escape(k, True)}={_escape(pending.pop(k))}")
                continue
        out.append(raw)
    for k, v in pending.items():
        out.append(f"{_escape(k, True)}={_escape(v)}")
    paths.atomic_write_text(path, "\n".join(out) + "\n")
