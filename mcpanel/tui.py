"""The MCPanel terminal UI (mouse + keyboard), built with Textual."""
from __future__ import annotations

import os
import time
import traceback
from pathlib import Path

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import (Button, Checkbox, DataTable, Footer, Header, Input, Label,
                             ListItem, ListView, ProgressBar, RichLog, Select, Static, Switch,
                             TabbedContent, TabPane, TextArea)

from . import (backups, control, curseforge, instances, javamgr, loaders, mcversions, modrinth,
               paths, players, props, provision, tailscale)
from .instances import Instance

STATE_STYLE = {
    "running": ("●", "green", "Running"),
    "starting": ("◐", "yellow", "Starting"),
    "stopping": ("◑", "yellow", "Stopping"),
    "restarting": ("◐", "magenta", "Restarting"),
    "stopped": ("○", "grey50", "Stopped"),
    "crashed": ("✖", "red", "Crashed"),
}

PROP_FIELDS = [
    ("motd", "MOTD (server list text)", "str", None),
    ("server-port", "Port", "int", None),
    ("max-players", "Max players", "int", None),
    ("difficulty", "Difficulty", "select", ["peaceful", "easy", "normal", "hard"]),
    ("gamemode", "Game mode", "select", ["survival", "creative", "adventure", "spectator"]),
    ("level-name", "World folder", "str", None),
    ("level-seed", "World seed", "str", None),
    ("view-distance", "View distance", "int", None),
    ("simulation-distance", "Simulation distance", "int", None),
    ("spawn-protection", "Spawn protection radius", "int", None),
    ("op-permission-level", "Default op level", "select", ["1", "2", "3", "4"]),
    ("server-ip", "Bind IP (blank = all interfaces)", "str", None),
    ("online-mode", "Online mode (official accounts)", "bool", None),
    ("white-list", "Whitelist enabled", "bool", None),
    ("enforce-whitelist", "Kick non-whitelisted players", "bool", None),
    ("pvp", "PvP", "bool", None),
    ("allow-flight", "Allow flight (recommended for modpacks)", "bool", None),
    ("hardcore", "Hardcore", "bool", None),
    ("enable-command-block", "Command blocks", "bool", None),
]
PROP_DEFAULTS = {
    "motd": "A Minecraft Server", "server-port": "25565", "max-players": "20",
    "difficulty": "easy", "gamemode": "survival", "level-name": "world", "level-seed": "",
    "view-distance": "10", "simulation-distance": "10", "spawn-protection": "16",
    "op-permission-level": "4", "server-ip": "", "online-mode": "true", "white-list": "false",
    "enforce-whitelist": "false", "pvp": "true", "allow-flight": "false", "hardcore": "false",
    "enable-command-block": "false",
}
NUMERIC_ENUMS = {
    "difficulty": ["peaceful", "easy", "normal", "hard"],
    "gamemode": ["survival", "creative", "adventure", "spectator"],
}
JAVA_OPTIONS = ["8", "11", "16", "17", "21", "22", "23", "24", "25"]
QUICK_CMDS = [("list", "list"), ("save-all", "save-all"), ("Day", "time set day"),
              ("Clear weather", "weather clear"), ("TPS (forge)", "forge tps")]


def fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def fmt_num(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.0f}k"
    return str(n)


def fmt_uptime(sec: float) -> str:
    sec = int(sec)
    h, m = divmod(sec // 60, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m {sec % 60:02d}s"


def colorize(line: str) -> Text:
    if "\x1b[" in line:
        return Text.from_ansi(line)
    t = Text(line)
    if line.startswith("[mcpanel]"):
        t.stylize("bold cyan")
    elif line.startswith("> "):
        t.stylize("bold green")
    elif "ERROR]" in line or "FATAL]" in line or "/ERROR" in line or "Exception" in line:
        t.stylize("red")
    elif "WARN]" in line or "/WARN" in line:
        t.stylize("yellow")
    elif "joined the game" in line or "left the game" in line:
        t.stylize("bright_blue")
    elif "Done (" in line:
        t.stylize("bold green")
    return t


def tail_file(path: Path, lines: int = 300) -> list:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 256 * 1024))
            data = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    return data.splitlines()[-lines:]


def total_ram_mb() -> int:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // (1024 * 1024)
    except (ValueError, OSError):
        return 0


# =====================================================================
# modal screens
# =====================================================================
class ConfirmScreen(ModalScreen):
    DEFAULT_CSS = """
    ConfirmScreen { align: center middle; }
    #confirm-box { width: 70; height: auto; padding: 1 2; border: thick $warning;
                   background: $surface; }
    #confirm-box > Horizontal { height: auto; align-horizontal: right; margin-top: 1; }
    #confirm-box Button { margin-left: 1; }
    """
    BINDINGS = [Binding("escape", "no", "Cancel")]

    def __init__(self, message: str, yes: str = "Yes", no: str = "Cancel", danger: bool = False):
        super().__init__()
        self.message, self.yes, self.no, self.danger = message, yes, no, danger

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Label(self.message, markup=False)
            with Horizontal():
                yield Button(self.no, id="no")
                yield Button(self.yes, id="yes", variant="error" if self.danger else "primary")

    @on(Button.Pressed)
    def _pressed(self, ev: Button.Pressed) -> None:
        self.dismiss(ev.button.id == "yes")

    def action_no(self) -> None:
        self.dismiss(False)


class TextEditScreen(ModalScreen):
    DEFAULT_CSS = """
    TextEditScreen { align: center middle; }
    #te-box { width: 95%; height: 90%; border: thick $primary; background: $surface;
              padding: 0 1; }
    #te-area { height: 1fr; }
    #te-box > Horizontal { height: auto; align-horizontal: right; }
    #te-box Button { margin-left: 1; }
    """
    BINDINGS = [Binding("escape", "cancel", "Cancel"), Binding("ctrl+s", "save", "Save")]

    def __init__(self, title: str, text: str):
        super().__init__()
        self.title_text, self.text = title, text

    def compose(self) -> ComposeResult:
        with Vertical(id="te-box"):
            yield Label(self.title_text + "   (Ctrl+S save, Esc cancel)", markup=False)
            yield TextArea(self.text, id="te-area")
            with Horizontal():
                yield Button("Cancel", id="te-cancel")
                yield Button("Save", id="te-save", variant="success")

    @on(Button.Pressed, "#te-cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#te-save")
    def action_save(self) -> None:
        self.dismiss(self.query_one("#te-area", TextArea).text)


class InstallScreen(ModalScreen):
    """Runs a long job in a thread and shows its log + progress."""
    DEFAULT_CSS = """
    InstallScreen { align: center middle; }
    #install-box { width: 92%; height: 88%; border: thick $primary; background: $surface;
                   padding: 0 2; }
    #install-title { text-style: bold; margin-top: 1; }
    #install-bar { margin: 1 0; }
    #install-log { height: 1fr; border: round $panel; }
    #install-close { width: 100%; margin-top: 1; }
    """

    def __init__(self, title: str, job):
        super().__init__()
        self.title_text, self.job = title, job
        self.result = None
        self._last = 0.0

    def compose(self) -> ComposeResult:
        with Vertical(id="install-box"):
            yield Label(self.title_text, id="install-title", markup=False)
            yield ProgressBar(id="install-bar", show_eta=False)
            yield RichLog(id="install-log", wrap=True, markup=False, highlight=False,
                          max_lines=8000)
            yield Button("Working... please wait", id="install-close", disabled=True)

    def on_mount(self) -> None:
        self.run_job()

    @work(thread=True, exclusive=True)
    def run_job(self) -> None:
        try:
            res = self.job(self._log, self._progress)
            self.app.call_from_thread(self._finish, res, None)
        except Exception as e:
            self.app.call_from_thread(self._finish, None, (e, traceback.format_exc()))

    def _log(self, msg) -> None:
        self.app.call_from_thread(self._write, str(msg))

    def _write(self, msg: str) -> None:
        style = "bold cyan" if not msg.startswith("  ") else ""
        if "WARNING" in msg or msg.lstrip().startswith("!"):
            style = "yellow"
        self.query_one("#install-log", RichLog).write(Text(msg, style=style))

    def _progress(self, done, total) -> None:
        now = time.monotonic()
        if now - self._last < 0.1 and done != total:
            return
        self._last = now
        self.app.call_from_thread(self._set_progress, done, total)

    def _set_progress(self, done, total) -> None:
        bar = self.query_one("#install-bar", ProgressBar)
        if total:
            bar.update(total=total, progress=done)
        else:
            bar.update(total=None)

    def _finish(self, res, err) -> None:
        self.result = res
        btn = self.query_one("#install-close", Button)
        logw = self.query_one("#install-log", RichLog)
        btn.disabled = False
        if err:
            logw.write(Text(f"FAILED: {err[0]}", style="bold red"))
            logw.write(Text(err[1], style="dim"))
            btn.label, btn.variant = "Close", "error"
        else:
            self.query_one("#install-bar", ProgressBar).update(total=100, progress=100)
            btn.label, btn.variant = "Done - go to server", "success"
        btn.focus()

    @on(Button.Pressed, "#install-close")
    def _close(self) -> None:
        self.dismiss(self.result)


# =====================================================================
# new server screen
# =====================================================================
class NewServerScreen(Screen):
    BINDINGS = [Binding("escape", "back", "Back")]

    def __init__(self):
        super().__init__()
        self.mr_hits: dict = {}
        self.mr_project = None
        self.cf_hits: dict = {}
        self.cf_project = None
        self.cf_files: dict = {}

    def compose(self) -> ComposeResult:
        cfg = paths.load_config()
        ram = total_ram_mb()
        yield Header()
        with VerticalScroll(id="new-wrap"):
            yield Label("Create a new server", classes="h1")
            with Horizontal(classes="form-row"):
                yield Label("Name")
                yield Input(placeholder="(defaults to the modpack name)", id="ns-name")
                yield Label("RAM MB")
                yield Input(str(cfg.get("default_memory_mb", 4096)), id="ns-mem", type="integer")
                yield Label("Port")
                yield Input(str(instances.next_free_port()), id="ns-port", type="integer")
            yield Static(f"This machine has {ram} MB RAM. Modpacks usually want 4096-8192 MB; "
                         "leave ~1.5 GB for the OS.", classes="hint")
            yield Checkbox("I accept the Minecraft EULA  (https://aka.ms/MinecraftEULA)",
                           id="ns-eula")
            with TabbedContent(id="ns-tabs"):
                with TabPane("Modrinth", id="ns-mr"):
                    with Horizontal(classes="form-row"):
                        yield Input(placeholder="Search modpacks (empty = most popular)", id="mr-q")
                        yield Button("Search", id="mr-search", variant="primary")
                    yield DataTable(id="mr-results", cursor_type="row", zebra_stripes=True)
                    with Horizontal(classes="form-row"):
                        yield Select([], prompt="Click a modpack above, then pick a version",
                                     id="mr-version")
                        yield Button("Install", id="mr-install", variant="success")
                with TabPane("CurseForge", id="ns-cf"):
                    yield Static("Needs a free API key from https://console.curseforge.com "
                                 "(only packs that publish a Server Pack can be installed).",
                                 classes="hint")
                    with Horizontal(classes="form-row"):
                        yield Input(cfg.get("curseforge_api_key", ""), password=True,
                                    placeholder="CurseForge API key", id="cf-key")
                        yield Button("Save key", id="cf-savekey")
                    with Horizontal(classes="form-row"):
                        yield Input(placeholder="Search CurseForge modpacks", id="cf-q")
                        yield Button("Search", id="cf-search", variant="primary")
                    yield DataTable(id="cf-results", cursor_type="row", zebra_stripes=True)
                    with Horizontal(classes="form-row"):
                        yield Select([], prompt="Click a modpack above, then pick a server pack",
                                     id="cf-file")
                        yield Button("Install", id="cf-install", variant="success")
                with TabPane("Import zip / URL / folder", id="ns-imp"):
                    yield Static(
                        "Any of: a server-pack .zip, a Modrinth .mrpack, a direct download URL "
                        "(https://...), or a path to an existing server folder on this machine.\n"
                        "Forge/NeoForge installers inside the pack are run automatically.",
                        classes="hint")
                    with Horizontal(classes="form-row"):
                        yield Input(placeholder="/home/me/Downloads/ServerPack.zip  or  https://...",
                                    id="imp-src")
                        yield Button("Import", id="imp-install", variant="success")
                with TabPane("Plain loader (Vanilla/Fabric/Forge...)", id="ns-basic"):
                    with Horizontal(classes="form-row"):
                        yield Label("Loader         ")
                        yield Select([(l.title() if l != "neoforge" else "NeoForge", l)
                                      for l in loaders.LOADERS],
                                     value="fabric", allow_blank=False, id="b-loader")
                    with Horizontal(classes="form-row"):
                        yield Label("Minecraft      ")
                        yield Input(placeholder="blank = latest release", id="b-mc")
                    with Horizontal(classes="form-row"):
                        yield Label("Loader version ")
                        yield Input(placeholder="blank = latest/recommended", id="b-lv")
                    yield Static("", id="b-hint", classes="hint")
                    yield Button("Create server", id="b-install", variant="success")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#mr-results", DataTable).add_columns("Modpack", "Downloads", "Loaders",
                                                             "Latest MC", "Author")
        self.query_one("#cf-results", DataTable).add_columns("Modpack", "Downloads", "Author")
        self.search_modrinth("")
        self.load_hint()

    def action_back(self) -> None:
        self.app.pop_screen()

    # ---------------------------------------------------------- helpers
    def _err(self, msg: str) -> None:
        self.app.notify(msg, severity="error", timeout=8)

    def _common(self):
        if not self.query_one("#ns-eula", Checkbox).value:
            self._err("Please tick 'I accept the Minecraft EULA' first.")
            return None
        try:
            mem = int(self.query_one("#ns-mem", Input).value or 4096)
            port = int(self.query_one("#ns-port", Input).value or 25565)
        except ValueError:
            self._err("RAM and port must be numbers")
            return None
        if mem < 512:
            self._err("RAM must be at least 512 MB")
            return None
        if port in instances.used_ports():
            self._err(f"Port {port} is already used by '{instances.used_ports()[port].name}'")
            return None
        return self.query_one("#ns-name", Input).value.strip(), mem, port

    def _run_install(self, name: str, kind: str, params: dict, mem: int, port: int) -> None:
        def job(log, progress):
            return provision.create_server(name, kind, params, mem, port, True, log, progress)

        def done(inst):
            if inst:
                self.app.pop_screen()
                self.app.server_created(inst)
        self.app.push_screen(InstallScreen(f"Creating '{name}'", job), done)

    # ---------------------------------------------------------- modrinth
    @on(Button.Pressed, "#mr-search")
    @on(Input.Submitted, "#mr-q")
    def _mr_search(self) -> None:
        self.search_modrinth(self.query_one("#mr-q", Input).value.strip())

    @work(thread=True, exclusive=True, group="mr-search")
    def search_modrinth(self, q: str) -> None:
        try:
            hits = modrinth.search(q)
        except Exception as e:
            self.app.call_from_thread(self._err, f"Modrinth search failed: {e}")
            return
        self.app.call_from_thread(self._fill_mr, hits)

    def _fill_mr(self, hits: list) -> None:
        t = self.query_one("#mr-results", DataTable)
        t.clear()
        self.mr_hits = {}
        for h in hits:
            pid = h["project_id"]
            if pid in self.mr_hits:
                continue
            self.mr_hits[pid] = h
            lds = [c for c in h.get("categories", []) if c in ("fabric", "forge", "neoforge", "quilt")]
            gv = h.get("versions") or []
            t.add_row(Text(h.get("title", "?")), fmt_num(h.get("downloads", 0)), ", ".join(lds),
                      gv[-1] if gv else "", Text(h.get("author", "")), key=pid)

    @on(DataTable.RowSelected, "#mr-results")
    def _mr_selected(self, ev: DataTable.RowSelected) -> None:
        pid = ev.row_key.value
        self.mr_project = self.mr_hits.get(pid)
        if not self.mr_project:
            return
        sel = self.query_one("#mr-version", Select)
        sel.set_options([])
        sel.prompt = f"Loading versions of {self.mr_project['title']}..."
        self.load_mr_versions(pid)

    @work(thread=True, exclusive=True, group="mr-versions")
    def load_mr_versions(self, pid: str) -> None:
        try:
            vers = modrinth.versions(pid)
        except Exception as e:
            self.app.call_from_thread(self._err, f"Could not load versions: {e}")
            return
        self.app.call_from_thread(self._fill_mr_versions, pid, vers)

    def _fill_mr_versions(self, pid: str, vers: list) -> None:
        if not self.mr_project or self.mr_project["project_id"] != pid:
            return
        sel = self.query_one("#mr-version", Select)
        opts = []
        for v in vers:
            label = (f"{v.get('version_number', '?')}  |  MC {', '.join(v.get('game_versions', [])[-2:])}"
                     f"  |  {', '.join(v.get('loaders', []))}  |  {v.get('version_type', '')}")
            opts.append((Text(label), v["id"]))
        sel.set_options(opts)
        sel.prompt = "Pick a version"
        if opts:
            sel.value = opts[0][1]
        self.notify(f"{self.mr_project['title']}: {len(opts)} versions - newest selected")

    @on(Button.Pressed, "#mr-install")
    def _mr_install(self) -> None:
        c = self._common()
        if not c:
            return
        vid = self.query_one("#mr-version", Select).value
        if not self.mr_project or not isinstance(vid, str):
            self._err("Pick a modpack and a version first")
            return
        name = c[0] or self.mr_project["title"]
        self._run_install(name, "modrinth", {"project_id": self.mr_project["project_id"],
                                             "version_id": vid,
                                             "title": self.mr_project["title"]}, c[1], c[2])

    # ---------------------------------------------------------- curseforge
    def _cf_key(self) -> str:
        return self.query_one("#cf-key", Input).value.strip()

    @on(Button.Pressed, "#cf-savekey")
    def _cf_savekey(self) -> None:
        cfg = paths.load_config()
        cfg["curseforge_api_key"] = self._cf_key()
        paths.save_config(cfg)
        self.notify("CurseForge API key saved")

    @on(Button.Pressed, "#cf-search")
    @on(Input.Submitted, "#cf-q")
    def _cf_search(self) -> None:
        if not self._cf_key():
            self._err("Enter your CurseForge API key first")
            return
        self.search_cf(self._cf_key(), self.query_one("#cf-q", Input).value.strip())

    @work(thread=True, exclusive=True, group="cf-search")
    def search_cf(self, key: str, q: str) -> None:
        try:
            hits = curseforge.search(key, q)
        except Exception as e:
            self.app.call_from_thread(self._err, f"CurseForge search failed: {e}")
            return
        self.app.call_from_thread(self._fill_cf, hits)

    def _fill_cf(self, hits: list) -> None:
        t = self.query_one("#cf-results", DataTable)
        t.clear()
        self.cf_hits = {}
        for h in hits:
            mid = str(h["id"])
            if mid in self.cf_hits:
                continue
            self.cf_hits[mid] = h
            author = ", ".join(a.get("name", "") for a in h.get("authors", [])[:2])
            t.add_row(Text(h.get("name", "?")), fmt_num(int(h.get("downloadCount", 0))),
                      Text(author), key=mid)

    @on(DataTable.RowSelected, "#cf-results")
    def _cf_selected(self, ev: DataTable.RowSelected) -> None:
        self.cf_project = self.cf_hits.get(ev.row_key.value)
        if self.cf_project:
            sel = self.query_one("#cf-file", Select)
            sel.set_options([])
            sel.prompt = "Loading server packs..."
            self.load_cf_files(self._cf_key(), self.cf_project["id"])

    @work(thread=True, exclusive=True, group="cf-files")
    def load_cf_files(self, key: str, mod_id: int) -> None:
        try:
            files = curseforge.server_pack_files(key, mod_id)
        except Exception as e:
            self.app.call_from_thread(self._err, f"Could not load files: {e}")
            return
        self.app.call_from_thread(self._fill_cf_files, mod_id, files)

    def _fill_cf_files(self, mod_id: int, files: list) -> None:
        if not self.cf_project or self.cf_project["id"] != mod_id:
            return
        sel = self.query_one("#cf-file", Select)
        self.cf_files = {str(f["id"]): f for f in files}
        opts = []
        for f in files:
            gv = [g for g in f.get("gameVersions", []) if g[:1].isdigit()]
            opts.append((Text(f"{f.get('displayName', f.get('fileName'))}  |  MC {', '.join(gv)}"),
                         str(f["id"])))
        sel.set_options(opts)
        if opts:
            sel.prompt = "Pick a server pack"
            sel.value = opts[0][1]
        else:
            sel.prompt = "No server packs published for this modpack"
            self._err("This modpack has no CurseForge server pack. Download one from the "
                      "pack's website and use the Import tab.")

    @on(Button.Pressed, "#cf-install")
    def _cf_install(self) -> None:
        c = self._common()
        if not c:
            return
        fid = self.query_one("#cf-file", Select).value
        if not self.cf_project or not isinstance(fid, str) or fid not in self.cf_files:
            self._err("Pick a modpack and a server pack first")
            return
        name = c[0] or self.cf_project["name"]
        self._run_install(name, "curseforge", {"api_key": self._cf_key(),
                                               "mod_id": self.cf_project["id"],
                                               "file": self.cf_files[fid],
                                               "title": self.cf_project["name"]}, c[1], c[2])

    # ---------------------------------------------------------- import
    @on(Button.Pressed, "#imp-install")
    @on(Input.Submitted, "#imp-src")
    def _imp_install(self) -> None:
        c = self._common()
        if not c:
            return
        src = self.query_one("#imp-src", Input).value.strip()
        if not src:
            self._err("Enter a file path, folder or URL")
            return
        name = c[0] or Path(src.rstrip("/")).stem.replace("_", " ").replace("-", " ") or "Imported"
        self._run_install(name, "import", {"source": src}, c[1], c[2])

    # ---------------------------------------------------------- basic
    @work(thread=True, group="hint")
    def load_hint(self) -> None:
        try:
            latest = mcversions.latest_release()
            msg = f"Latest Minecraft release: {latest}"
        except Exception:
            msg = "Could not reach Mojang to check the latest version."
        self.app.call_from_thread(self.query_one("#b-hint", Static).update, msg)

    @on(Button.Pressed, "#b-install")
    def _b_install(self) -> None:
        c = self._common()
        if not c:
            return
        loader = str(self.query_one("#b-loader", Select).value)
        mc = self.query_one("#b-mc", Input).value.strip()
        lv = self.query_one("#b-lv", Input).value.strip()
        name = c[0] or f"{loader.title()} {mc or 'latest'}"
        self._run_install(name, "basic", {"loader": loader, "mc_version": mc,
                                          "loader_version": lv}, c[1], c[2])


# =====================================================================
# command input with history
# =====================================================================
class HistoryInput(Input):
    BINDINGS = [Binding("up", "hist(-1)", show=False), Binding("down", "hist(1)", show=False)]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.history: list = []
        self.hpos = 0

    def push(self, text: str) -> None:
        if text and (not self.history or self.history[-1] != text):
            self.history.append(text)
        self.hpos = len(self.history)

    def action_hist(self, d: int) -> None:
        if not self.history:
            return
        self.hpos = max(0, min(len(self.history), self.hpos + d))
        self.value = self.history[self.hpos] if self.hpos < len(self.history) else ""
        self.cursor_position = len(self.value)


# =====================================================================
# main app
# =====================================================================
MAIN_CSS = """
#body { height: 1fr; }
#sidebar { width: 40; padding: 0 1; border-right: tall $panel; }
.section-title { text-style: bold; color: $accent; margin: 1 0 0 0; }
#server-list { height: 1fr; border: round $panel; }
#server-list > ListItem { padding: 0 1; height: auto; }
#btn-new { width: 100%; margin-top: 1; }
#ts-info { height: auto; margin: 1 0; color: $text-muted; }
#main { width: 1fr; padding: 0 1; }
#empty { width: 1fr; height: 1fr; content-align: center middle; color: $text-muted; }
#detail { height: 1fr; }
#title { height: auto; padding: 0 1; border: round $accent; }
#actions { height: auto; }
#actions Button { margin-right: 1; min-width: 9; }
#tabs { height: 1fr; }
#tabs ContentSwitcher { height: 1fr; }
#tabs TabPane { height: 1fr; padding: 0; }
#console { height: 1fr; border: round $panel; }
.row { height: auto; }
.row Button { margin-right: 1; min-width: 8; }
#cmd { width: 1fr; }
.quick Button { min-width: 6; }
#pl-name { width: 26; } #pl-level { width: 32; } #pl-reason { width: 1fr; }
#pl-tables { height: 1fr; }
#pl-tables > Vertical { width: 1fr; border: round $panel; }
#pl-tables Label { text-style: bold; padding: 0 1; }
#detail DataTable { height: 1fr; }
#settings-scroll { height: 1fr; }
.set-head { text-style: bold; color: $accent; margin: 1 0 0 0; }
.set-row { height: auto; }
.set-lbl { width: 40; padding: 1 1 0 1; }
.set-row Input { width: 1fr; }
.set-row Select { width: 40; }
#mods-path, #bk-path { color: $text-muted; padding: 0 1; }
#mods-filter { width: 1fr; }
#bk-kind { width: 26; }

#new-wrap { padding: 0 2; }
.h1 { text-style: bold; color: $accent; margin: 1 0; }
.form-row { height: auto; margin-bottom: 1; }
.form-row > Label { padding: 1 1 0 0; }
#ns-name { width: 2fr; } #ns-mem { width: 14; } #ns-port { width: 12; }
#ns-tabs { height: auto; }
#mr-q, #cf-q, #cf-key, #imp-src { width: 1fr; }
#mr-results, #cf-results { height: 14; margin-bottom: 1; }
#mr-version, #cf-file { width: 1fr; }
.hint { color: $text-muted; margin-bottom: 1; }
#b-loader { width: 30; } #b-mc, #b-lv { width: 30; }
"""


class MCPanelApp(App):
    TITLE = "MCPanel"
    SUB_TITLE = "Minecraft modpack servers"
    CSS = MAIN_CSS
    BINDINGS = [
        Binding("n", "new_server", "New server"),
        Binding("s", "start", "Start"),
        Binding("x", "stop", "Stop"),
        Binding("r", "restart", "Restart"),
        Binding("b", "backup", "Backup"),
        Binding("c", "copy_address", "Copy address"),
        Binding("t", "recheck_tailscale", "Re-check Tailscale"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self):
        super().__init__()
        self.insts: dict = {}
        self.labels: dict = {}
        self.statuses: dict = {}
        self.current_id = None
        self.stream = None
        self.ts: dict = {}
        self._tick_n = 0
        self._last_players = None
        self._busy = False

    MAIN_ONLY = {"new_server", "start", "stop", "restart", "backup", "copy_address",
                 "recheck_tailscale"}

    def check_action(self, action: str, parameters):
        if action in self.MAIN_ONLY and len(self.screen_stack) > 1:
            return False
        return True

    # ------------------------------------------------------------ layout
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                yield Label("SERVERS", classes="section-title")
                yield ListView(id="server-list")
                yield Button("+ New server", id="btn-new", variant="primary")
                yield Static("", id="ts-info")
            with Vertical(id="main"):
                yield Static("No servers yet.\n\nClick '+ New server' (or press N) to install "
                             "a modpack from Modrinth / CurseForge, import a server pack, or "
                             "create a plain Fabric/Forge/NeoForge/Vanilla server.", id="empty")
                with Vertical(id="detail"):
                    yield Static("", id="title")
                    with Horizontal(id="actions"):
                        yield Button("▶ Start", id="btn-start", variant="success")
                        yield Button("■ Stop", id="btn-stop", variant="error")
                        yield Button("⟳ Restart", id="btn-restart", variant="warning")
                        yield Button("Kill", id="btn-kill")
                        yield Button("Backup", id="btn-backup")
                        yield Button("Copy IP", id="btn-copy")
                        yield Button("Delete", id="btn-delete")
                    with TabbedContent(id="tabs"):
                        with TabPane("Console", id="tab-console"):
                            yield RichLog(id="console", wrap=True, markup=False,
                                          highlight=False, max_lines=5000)
                            with Horizontal(classes="row"):
                                yield HistoryInput(placeholder="Type a server command "
                                                   "(e.g. say hello, gamerule keepInventory true)"
                                                   "  -  Up/Down = history", id="cmd")
                                yield Button("Send", id="cmd-send", variant="primary")
                            with Horizontal(classes="row quick"):
                                for label, cmd in QUICK_CMDS:
                                    btn = Button(label, classes="quick-btn")
                                    btn.mc_cmd = cmd
                                    yield btn
                                yield Button("Clear view", id="console-clear")
                        with TabPane("Players", id="tab-players"):
                            yield from self._compose_players()
                        with TabPane("Settings", id="tab-settings"):
                            yield from self._compose_settings()
                        with TabPane("Mods", id="tab-mods"):
                            yield Static("", id="mods-path")
                            with Horizontal(classes="row"):
                                yield Input(placeholder="Filter mods...", id="mods-filter")
                                yield Button("Enable/Disable", id="mods-toggle", variant="primary")
                                yield Button("Delete", id="mods-delete", variant="error")
                                yield Button("Refresh", id="mods-refresh")
                            yield DataTable(id="mods-table", cursor_type="row", zebra_stripes=True)
                        with TabPane("Backups", id="tab-backups"):
                            yield Static("", id="bk-path")
                            with Horizontal(classes="row"):
                                yield Select([("World only", "world"), ("Full server", "full")],
                                             value="world", allow_blank=False, id="bk-kind")
                                yield Button("Create backup", id="bk-create", variant="success")
                                yield Button("Restore", id="bk-restore", variant="warning")
                                yield Button("Delete", id="bk-delete", variant="error")
                                yield Button("Refresh", id="bk-refresh")
                            yield DataTable(id="bk-table", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def _compose_players(self):
        with Horizontal(classes="row"):
            yield Input(placeholder="Player name", id="pl-name")
            yield Select([("Op level 4 (owner)", 4), ("Op level 3 (admin)", 3),
                          ("Op level 2 (mod)", 2), ("Op level 1 (bypass spawn)", 1)],
                         value=4, allow_blank=False, id="pl-level")
            yield Input(placeholder="Reason for kick/ban (optional)", id="pl-reason")
        with Horizontal(classes="row"):
            for bid, label, variant in (("pl-op", "Make OP", "success"), ("pl-deop", "De-OP", "default"),
                                        ("pl-wladd", "Whitelist +", "primary"),
                                        ("pl-wlrm", "Whitelist -", "default"),
                                        ("pl-kick", "Kick", "warning"), ("pl-ban", "Ban", "error"),
                                        ("pl-pardon", "Unban", "default")):
                yield Button(label, id=bid, variant=variant, classes="pl-btn")
            yield Button("Whitelist ON", id="pl-wlon", classes="pl-btn")
            yield Button("Whitelist OFF", id="pl-wloff", classes="pl-btn")
        with Horizontal(id="pl-tables"):
            for tid, title in (("pl-online", "Online now"), ("pl-ops", "Operators"),
                               ("pl-wl", "Whitelist"), ("pl-bans", "Banned")):
                with Vertical():
                    yield Label(title)
                    yield DataTable(id=tid, cursor_type="row")

    def _compose_settings(self):
        with VerticalScroll(id="settings-scroll"):
            with Horizontal(classes="row"):
                yield Button("Save settings", id="set-save", variant="success")
                yield Button("Edit server.properties (raw)", id="set-raw")
                yield Button("Edit JVM args file", id="set-jvmfile")
                yield Button("Bind to Tailscale IP only", id="set-tsbind")
                yield Button("Re-detect launch", id="set-detect")
            yield Label("MCPanel options", classes="set-head")
            rows = [
                ("inst-name", "Display name", Input(id="inst-name")),
                ("inst-memory_mb", "Max RAM (MB, -Xmx)", Input(id="inst-memory_mb", type="integer")),
                ("inst-min_memory_mb", "Min RAM (MB, -Xms)", Input(id="inst-min_memory_mb", type="integer")),
                ("inst-java_major", "Java version (auto-downloaded)",
                 Select([(f"Java {j}", j) for j in JAVA_OPTIONS], allow_blank=False, value="21",
                        id="inst-java_major")),
                ("inst-java_path", "Custom Java binary (blank = auto)", Input(id="inst-java_path")),
                ("inst-jvm_args", "Extra JVM args", Input(id="inst-jvm_args")),
                ("inst-aikar_flags", "Use Aikar's G1GC performance flags", Switch(id="inst-aikar_flags")),
                ("inst-autostart", "Start automatically on boot", Switch(id="inst-autostart")),
                ("inst-auto_restart", "Auto-restart after a crash", Switch(id="inst-auto_restart")),
                ("inst-backup_interval_h", "Auto-backup every N hours (0 = off)",
                 Input(id="inst-backup_interval_h")),
                ("inst-backup_keep", "Backups to keep", Input(id="inst-backup_keep", type="integer")),
                ("inst-launch_mode", "Launch mode",
                 Select([("Args file (Forge/NeoForge 1.17+)", "argsfile"), ("Jar (-jar)", "jar"),
                         ("Shell script", "script")], allow_blank=True, id="inst-launch_mode")),
                ("inst-launch_target", "Launch target (relative path)", Input(id="inst-launch_target")),
            ]
            for _, label, w in rows:
                with Horizontal(classes="set-row"):
                    yield Label(label, classes="set-lbl")
                    yield w
            yield Label("server.properties", classes="set-head")
            for key, label, typ, opts in PROP_FIELDS:
                with Horizontal(classes="set-row"):
                    yield Label(label, classes="set-lbl")
                    wid = f"prop-{key.replace('.', '_')}"
                    if typ == "bool":
                        yield Switch(id=wid)
                    elif typ == "select":
                        yield Select([(o, o) for o in opts], allow_blank=False, value=opts[0], id=wid)
                    else:
                        yield Input(id=wid, type="integer" if typ == "int" else "text")

    def on_mount(self) -> None:
        self.query_one("#mods-table", DataTable).add_columns("State", "File", "Size")
        self.query_one("#bk-table", DataTable).add_columns("Backup", "Type", "Size", "Date")
        self.query_one("#pl-online", DataTable).add_columns("Name")
        self.query_one("#pl-ops", DataTable).add_columns("Name", "Level")
        self.query_one("#pl-wl", DataTable).add_columns("Name")
        self.query_one("#pl-bans", DataTable).add_columns("Name", "Reason")
        self.call_after_refresh(self.reload_list)
        self.set_interval(2.0, self.tick)
        self.set_interval(60.0, self.refresh_tailscale)
        self.refresh_tailscale()

    # ------------------------------------------------------------ list
    def _item_text(self, inst: Instance, st=None) -> Text:
        st = st or self.statuses.get(inst.id) or control.read_status(inst)
        sym, color, label = STATE_STYLE.get(st.get("state", "stopped"), STATE_STYLE["stopped"])
        t = Text()
        t.append(f"{sym} ", style=color)
        t.append(inst.name, style="bold")
        t.append(f"\n  {inst.describe()}  :{inst.port}", style="dim")
        n = len(st.get("players") or [])
        if st.get("alive"):
            t.append(f"  {n} online", style="green" if n else "dim")
        return t

    async def reload_list(self, select=None) -> None:
        insts = instances.list_instances()
        self.insts = {i.id: i for i in insts}
        lv = self.query_one("#server-list", ListView)
        await lv.clear()
        self.labels = {}
        items = []
        for i in insts:
            self.statuses[i.id] = control.read_status(i)
            lab = Label(self._item_text(i))
            self.labels[i.id] = lab
            items.append(ListItem(lab, id=f"srv-{i.id}"))
        if items:
            await lv.extend(items)
        ids = [i.id for i in insts]
        target = select or self.current_id
        if ids:
            idx = ids.index(target) if target in ids else 0
            lv.index = idx
            self.select_server(ids[idx], force=True)
        else:
            self.select_server(None, force=True)

    @on(ListView.Highlighted, "#server-list")
    def _highlighted(self, ev: ListView.Highlighted) -> None:
        if ev.item is not None and ev.item.id:
            self.select_server(ev.item.id[4:])

    def server_created(self, inst: Instance) -> None:
        self.notify(f"'{inst.name}' is ready. Press Start to play!", timeout=8)
        self.run_worker(self.reload_list(select=inst.id))

    # ------------------------------------------------------------ selection
    def cur(self):
        return self.insts.get(self.current_id) if self.current_id else None

    def select_server(self, iid, force: bool = False) -> None:
        if iid == self.current_id and not force:
            return
        changed = iid != self.current_id
        self.current_id = iid
        self.query_one("#empty").display = iid is None
        self.query_one("#detail").display = iid is not None
        if iid is None:
            self.close_stream()
            return
        inst = self.insts[iid]
        self._last_players = None
        self.refresh_header()
        self.load_settings()
        self.load_players()
        self.load_mods()
        self.load_backups()
        if changed or not (self.stream and self.stream.alive):
            self.close_stream()
            con = self.query_one("#console", RichLog)
            con.clear()
            if control.is_running(inst):
                self.open_stream()
            else:
                self.show_offline_log()

    # ------------------------------------------------------------ console
    def close_stream(self) -> None:
        if self.stream:
            self.stream.close()
            self.stream = None

    def open_stream(self) -> None:
        self.close_stream()
        iid = self.current_id
        self.query_one("#console", RichLog).clear()

        def on_lines(lines, iid=iid):
            try:
                self.call_from_thread(self._console_lines, iid, lines)
            except RuntimeError:
                pass

        def on_close(iid=iid):
            try:
                self.call_from_thread(self._console_closed, iid)
            except RuntimeError:
                pass
        self.stream = control.ConsoleStream(iid, on_lines, on_close)

    def _console_lines(self, iid: str, lines: list) -> None:
        if iid != self.current_id:
            return
        con = self.query_one("#console", RichLog)
        for line in lines:
            con.write(colorize(line))

    def _console_closed(self, iid: str) -> None:
        if iid == self.current_id:
            self.query_one("#console", RichLog).write(
                Text("[mcpanel] Console disconnected - server is no longer running.", style="bold cyan"))
            self.stream = None

    def show_offline_log(self) -> None:
        inst = self.cur()
        con = self.query_one("#console", RichLog)
        lines = tail_file(inst.logs_dir / "console.log")
        con.write(Text("Server is offline. Press Start (or S) to launch it.", style="bold yellow"))
        if lines:
            con.write(Text("--- last output ---", style="dim"))
            for line in lines:
                con.write(colorize(line))

    def send_console(self, text: str) -> None:
        text = text.strip()
        inst = self.cur()
        if not text or not inst:
            return
        if not control.is_running(inst):
            self.notify("Server is not running", severity="warning")
            return
        try:
            control.send_command(inst, text)
        except Exception as e:
            self.notify(f"Command failed: {e}", severity="error")

    @on(Input.Submitted, "#cmd")
    def _cmd_submit(self, ev: Input.Submitted) -> None:
        inp = self.query_one("#cmd", HistoryInput)
        inp.push(ev.value.strip())
        self.send_console(ev.value)
        inp.value = ""

    @on(Button.Pressed, "#cmd-send")
    def _cmd_send(self) -> None:
        inp = self.query_one("#cmd", HistoryInput)
        inp.push(inp.value.strip())
        self.send_console(inp.value)
        inp.value = ""

    @on(Button.Pressed, ".quick-btn")
    def _quick(self, ev: Button.Pressed) -> None:
        self.send_console(getattr(ev.button, "mc_cmd", ""))

    @on(Button.Pressed, "#console-clear")
    def _console_clear(self) -> None:
        self.query_one("#console", RichLog).clear()

    # ------------------------------------------------------------ status
    def address(self, inst: Instance) -> str:
        port = inst.port
        bind = inst.properties().get("server-ip", "").strip()
        if bind:
            return f"{bind}:{port}"
        if self.ts.get("ip4"):
            s = f"{self.ts['ip4']}:{port}"
            if self.ts.get("dns"):
                s += f"   or   {self.ts['dns']}:{port}"
            return s
        return f"{tailscale.lan_ip()}:{port}"

    def refresh_header(self) -> None:
        inst = self.cur()
        if not inst:
            return
        st = self.statuses.get(inst.id) or control.read_status(inst)
        state = st.get("state", "stopped")
        sym, color, label = STATE_STYLE.get(state, STATE_STYLE["stopped"])
        t = Text()
        t.append(inst.name, style="bold")
        t.append(f"   {sym} {label}", style=f"bold {color}")
        if st.get("alive") and st.get("started_at"):
            t.append(f"   up {fmt_uptime(time.time() - st['started_at'])}", style="dim")
        if state == "crashed" and st.get("exit_code") is not None:
            t.append(f"   (exit code {st['exit_code']})", style="red")
        lv = f" {inst.loader_version}" if inst.loader_version else ""
        t.append(f"\n{inst.describe()}{lv}  ·  Java {inst.java_major}  ·  RAM {inst.memory_mb} MB"
                 f"  ·  {'autostart' if inst.autostart else 'manual start'}", style="dim")
        t.append("\nConnect: ", style="bold")
        t.append(self.address(inst), style="bold cyan")
        pl = st.get("players") or []
        if st.get("alive"):
            t.append(f"\nOnline ({len(pl)}): ", style="bold")
            t.append(", ".join(pl) if pl else "nobody", style="green" if pl else "dim")
        self.query_one("#title", Static).update(t)
        alive = bool(st.get("alive"))
        self.query_one("#btn-start").disabled = alive
        for b in ("#btn-stop", "#btn-restart", "#btn-kill"):
            self.query_one(b).disabled = not alive
        self.query_one("#btn-delete").disabled = alive

    def tick(self) -> None:
        control.reap()
        self._tick_n += 1
        if self._tick_n % 10 == 0:
            on_disk = {d.name for d in paths.SERVERS_DIR.iterdir() if (d / "instance.json").exists()} \
                if paths.SERVERS_DIR.exists() else set()
            if on_disk != set(self.insts):
                self.run_worker(self.reload_list())
                return
        for iid, inst in list(self.insts.items()):
            st = control.read_status(inst)
            self.statuses[iid] = st
            lab = self.labels.get(iid)
            if lab is not None:
                lab.update(self._item_text(inst, st))
        inst = self.cur()
        if inst:
            self.refresh_header()
            st = self.statuses.get(inst.id, {})
            if st.get("alive") and not (self.stream and self.stream.alive):
                self.open_stream()
            pl = tuple(st.get("players") or [])
            if pl != self._last_players:
                self._last_players = pl
                self._fill_online(pl)

    @work(thread=True, group="tailscale")
    def refresh_tailscale(self) -> None:
        ts = tailscale.info()
        daemon = control.daemon_running()
        self.call_from_thread(self._set_ts, ts, daemon)

    def action_recheck_tailscale(self) -> None:
        self.notify("Checking Tailscale...")
        self.refresh_tailscale()

    def _set_ts(self, ts: dict, daemon: bool) -> None:
        self.ts = ts
        t = Text()
        if ts.get("ip4"):
            t.append("Tailscale ", style="bold")
            t.append("● connected\n", style="green")
            t.append(f"{ts['ip4']}\n")
            if ts.get("dns"):
                t.append(f"{ts['dns']}\n", style="dim")
        else:
            t.append("Tailscale ", style="bold")
            t.append(f"{ts.get('error', 'not detected')}\n", style="yellow")
            if ts.get("fix"):
                t.append("Fix: ", style="bold")
                t.append(f"{ts['fix']}\n", style="cyan")
            t.append(f"LAN IP {tailscale.lan_ip()}  (press T to re-check)\n", style="dim")
        t.append("Service ", style="bold")
        if daemon:
            t.append("● always-on", style="green")
        else:
            t.append("○ not running", style="yellow")
            t.append("\n(run install.sh --service)", style="dim")
        self.query_one("#ts-info", Static).update(t)
        if self.cur():
            self.refresh_header()

    # ------------------------------------------------------------ actions
    def action_new_server(self) -> None:
        self.push_screen(NewServerScreen())

    @on(Button.Pressed, "#btn-new")
    def _new(self) -> None:
        self.action_new_server()

    @on(Button.Pressed, "#btn-start")
    def action_start(self) -> None:
        inst = self.cur()
        if not inst:
            return
        if control.is_running(inst):
            self.notify("Already running")
            return
        if not inst.eula_accepted():
            def cb(ok):
                if ok:
                    inst.accept_eula()
                    self._do_start(inst)
            self.push_screen(ConfirmScreen(
                "To run a Minecraft server you must accept the Minecraft EULA:\n"
                "https://aka.ms/MinecraftEULA\n\nDo you accept it?", yes="I accept"), cb)
            return
        self._do_start(inst)

    @work(thread=True, group="ctl")
    def _do_start(self, inst: Instance) -> None:
        if not inst.java_path and not javamgr.java_for(inst):
            self.call_from_thread(self.notify, f"Downloading Java {inst.java_major} first "
                                  "(watch the console)...", timeout=6)
        try:
            ok = control.start(inst)
        except Exception as e:
            self.call_from_thread(self.notify, f"Start failed: {e}", severity="error")
            return
        if ok:
            self.call_from_thread(self.notify, f"Starting {inst.name}... friends connect to "
                                  f"{self.address(inst)}", timeout=8)
        else:
            self.call_from_thread(self.notify, "Server did not start - see logs/runner.log",
                                  severity="error")
        self.call_from_thread(self.tick)

    @on(Button.Pressed, "#btn-stop")
    def action_stop(self) -> None:
        inst = self.cur()
        if inst and control.is_running(inst):
            self._ctl(inst, control.stop, "Stopping (saving the world)...")

    @on(Button.Pressed, "#btn-restart")
    def action_restart(self) -> None:
        inst = self.cur()
        if inst and control.is_running(inst):
            self._ctl(inst, control.restart, "Restarting...")

    @on(Button.Pressed, "#btn-kill")
    def _kill(self) -> None:
        inst = self.cur()
        if not inst or not control.is_running(inst):
            return
        self.push_screen(ConfirmScreen(f"Force-kill '{inst.name}'?\nUnsaved progress since the "
                                       "last autosave will be lost.", yes="Kill", danger=True),
                         lambda ok: ok and self._ctl(inst, control.kill, "Killed"))

    @work(thread=True, group="ctl")
    def _ctl(self, inst: Instance, fn, msg: str) -> None:
        try:
            fn(inst)
            self.call_from_thread(self.notify, msg)
        except Exception as e:
            self.call_from_thread(self.notify, f"Failed: {e}", severity="error")

    @on(Button.Pressed, "#btn-copy")
    def action_copy_address(self) -> None:
        inst = self.cur()
        if inst:
            addr = self.address(inst).split()[0]
            self.copy_to_clipboard(addr)
            self.notify(f"Copied {addr}")

    @on(Button.Pressed, "#btn-delete")
    def _delete(self) -> None:
        inst = self.cur()
        if not inst:
            return
        if control.is_running(inst):
            self.notify("Stop the server first", severity="warning")
            return
        self.push_screen(ConfirmScreen(
            f"Delete '{inst.name}' permanently?\n\nThis removes the world, mods, configs AND its "
            f"backups:\n{inst.dir}\n\nThis cannot be undone.", yes="Delete forever", danger=True),
            lambda ok: ok and self._do_delete(inst))

    @work(thread=True, group="ctl")
    def _do_delete(self, inst: Instance) -> None:
        try:
            instances.delete_instance(inst)
        except Exception as e:
            self.call_from_thread(self.notify, f"Delete failed: {e}", severity="error")
            return
        self.current_id = None
        self.call_from_thread(self.notify, f"Deleted {inst.name}")
        self.call_from_thread(lambda: self.run_worker(self.reload_list()))

    # ------------------------------------------------------------ backups
    @on(Button.Pressed, "#btn-backup")
    def action_backup(self) -> None:
        self._backup("world")

    @on(Button.Pressed, "#bk-create")
    def _bk_create(self) -> None:
        self._backup(str(self.query_one("#bk-kind", Select).value))

    def _backup(self, kind: str) -> None:
        inst = self.cur()
        if not inst:
            return
        if self._busy:
            self.notify("A backup is already running", severity="warning")
            return
        self.notify(f"Creating {kind} backup of {inst.name}...")
        self._do_backup(inst, kind)

    @work(thread=True, group="backup")
    def _do_backup(self, inst: Instance, kind: str) -> None:
        self._busy = True
        try:
            out = backups.create_backup(inst, kind)
            self.call_from_thread(self.notify, f"Backup saved: {out.name} "
                                  f"({fmt_size(out.stat().st_size)})")
        except Exception as e:
            self.call_from_thread(self.notify, f"Backup failed: {e}", severity="error")
        finally:
            self._busy = False
        self.call_from_thread(self.load_backups)

    def load_backups(self) -> None:
        inst = self.cur()
        t = self.query_one("#bk-table", DataTable)
        t.clear()
        if not inst:
            return
        self.query_one("#bk-path", Static).update(f"Folder: {inst.backups_dir}   "
                                                  f"(keeping newest {inst.backup_keep})")
        for p in backups.list_backups(inst):
            stt = p.stat()
            t.add_row(p.name, backups.backup_kind(p), fmt_size(stt.st_size),
                      time.strftime("%Y-%m-%d %H:%M", time.localtime(stt.st_mtime)), key=p.name)

    def _selected_key(self, tid: str):
        t = self.query_one(tid, DataTable)
        if not t.row_count:
            return None
        try:
            return t.coordinate_to_cell_key(t.cursor_coordinate).row_key.value
        except Exception:
            return None

    @on(Button.Pressed, "#bk-refresh")
    def _bk_refresh(self) -> None:
        self.load_backups()

    @on(Button.Pressed, "#bk-restore")
    def _bk_restore(self) -> None:
        inst, name = self.cur(), self._selected_key("#bk-table")
        if not inst or not name:
            self.notify("Select a backup first", severity="warning")
            return
        if control.is_running(inst):
            self.notify("Stop the server before restoring", severity="warning")
            return
        path = inst.backups_dir / name

        def do(ok):
            if not ok:
                return
            try:
                backups.restore_backup(inst, path, log=lambda m: self.notify(m))
            except Exception as e:
                self.notify(f"Restore failed: {e}", severity="error")
        self.push_screen(ConfirmScreen(f"Restore {name}?\nThe current world will be kept as "
                                       "'<world>.before-restore'.", yes="Restore", danger=True), do)

    @on(Button.Pressed, "#bk-delete")
    def _bk_delete(self) -> None:
        inst, name = self.cur(), self._selected_key("#bk-table")
        if not inst or not name:
            return

        def do(ok):
            if ok:
                (inst.backups_dir / name).unlink(missing_ok=True)
                self.load_backups()
        self.push_screen(ConfirmScreen(f"Delete backup {name}?", yes="Delete", danger=True), do)

    # ------------------------------------------------------------ players
    def load_players(self) -> None:
        inst = self.cur()
        if not inst:
            return
        for kind, tid in (("ops", "#pl-ops"), ("whitelist", "#pl-wl"), ("bans", "#pl-bans")):
            t = self.query_one(tid, DataTable)
            t.clear()
            for n, e in enumerate(players.read_list(inst, kind)):
                name = str(e.get("name", "?"))
                if kind == "ops":
                    t.add_row(name, str(e.get("level", "")), key=f"{n}")
                elif kind == "bans":
                    t.add_row(name, Text(str(e.get("reason", ""))), key=f"{n}")
                else:
                    t.add_row(name, key=f"{n}")
        st = self.statuses.get(inst.id) or {}
        self._fill_online(tuple(st.get("players") or []))

    def _fill_online(self, pl) -> None:
        t = self.query_one("#pl-online", DataTable)
        t.clear()
        for n, p in enumerate(pl):
            t.add_row(p, key=str(n))

    @on(DataTable.RowSelected)
    def _player_row(self, ev: DataTable.RowSelected) -> None:
        if ev.data_table.id in ("pl-online", "pl-ops", "pl-wl", "pl-bans"):
            name = ev.data_table.get_row(ev.row_key)[0]
            self.query_one("#pl-name", Input).value = str(name)

    @on(Button.Pressed, ".pl-btn")
    def _player_btn(self, ev: Button.Pressed) -> None:
        inst = self.cur()
        if not inst:
            return
        action = ev.button.id[3:]
        if action in ("wlon", "wloff"):
            on_ = action == "wlon"
            try:
                if control.is_running(inst):
                    control.send_command(inst, f"whitelist {'on' if on_ else 'off'}")
                props.write_props(inst.props_file, {"white-list": "true" if on_ else "false"})
                self.notify(f"Whitelist {'enabled' if on_ else 'disabled'}")
                self.load_settings()
            except Exception as e:
                self.notify(str(e), severity="error")
            return
        name = self.query_one("#pl-name", Input).value.strip()
        if not name:
            self.notify("Type a player name (or click one in a list)", severity="warning")
            return
        level = self.query_one("#pl-level", Select).value
        reason = self.query_one("#pl-reason", Input).value
        self._player_job(inst, action, name, int(level) if isinstance(level, int) else 4, reason)

    @work(thread=True, group="players")
    def _player_job(self, inst, action, name, level, reason) -> None:
        try:
            msg = players.player_action(inst, action, name, level, reason)
            self.call_from_thread(self.notify, msg)
        except Exception as e:
            self.call_from_thread(self.notify, str(e), severity="error")
            return
        time.sleep(1.5)  # let a running server write its json files
        self.call_from_thread(self.load_players)

    # ------------------------------------------------------------ mods
    def load_mods(self) -> None:
        inst = self.cur()
        t = self.query_one("#mods-table", DataTable)
        t.clear()
        if not inst:
            return
        md = inst.server_dir / "mods"
        flt = self.query_one("#mods-filter", Input).value.strip().lower()
        if not md.is_dir():
            self.query_one("#mods-path", Static).update("No mods folder (vanilla server?)")
            return
        files = sorted((p for p in md.iterdir() if p.is_file() and
                        (p.name.endswith(".jar") or p.name.endswith(".jar.disabled"))),
                       key=lambda p: p.name.lower())
        on_ = sum(1 for p in files if p.name.endswith(".jar"))
        self.query_one("#mods-path", Static).update(
            f"{md}   -   {on_} enabled, {len(files) - on_} disabled.  "
            "Add mods by copying .jar files into this folder (e.g. with scp), then Refresh.")
        for p in files:
            if flt and flt not in p.name.lower():
                continue
            enabled = p.name.endswith(".jar")
            t.add_row(Text("ON", style="green") if enabled else Text("off", style="red"),
                      p.name, fmt_size(p.stat().st_size), key=p.name)

    @on(Input.Changed, "#mods-filter")
    @on(Button.Pressed, "#mods-refresh")
    def _mods_refresh(self) -> None:
        self.load_mods()

    @on(Button.Pressed, "#mods-toggle")
    def _mods_toggle(self) -> None:
        inst, name = self.cur(), self._selected_key("#mods-table")
        if not inst or not name:
            return
        p = inst.server_dir / "mods" / name
        new = p.with_name(name[:-len(".disabled")]) if name.endswith(".disabled") \
            else p.with_name(name + ".disabled")
        try:
            p.rename(new)
        except OSError as e:
            self.notify(str(e), severity="error")
            return
        row = self.query_one("#mods-table", DataTable).cursor_row
        self.load_mods()
        self.query_one("#mods-table", DataTable).move_cursor(row=row)
        msg = f"{'Enabled' if new.name.endswith('.jar') else 'Disabled'} {new.name}"
        if control.is_running(inst):
            msg += " (restart the server to apply)"
        self.notify(msg)

    @on(Button.Pressed, "#mods-delete")
    def _mods_delete(self) -> None:
        inst, name = self.cur(), self._selected_key("#mods-table")
        if not inst or not name:
            return

        def do(ok):
            if ok:
                (inst.server_dir / "mods" / name).unlink(missing_ok=True)
                self.load_mods()
        self.push_screen(ConfirmScreen(f"Delete mod {name}?", yes="Delete", danger=True), do)

    # ------------------------------------------------------------ settings
    def _set_select(self, wid: str, value) -> None:
        sel = self.query_one(wid, Select)
        try:
            sel.value = value
        except Exception:
            pass

    def load_settings(self) -> None:
        inst = self.cur()
        if not inst:
            return
        q = self.query_one
        q("#inst-name", Input).value = inst.name
        q("#inst-memory_mb", Input).value = str(inst.memory_mb)
        q("#inst-min_memory_mb", Input).value = str(inst.min_memory_mb)
        self._set_select("#inst-java_major", str(inst.java_major))
        q("#inst-java_path", Input).value = inst.java_path
        q("#inst-jvm_args", Input).value = inst.jvm_args
        q("#inst-aikar_flags", Switch).value = inst.aikar_flags
        q("#inst-autostart", Switch).value = inst.autostart
        q("#inst-auto_restart", Switch).value = inst.auto_restart
        q("#inst-backup_interval_h", Input).value = f"{inst.backup_interval_h:g}"
        q("#inst-backup_keep", Input).value = str(inst.backup_keep)
        lm = q("#inst-launch_mode", Select)
        if inst.launch_mode:
            self._set_select("#inst-launch_mode", inst.launch_mode)
        else:
            lm.clear()
        q("#inst-launch_target", Input).value = inst.launch_target
        pr = inst.properties()
        for key, _, typ, opts in PROP_FIELDS:
            wid = f"#prop-{key.replace('.', '_')}"
            val = pr.get(key, PROP_DEFAULTS.get(key, ""))
            if typ == "bool":
                q(wid, Switch).value = val.strip().lower() == "true"
            elif typ == "select":
                if key in NUMERIC_ENUMS and val.isdigit() and int(val) < len(NUMERIC_ENUMS[key]):
                    val = NUMERIC_ENUMS[key][int(val)]
                self._set_select(wid, val if val in opts else PROP_DEFAULTS[key])
            else:
                q(wid, Input).value = val

    @on(Button.Pressed, "#set-save")
    def _save_settings(self) -> None:
        inst = self.cur()
        if not inst:
            return
        q = self.query_one
        try:
            inst = inst.reload()
            inst.name = q("#inst-name", Input).value.strip() or inst.name
            inst.memory_mb = int(q("#inst-memory_mb", Input).value)
            inst.min_memory_mb = int(q("#inst-min_memory_mb", Input).value or 512)
            inst.java_major = int(q("#inst-java_major", Select).value)
            inst.java_path = q("#inst-java_path", Input).value.strip()
            inst.jvm_args = q("#inst-jvm_args", Input).value.strip()
            inst.aikar_flags = q("#inst-aikar_flags", Switch).value
            inst.autostart = q("#inst-autostart", Switch).value
            inst.auto_restart = q("#inst-auto_restart", Switch).value
            inst.backup_interval_h = float(q("#inst-backup_interval_h", Input).value or 0)
            inst.backup_keep = max(1, int(q("#inst-backup_keep", Input).value or 5))
            lm = q("#inst-launch_mode", Select).value
            inst.launch_mode = lm if isinstance(lm, str) else ""
            inst.launch_target = q("#inst-launch_target", Input).value.strip()
        except ValueError as e:
            self.notify(f"Invalid number: {e}", severity="error")
            return
        if inst.memory_mb < 512:
            self.notify("Max RAM must be at least 512 MB", severity="error")
            return
        updates = {}
        for key, _, typ, _o in PROP_FIELDS:
            wid = f"#prop-{key.replace('.', '_')}"
            if typ == "bool":
                updates[key] = "true" if q(wid, Switch).value else "false"
            elif typ == "select":
                updates[key] = str(q(wid, Select).value)
            else:
                updates[key] = q(wid, Input).value.strip()
        old = inst.properties()
        for key, names in NUMERIC_ENUMS.items():  # keep numeric style for very old versions
            if old.get(key, "").isdigit() and updates[key] in names:
                updates[key] = str(names.index(updates[key]))
        try:
            port = int(updates["server-port"])
        except ValueError:
            self.notify("Port must be a number", severity="error")
            return
        clash = instances.used_ports(exclude_id=inst.id).get(port)
        if clash:
            self.notify(f"Port {port} is already used by '{clash.name}'", severity="error")
            return
        if updates.get("query.port") is None and old.get("query.port"):
            updates["query.port"] = str(port)
        inst.save()
        props.write_props(inst.props_file, updates)
        self.insts[inst.id] = inst
        self.notify("Settings saved." + (" Restart the server to apply them."
                                          if control.is_running(inst) else ""))
        if inst.id in self.labels:
            self.labels[inst.id].update(self._item_text(inst))
        self.refresh_header()

    @on(Button.Pressed, "#set-raw")
    def _raw_props(self) -> None:
        inst = self.cur()
        if not inst:
            return
        try:
            text = inst.props_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""

        def done(new):
            if new is not None:
                paths.atomic_write_text(inst.props_file, new)
                self.load_settings()
                self.notify("server.properties saved")
        self.push_screen(TextEditScreen(str(inst.props_file), text), done)

    @on(Button.Pressed, "#set-jvmfile")
    def _jvm_file(self) -> None:
        inst = self.cur()
        if not inst:
            return
        f = inst.server_dir / "user_jvm_args.txt"
        text = f.read_text(errors="replace") if f.exists() else \
            "# Used by start scripts (launch mode 'script'). MCPanel's RAM/JVM settings\n" \
            "# are used for 'jar' and 'argsfile' modes.\n"

        def done(new):
            if new is not None:
                paths.atomic_write_text(f, new)
                self.notify("user_jvm_args.txt saved")
        self.push_screen(TextEditScreen(str(f), text), done)

    @on(Button.Pressed, "#set-tsbind")
    def _ts_bind(self) -> None:
        if not self.ts.get("ip4"):
            self.notify("Tailscale IP not found", severity="warning")
            return
        self.query_one("#prop-server-ip", Input).value = self.ts["ip4"]
        self.notify("Bind IP set to your Tailscale IP - only tailnet devices can join. "
                    "Click 'Save settings'.", timeout=8)

    @on(Button.Pressed, "#set-detect")
    def _detect(self) -> None:
        inst = self.cur()
        if not inst:
            return
        mode, target = loaders.detect_launch(inst.server_dir)
        if not mode:
            self.notify("Could not detect a launch target", severity="error")
            return
        self._set_select("#inst-launch_mode", mode)
        self.query_one("#inst-launch_target", Input).value = target
        self.notify(f"Detected {mode}: {target}. Click 'Save settings'.")

    @on(TabbedContent.TabActivated, "#tabs")
    def _tab(self, ev: TabbedContent.TabActivated) -> None:
        pane = ev.pane.id if ev.pane else ""
        if pane == "tab-mods":
            self.load_mods()
        elif pane == "tab-backups":
            self.load_backups()
        elif pane == "tab-players":
            self.load_players()
        elif pane == "tab-console":
            self.query_one("#cmd").focus()


def run() -> None:
    paths.ensure_dirs()
    MCPanelApp().run()
