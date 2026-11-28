# MCPanel cloud test - 43/43 checks passed
Runtime: 343s on runnervm3lb7s

| | Check | Details |
|---|---|---|
| ✅ | Mojang version manifest reachable | latest release 26.3 |
| ✅ | Java requirement lookup | MC 26.3 -> Java 25 |
| ✅ | Fabric: created | MC 26.3, loader 0.20.1, Java 25 |
| ✅ | Fabric: Java auto-downloaded | /usr/lib/jvm/temurin-25-jdk-amd64/bin/java |
| ✅ | Cloud Fabric: starts and reaches 'Done' (18s) | state=running |
| ✅ | Fabric: answers multiplayer ping on its port | version=26.3 motd='MCPanel cloud test' max=7 |
| ✅ | Fabric: console commands work |  |
| ✅ | Fabric: make operator (live) | ops.json=['CloudTester'] |
| ✅ | Fabric: whitelist add (live) | whitelist.json=['CloudTester'] |
| ✅ | Fabric: world backup while running | 20261128-161215_world.zip, 25 files, 206 KB |
| ✅ | Fabric: restart | state=running |
| ✅ | Fabric: auto-restart after crash (java killed) | state=running |
| ✅ | Cloud Fabric: graceful stop (1s) | state=stopped exit=0 |
| ✅ | Cloud Fabric: world saved on stop | save messages in log=True, level.dat updated=True |
| ✅ | Fabric: op/ban while offline (file edit) | ops={'CloudTester': 4, 'OfflineAdmin': 2} bans=['Griefer'] |
| ✅ | Fabric: restore backup |  |
| ✅ | Cloud Fabric: starts and reaches 'Done' (10s) | state=running |
| ✅ | Fabric: offline op survives server start | ops={'OfflineAdmin': 2, 'CloudTester': 4} |
| ✅ | Cloud Fabric: graceful stop (1s) | state=stopped exit=0 |
| ✅ | Cloud Fabric: world saved on stop | save messages in log=True, level.dat updated=True |
| ✅ | Modrinth: search 'cobbleverse' | COBBLEVERSE - Pokemon Adventure [Cobblemon], Cam's CobbleVerse, MyM Cobbleverse, Cobblemon Adapted, cobblemon_omega |
| ✅ | Modrinth: Cobbleverse found |  |
| ✅ | Cobbleverse: installed 1.8.1 (8s) | 96 server mods incl. Cobblemon=True, MC 1.21.1, fabric 0.19.5, Java 21 |
| ✅ | Cobbleverse: starts and reaches 'Done' (72s) | state=running |
| ✅ | Cobbleverse: answers multiplayer ping | version=1.21.1 |
| ✅ | Cobbleverse: Cobblemon loaded on the server |  |
| ✅ | Cobbleverse: no crash reports |  |
| ✅ | Cobbleverse: world backup while running | 20261128-161444_world.zip 1 MB |
| ✅ | Cobbleverse: graceful stop (3s) | state=stopped exit=0 |
| ✅ | Cobbleverse: world saved on stop | save messages in log=True, level.dat updated=True |
| ✅ | NeoForge: installed | 21.1.268, launch argsfile libraries/net/neoforged/neoforge/21.1.268/unix_args.txt |
| ✅ | Cloud NeoForge: starts and reaches 'Done' (24s) | state=running |
| ✅ | Cloud NeoForge: graceful stop (2s) | state=stopped exit=0 |
| ✅ | Cloud NeoForge: world saved on stop | save messages in log=True, level.dat updated=True |
| ✅ | Forge: installed | 47.4.28, launch argsfile libraries/net/minecraftforge/forge/1.20.1-47.4.28/unix_args.txt |
| ✅ | Cloud Forge: starts and reaches 'Done' (50s) | state=running |
| ✅ | Cloud Forge: graceful stop (4s) | state=stopped exit=0 |
| ✅ | Cloud Forge: world saved on stop | save messages in log=True, level.dat updated=True |
| ✅ | Daemon: autostarts server | state=running |
| ✅ | Daemon: SIGTERM (reboot) stops servers cleanly | state=stopped exit=0 |
| ✅ | Daemon: world saved during shutdown |  |
| ✅ | TUI: Start button launches the server |  |
| ✅ | TUI: Stop button stops + saves the server |  |
