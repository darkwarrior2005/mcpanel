# MCPanel - Minecraft modpack servers from a TUI

Run any number of Minecraft modpack servers on your own Linux machine and manage
everything from one terminal UI (mouse **and** keyboard), over SSH, through Tailscale.

- **Install modpacks in a few clicks**: search **Modrinth** or **CurseForge**, import any
  server-pack **.zip / .mrpack / URL / folder**, or create a plain **Vanilla / Fabric / Forge /
  NeoForge / Quilt** server.
- **Java is automatic**: the right Java (8 / 17 / 21 / 25 ...) is picked from the Minecraft
  version and downloaded for you (Eclipse Temurin). No system Java needed.
- **Host several servers at once**, each on its own port (assigned automatically). Start, stop,
  restart or kill whichever one you want to play.
- **Live console** with command input, history and quick buttons (`list`, `save-all`, ...).
- **Players**: make/remove operators (level 1-4), whitelist add/remove/on/off, kick, ban, unban.
  Works live (sends commands) and while the server is offline (edits ops.json etc.).
- **Settings** (save automatically as you change them, with a live preview of the exact
  launch command): RAM, Java version, JVM flags (Aikar's flags preset), MOTD, port, difficulty,
  gamemode, max players, view distance, PvP, online-mode, whitelist, seed... plus a raw
  `server.properties` editor.
- **Mods**: list, filter, enable/disable (`.jar` <-> `.jar.disabled`), delete.
- **Backups**: world or full backups (safe while running: `save-off` / `save-all flush`),
  restore, auto-backup every N hours, keep the newest N.
- **Always on**: a systemd service auto-starts chosen servers on boot, keeps them running
  after you close the TUI or log out, auto-restarts crashed servers, and saves + stops all
  servers cleanly on shutdown/reboot.
- **Tailscale aware**: shows your Tailscale IP / MagicDNS name as the address to give
  friends, and can bind a server to the Tailscale IP only.

---

## 1. Install (on the server machine)

Requirements: Linux, Python 3.9+, `python3-venv`. (Debian/Ubuntu: `sudo apt install python3 python3-venv`)

```bash
git clone https://github.com/darkwarrior2005/mcpanel ~/mcpanel-src
cd ~/mcpanel-src
./install.sh --service
```

> Keep the code in `~/mcpanel-src`. `~/mcpanel` is MCPanel's **data** folder (your worlds) -
> never delete it.

This creates a private virtualenv in `~/.local/share/mcpanel`, installs the `mcpanel`
command to `~/.local/bin`, keeps all data in `~/mcpanel`, and installs + starts the
`mcpanel` systemd service (it asks for sudo once). Run it as the normal user who should own
the servers (not root).

Then just run:

```bash
mcpanel
```

> If `mcpanel` is "not found", open a new shell or add `export PATH="$HOME/.local/bin:$PATH"`
> to `~/.bashrc`.

## 2. Play

1. Press **N** (or click **+ New server**).
2. Tick **I accept the Minecraft EULA**, pick RAM (the screen shows how much the machine has).
3. **Modrinth** tab: search, click a modpack, pick a version, click **Install**.
   (or CurseForge / Import / Plain loader - see below)
4. When it says *Done*, click **Done - go to server**, then **Start** (or press **S**).
5. Give your friends the **Connect:** address shown at the top, e.g. `100.101.102.103:25565`
   or `myserver.tailnet-name.ts.net:25565`. They need to be on your tailnet (or you can
   [share the machine](https://tailscale.com/kb/1084/sharing) with them in Tailscale).
6. The **client** needs the same modpack installed (Modrinth App, Prism Launcher, CurseForge
   app, ATLauncher...).

Each server gets its own port automatically (25565, 25566, ...), so several can run at once.

### Keyboard shortcuts (main screen)

| Key | Action | Key | Action |
|---|---|---|---|
| `N` | new server | `B` | backup world |
| `S` | start | `C` | copy connect address |
| `X` | stop (saves world) | `Q` | quit the TUI (servers keep running) |
| `R` | restart | `Esc` | back / cancel |

Everything is also clickable. In the console input: `Up`/`Down` = command history.
`Ctrl+P` opens the command palette (e.g. to change the colour theme).

## 3. Installing modpacks

| Tab | Use it for |
|---|---|
| **Modrinth** | Search + one-click install of any Modrinth modpack. Client-only mods are skipped automatically, server overrides applied, loader installed. |
| **CurseForge** | Needs a free API key from <https://console.curseforge.com> (paste it once, *Save key*). Installs the pack's official **Server Pack**. Packs without a server pack can't be installed automatically. |
| **Import** | A server-pack `.zip`, a `.mrpack`, an `https://` download link, or a path to an existing server folder. Bundled Forge/NeoForge installers are run for you. Great for FTB / ATLauncher / website server packs. |
| **Plain loader** | Vanilla, Fabric, Forge, NeoForge or Quilt for any Minecraft version (blank = latest). Add mods yourself into the `mods` folder. |

Server files live in `~/mcpanel/servers/<id>/server/` - copy mods/configs in with `scp`,
`rsync` or SFTP, then hit *Refresh* in the Mods tab.

## 4. Always-on service

```bash
sudo systemctl status mcpanel        # is it running?
journalctl -u mcpanel -f             # service log (autostart, backups, shutdown)
sudo systemctl restart mcpanel       # NOTE: stops (saves) all servers; autostart ones come back
```

- In a server's **Settings** tab turn on **Start automatically on boot**.
- **Auto-restart after a crash** is on by default (gives up after 3 crashes in 10 minutes).
- **Auto-backup every N hours** runs while the server is running.
- Without the service everything still works; servers just won't come back after a reboot.

## 5. Command line (handy for scripts/cron)

```bash
mcpanel list                     # all servers + state + online players
mcpanel start <name|id>
mcpanel stop <name|id>           # graceful, waits until saved
mcpanel restart <name|id>
mcpanel cmd <name|id> say Server restarting in 5 minutes
mcpanel console <name|id>        # attach to live console (Ctrl+C detaches, server keeps running)
mcpanel backup <name|id>
```

## 6. Tailscale tips

- Nothing needs port-forwarding: friends on your tailnet connect to the Tailscale IP + port.
- **Settings -> Bind to Tailscale IP only** sets `server-ip` so the server is reachable
  *only* over Tailscale (not your LAN/Internet). Click *Save settings*, then restart.
- Keep **Online mode** ON unless you know why you need offline mode.
- If you use the Tailscale ACL policy, allow TCP to your server ports (25565+).

## 7. Files & settings

```
~/mcpanel/
  config.json                 global: CurseForge key, default RAM, stop timeout, restart limits
  servers/<id>/instance.json  per-server panel settings (RAM, Java, flags, autostart...)
  servers/<id>/server/        the Minecraft server itself (mods, config, world, server.properties)
  servers/<id>/backups/       backups (.zip)
  servers/<id>/logs/console.log   full console history; runner.log = supervisor errors
  java/<version>/             auto-downloaded Java runtimes
```

## 8. Troubleshooting

- **Server stops right after starting** - look at the Console tab (and `logs/console.log`).
  Usual causes: not enough RAM (raise *Max RAM*), wrong Java (change *Java version*), a
  client-only mod in a manually imported pack (disable it in the Mods tab).
- **"Don't know how to start this server"** - Settings -> *Re-detect launch*, or set
  *Launch mode* (`argsfile` for Forge/NeoForge 1.17+, `jar` for Fabric/old Forge/vanilla,
  `script` for packs that only ship `start.sh`) and *Launch target*, then Save.
- **Script mode** packs (`start.sh`): MCPanel's Max/Min RAM and Extra JVM args still apply
  (passed via `_JAVA_OPTIONS`, which overrides the script's own `-Xmx`). The launch preview in
  Settings shows exactly what is used. Some scripts loop forever on stop; prefer `jar`/`argsfile`.
- **Mouse doesn't work over SSH** - use a modern terminal (Windows Terminal, iTerm2, kitty,
  GNOME Terminal...). Keyboard always works: Tab moves focus, Enter presses buttons.
- **Java download fails** - install it yourself (`sudo apt install openjdk-21-jre-headless`);
  MCPanel finds system Java automatically, or set *Custom Java binary* in Settings.

## Updating

```bash
cd ~/mcpanel-src
./update.sh
```

Pulls the newest code and replaces only MCPanel's program files (a few seconds - no
reinstall). Your servers, worlds, mods, backups and settings are not touched, and the
background service restarts **without stopping running servers**. Reopen `mcpanel`
afterwards; restart servers (R) when convenient so they pick up the new code too.

## Uninstall

`./uninstall.sh` removes the program and service. Your worlds in `~/mcpanel` are left alone.
