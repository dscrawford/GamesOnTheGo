# Windows: games built by Nix, played without it

Status: in progress on `feat/windows`. First milestone: the ares platforms,
Dolphin (GameCube, Wii) and Ship of Harkinian, as one zip per game.

Nix stays where it is. A Linux machine with Nix builds a game's **Windows
bundle**, pushes it to the server like a `.run`, and a Windows user downloads
it from the game's page and runs `gotg.exe`. Nothing on the Linux path
changes: `nix run gotg#...`, the bash client, danstick and the overlay are
untouched, and **every Linux derivation keeps its drvPath**. The Windows side
rides in `passthru`, which is not part of a derivation, so this holds by
construction; `checks.windows-drvpaths` holds it by test.

```
nix build <library>#n64.usa.donkey_kong_64.gotgWindows
  -> gotg-n64.usa.donkey_kong_64-win64.zip
       gotg.exe          the launcher (rust/crates/gotg-launch, cross-built)
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

### Actions (version 1)

Each is an object with one key. gotg.exe applies them in order on every
launch, after seeding config files.

- `ini`: `{file, section, key, value}`; set a key, creating the section and
  file as needed (Dolphin's `gotg_ini_set`). `key = value` with spaces.
- `mkdir`: `{path}`.
- `copy`: `{from, to, once}`; `from` a bundle path or `{install}` path.

An unknown action is an error, not a skip: a bundle never half-applies.

### What gotg.exe does, in order

1. `gotg.exe login [--server URL]`: device flow against the spec's server,
   token kept in `%APPDATA%\gotg\api.json` (same format as Linux).
2. Resolve the game in the catalog, download it into
   `%LOCALAPPDATA%\gotg\games\<platform>\` with resume and sha256 check
   (the same files_urls, the same layout as `download.sh`).
3. Fetch `keys` / `firmware` when declared (`keys.sh`, `firmware.sh`).
4. `saves pull`, create `saves_dirs`, seed `config_files` (only if absent),
   apply `actions`.
5. Start `exe` with the rendered args and env; wait for it.
6. `saves push`; history as on Linux.

Also: `gotg.exe saves list|push|pull|restore`, `gotg.exe --version`.

### gotg.exe, as built (rust/crates/gotg-launch)

- `gotg login [--server URL] [--token | --claim URL]`: the server defaults to
  the spec's. At a terminal it is the device flow (`/auth/device`), falling
  back to a pasted token where the deployment has no browser sign-in;
  `--token`, or a token on a pipe, is the Linux client's typed-token login;
  `--claim` is its claim link. Checked against the service before api.json
  (`{url, token, name?}`, merged) is written. No netrc: that is Nix's.
- `gotg [--spec PATH] [--windowed] [--dry-run] [-- ARGS...]`: `--dry-run`
  prints `cwd:`, `env:` and `run:` (the command line, Windows-quoted) and
  touches nothing. The emulator runs with its own directory as the working
  directory.
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
- Saves: `src/saves.rs` is the seam (`SaveSync::pull`/`push`/`command`);
  until `gotg-saves` is wired in, saves stay on the machine.

## Where the pieces live

| piece                         | path                                    |
|-------------------------------|-----------------------------------------|
| the facet, its JSON           | `src/client/env/machinery/lib.nix`      |
| inheritance rule              | `src/client/env/default.nix`            |
| Windows emulator packages     | `nix/windows/emulators/<emu>.nix`       |
| bundle builder                | `nix/windows/bundle.nix`                |
| launcher                      | `rust/crates/gotg-launch`               |
| cross build of the launcher   | `rust/gotg-launch.nix`                  |
| server/web: the `win` kind    | `src/gotg/ports.py`, `routes_ports.py`  |
| drvPath guard                 | `nix/checks/windows-drvpaths.nix`       |

## Not yet

Switch and Wii U (Ryubing, Cemu), the recompiled ports, sm64coopdx, Melee
PC, split-screen variants, an installer, gotg-pads and the kill switch on
Windows.
