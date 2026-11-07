# Windows: games built by Nix, played without it

Status: in progress on `feat/windows`. First milestone: the ares platforms,
Dolphin (GameCube, Wii) and the Harkinian ports (Ship of Harkinian,
2 Ship 2 Harkinian, BattleShip), as one zip per game.

Nix stays where it is. A Linux machine with Nix builds a game's **Windows
bundle**, pushes it to the server like a `.run`, and a Windows user downloads
it from the game's page and runs `gotg.exe`. Nothing on the Linux path
changes: `nix run gotg#...`, the bash client, danstick and the overlay are
untouched, and **every Linux derivation keeps its drvPath**. The Windows side
rides in `passthru`, which is not part of a derivation, so this holds by
construction; `checks.windowsFacet` holds it by test (a facet leaves drvPath alone).

```
nix build <library>#n64.usa.donkey_kong_64.gotgWindows
  -> gotg-n64.usa.donkey_kong_64-win64.zip
       gotg.exe          the launcher (rust/windows/crates/gotg-launch, cross-built)
       spec.json         the game's spec, with a "windows" section (below)
       emulator/         the emulator or port's Windows build, portable mode
       config/           the environment's seed files, by bundle-relative path
```

Like a `.run`, a bundle holds no game data: `gotg.exe login` stores a token
once, and each run downloads the game, keeps saves in sync and starts it.

## The contract

### In an environment: the `windows` facet

`machinery/lib.nix` takes one more argument, `windows`, describing the same
environment for Windows. Null (the default) means "no Windows build", and the
bundle builder refuses the game with `reason` (or a generic sentence).

```nix
windows = {
  # The emulator's Windows build: a derivation whose $out is the directory
  # that ships as emulator/ (nix/windows/emulators/*.nix), and the exe in it.
  emulator = windowsPkgs.ares;
  exe = "ares.exe";
  # Optional; each defaults to the Linux environment's own value, which is
  # right when the emulator takes the same command line on both.
  args = [ "{fullscreen}" "--setting" "Paths/Saves={state}/saves/" "{target}" ];
  fullscreenFlag = "--fullscreen";
  # Optional, empty by default. Same shapes and placeholders as on Linux.
  env = { };
  configFiles = { "portable/settings.bml" = ./ares-settings.bml; };
  # Every-launch edits, the data form of what a preLaunch does on Linux.
  actions = [ { ini = { file = "{state}/User/Config/Dolphin.ini"; section = "Core"; key = "SIMDisplay"; value = "False"; }; } ];
  # Where configFiles land: "state" (default) or "emulator" (beside the exe,
  # for an emulator whose portable mode reads its settings from there).
  configRoot = "state";
  # Optional: the directory the exe starts in (default "{state}"), for a
  # port whose data directory is its working directory.
  cwd = "{state}/data/soh";
  # Optional: arguments for a first run only (below).
  firstRun = {
    unlessExists = [ "{state}/data/soh/oot.o2r" "{state}/data/soh/oot-mq.o2r" ];
    stamp = { file = "{state}/data/soh/.gotg-archive-version"; value = "9.3.0"; };
    args = [ "{target}" ];
  };
};
# or, to say why there is none:
windows = { reason = "needs danstick's split-screen session, which is Linux only"; };
```

Not carried: `preLaunch` (bash), `path`, `ownsSession`, the `pad*` fields
(Windows uses each emulator's own XInput/SDL support until gotg-pads builds
there), `recipes` (the importer's, server side).

**Inheritance fails safe.** A game file merges over its platform shallowly
(`env/default.nix`). It inherits the platform's facet only when the patch
touches nothing about *how the game starts*: any of `emulator`, `bin`,
`args`, `preLaunch`, `env`, `configFiles`, `path`, `isolate`, `ownsSession`,
`nativePort`, `fullscreenFlag` in a patch without a `windows` of its own sets
`windows = null` (with a reason naming the field). So a Ship of Harkinian
game never ships ares, and Super Metroid's settings never get silently
dropped on Windows; a game that is fine adds `windows = base.windows // ...`.

### Placeholders, rendered by gotg.exe

| placeholder    | Windows value                                                       |
|----------------|---------------------------------------------------------------------|
| `{target}`     | the downloaded game file (or directory, for a multi-file install)   |
| `{install}`    | the directory it was downloaded into                                |
| `{state}`      | `%LOCALAPPDATA%\gotg\env\<env name>` (`GOTG_ENV_STATE` overrides)   |
| `{bundle}`     | the directory gotg.exe is in                                        |
| `{emulator}`   | `{bundle}\emulator`                                                 |
| `{fullscreen}` | `fullscreenFlag` or no argument at all; whole-argument only         |

Paths are rendered with backslashes. Inside a longer argument a placeholder
is substituted as text (so `Paths/Saves={state}/saves/` stays one argument,
trailing separator kept). `{fullscreen}` is on unless `GOTG_FULLSCREEN=0` or
`--windowed`; there is no "terminal means desk" test on Windows.

### In the bundle: `spec.json`

The library spec, unchanged (`version`, `server`, `files_urls`, `attr`,
`variant`, `output`, `game`), minus `env` (a store path), plus:

```json
"windows": {
  "version": 1,
  "name": "env-n64",
  "title": "...",                      // optional
  "exe": "emulator/ares.exe",          // bundle-relative, forward slashes
  "args": ["{fullscreen}", "--setting", "Paths/Saves={state}/saves/", "{target}"],
  "fullscreen_flag": "--fullscreen",
  "env": {},
  "config_root": "state",
  "config_files": { "config/Ares/settings.bml": "config/0/settings.bml" },
  "actions": [],
  "cwd": "{state}",
  "first_run": null,
  "saves": ["saves/**"], "excludes": ["saves/*.bs[0-9]"], "states": { ... },
  "saves_dirs": ["saves"],
  "keys": null, "firmware": null,
  "native_port": false,
  "game_version_min": null, "game_version_max": null
}
```

`config_files` maps a path under the config root to the file in the bundle.
`saves`, `excludes`, `states` are exactly the Linux `saves.json` fields, so
saves move between a Linux and a Windows machine (their emulators must agree
on the format; ares and Dolphin memory saves do).

### Working directory and first run

`cwd` is rendered and created if absent, and the exe starts there; absent
it is `{state}`. `first_run` is null or `{unless_exists, stamp, args}`:

1. If `stamp` is not null and `stamp.file` does not hold exactly
   `stamp.value`, delete every `unless_exists` path (an archive made by
   another version of the port; the Linux preLaunch's stamp).
2. If none of `unless_exists` exists, write `stamp.value` to `stamp.file`
   (when `stamp` is set) and start the exe with `first_run.args` instead of
   `args`. Otherwise start it with `args`.

For a port that must be handed its ROM once and never again: passing it
every launch makes Ship of Harkinian ask "Confirm Re-extract" and
2 Ship 2 Harkinian extract again. Both fields are new since d007731 and
optional, so a manifest without them means what it did.

### Actions (version 1)

Each is an object with one key. gotg.exe applies them in order on every
launch, after seeding config files.

- `ini`: `{file, section, key, value}`; set a key, creating the section and
  file as needed (Dolphin's `gotg_ini_set`). `key = value` with spaces.
- `mkdir`: `{path}`.
- `copy`: `{from, to, once}`; `from` a bundle path or `{install}` path.
  `once`: only when `to` does not exist. `{target}` is an `{install}` path.

An unknown action is an error, not a skip: a bundle never half-applies.

### What gotg.exe does, in order

1. `gotg.exe login [--server URL]`: device flow against the spec's server,
   token kept in `%APPDATA%\gotg\api.json` (same format as Linux).
2. Resolve the game in the catalog, download it into
   `%LOCALAPPDATA%\gotg\games\<platform>\` with resume and sha256 check
   (the same files_urls, the same layout as `download.sh`).
3. Fetch `keys` / `firmware` when declared (`keys.sh`, `firmware.sh`).
4. `saves sync` (what a Linux launch does: pull what is newer on the
   service, push what is newer here, leave a conflict for `saves keep`),
   create `saves_dirs`, seed `config_files` (only if absent), apply `actions`.
5. Start `exe` with the rendered args and env; wait for it.
6. `saves push`; history as on Linux. Journal and archives in
   `%LOCALAPPDATA%\gotg\saves` (Linux: `$GOTG_STATE_DIR/saves`). All of
   it is `rust/windows/crates/gotg-saves` (`Saves::sync`, `push`, `pull`, `check`,
   `keep`, `list`, `restore`, `states`); its lib.rs documents the API.

Also: `gotg.exe saves list|push|pull|restore|keep`, `gotg.exe --version`.

### gotg.exe, as built (rust/windows/crates/gotg-launch)

- `gotg login [--server URL] [--token | --claim URL]`: the server defaults to
  the spec's. At a terminal it is the device flow (`/auth/device`), falling
  back to a pasted token where the deployment has no browser sign-in;
  `--token`, or a token on a pipe, is the Linux client's typed-token login;
  `--claim` is its claim link. Checked against the service before api.json
  (`{url, token, name?}`, merged) is written. No netrc: that is Nix's.
- `gotg [--spec PATH] [--windowed] [--dry-run] [-- ARGS...]`: `--dry-run`
  prints `cwd:`, `env:` and `run:` (the command line, Windows-quoted) and
  touches nothing (and `first run: yes|no` when the spec has a `first_run`).
  The emulator starts in `cwd` (default `{state}`). While it runs, Ctrl+C
  and closing the console are the emulator's: gotg.exe ignores them (a
  console control handler) and pushes the saves once it exits.
- Downloads: `single_file`/`no_intro_set` games are placed as
  `games\<platform>\<member>`, `wiiu_decrypted` as `games\<platform>\<id>\`;
  extras (updates, DLC) are not installed. Any other handler needs a Linux
  recipe and is refused by name.
- Action paths: a relative `file`, `path` or `to` is under `{state}`; a
  relative `copy.from` is under `{bundle}`. `copy` takes a file or a tree;
  without `once` it is copied every launch.
- `GOTG_ENV_STATE` is `{state}` itself; `GOTG_GAMES`, `GOTG_CONFIG_DIR`,
  `GOTG_API_FILE`, `GOTG_LOCAL_DIR` (the %LOCALAPPDATA%\gotg half),
  `GOTG_FILES_URL`, `GOTG_FIRMWARE_DIR`, `GOTG_PROGRESS_LINES=1` and
  `GOTG_NO_BROWSER` as their names say. Off Windows (tests) the defaults are
  `$XDG_CONFIG_HOME/gotg` and `$XDG_DATA_HOME/gotg-launch`.
- Saves (`src/saves.rs`, over gotg-saves): before the game `sync(Both)`; a
  conflict stops the launch and names `gotg saves keep here|remote`; not
  signed in, an unreachable service or a refused token is a warning and the
  game starts (the Linux launch's behaviour). After it `push(false)`, whose
  failure is a warning; the game's exit code stands. Journal and archives in
  `GOTG_LOCAL_DIR\saves`; the device is api.json's `name`
  (`GOTG_DEVICE_NAME` overrides). `gotg saves list | check | push [--force] |
  pull | restore <id> | keep here|remote`, `<id>` as `list` prints it
  (`remote:<n>`, `local:<archive>`). One environment's saves are synced by
  one gotg.exe at a time (`locks\<env>.saves.lock`, 30 s wait).
- `first_run.stamp.file` is compared as the Linux `$(cat)` reads it:
  trailing newlines are not part of the value. An empty `unless_exists` is
  refused (every launch would be a first one).

## Where the pieces live

| piece                         | path                                    |
|-------------------------------|-----------------------------------------|
| the facet, its JSON           | `src/client/env/machinery/lib.nix`      |
| inheritance rule              | `src/client/env/default.nix`            |
| Windows emulator packages     | `nix/windows/emulators/<emu>.nix`       |
| bundle builder                | `nix/windows/bundle.nix`                |
| launcher                      | `rust/windows/crates/gotg-launch`      |
| save sync                     | `rust/windows/crates/gotg-saves`       |
| cross build of the launcher   | `rust/windows/gotg-launch.nix`          |
| server/web: the `win` kind    | `src/gotg/ports.py`, `routes_ports.py`  |
| drvPath guard, inheritance    | `nix/checks/windows-facet.nix`          |
| platforms that must have one  | `nix/checks/windows-platforms.nix`    |

## ares

The official release (`nix/windows/emulators/ares.nix`, flake package
`windows-ares`), the same version as nixpkgs' `ares`. Left alone it reads
`settings.bml` beside `ares.exe`, else `%LOCALAPPDATA%\ares\`, else
`%PROGRAMDATA%\ares\`, and otherwise creates it beside `ares.exe`; the
facet passes `--settings-file {state}/settings.bml` so an installed ares'
settings are never picked up and each environment has its own, and
`Paths/Home={state}/Systems/` so its `<System>.sys` directories stay out of
the bundle. `config_root` stays `"state"` with no seed files. ares still
writes `hiro\windows.bml` (window geometry) beside the exe on exit.

## Ship of Harkinian, 2 Ship 2 Harkinian, BattleShip

The ports' official Windows releases (`nix/windows/emulators/shipwright.nix`,
`2ship.nix`, `battleship.nix`; `windows-shipwright`, `windows-2ship`,
`windows-battleship`), the same versions the Linux environments run
(9.3.0, 5.0.1, 1.6). The facet is `harkinianWindows` in
`env/emulators/harkinian.nix`, set by `harkinianPort`'s `windowsPort`.

- **Where they keep things.** All three releases are portable builds
  (no `NON_PORTABLE` in their release workflows), so libultraship's
  `Context::GetAppDirectoryPath` returns `"."`, the working directory:
  `SHIP_HOME` is read only on Linux and macOS, and `SDL_GetPrefPath` only
  under `NON_PORTABLE`. Archives (`oot.o2r`, `mm.o2r`, `BattleShip.o2r`),
  settings, saves, logs and `mods/` all land in the working directory;
  `soh.o2r`, `assets/` and BattleShip's `torch.exe`/`config.yml` are read
  from beside the exe. So `cwd` is `{state}/data/<appName>`, where
  `XDG_DATA_HOME={state}/data` puts them on Linux, and a path under {state}
  means the same file on both. Seen under Wine: each wrote its settings
  file (and BattleShip `logs/`) into the working directory. BattleShip
  alone also opens an empty `%APPDATA%\BattleShip\ssb64.log` before its
  context exists.
- **Settings.** The seed `config_files` entry is the file a first Linux
  launch writes (`lusSettings`: pad menu navigation, windowed full screen,
  matched refresh rate, 4x MSAA, full screen on); it is only written when
  absent, so one pulled with the saves wins. No per-launch edit, so
  `--windowed` is not heard; F11 is the ports' own toggle.
- **The ROM.** Ship of Harkinian and 2 Ship 2 Harkinian extract their
  archive from a ROM given as an argument (`first_run`); with no archive
  and no argument they ask, and with both Ship of Harkinian asks
  "Confirm Re-extract". Ship of Harkinian then asks "All files have been
  processed. Run SoH?" once (the question pkgs/shipwright patches out on
  Linux; here it is the official build). BattleShip extracts by itself,
  silently, from `baserom.us.z64` in its data directory, so a `copy`
  action puts the ROM there once.
- Not a `run` action: Ship of Harkinian has no extract-only mode, so
  running it for the extraction would start the game, then start it again.
- The official builds refuse a few places: Ship of Harkinian and 2 Ship
  stop if their exe is under `%TEMP%` or a path containing `OneDrive`, so
  the bundle must be unpacked somewhere else.
- **Saves do not travel yet, for two of them.** The globs are the Linux
  ones, and two miss the save itself on both systems: Ship of Harkinian
  writes `Save/file<n>.sav` (SaveManager.cpp), not `saves/`, and BattleShip
  `ssb64_save.bin` beside its settings (port_save.cpp). 2 Ship's
  `saves/` is right. Fixing the globs changes the Linux saves.json, which
  moves those environments' drvPaths, so it is left for its own change.
- Split-screen variants (2p/3p/4p) have no facet.

## Not yet

Switch and Wii U (Ryubing, Cemu), the recompiled ports, sm64coopdx, Melee
PC, split-screen variants, an installer, gotg-pads and the kill switch on
Windows.
