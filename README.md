# GamesOnTheGo (GOTG)

Keep the game library on the server. Download a game to the laptop or the Steam
Deck the first time you launch it, and play it from Steam like anything else.

Two components share one contract:

| Component | What it does | Where it runs |
|---|---|---|
| **[importer](importer/)** | Organizes completed game torrents into a canonical `/Games` tree with hardlinks, and publishes the catalog | In-cluster CronJob |
| **[client](client/)** | `gotg` — fetches a game on demand, builds its emulator, generates a Steam launcher | Desktop, Steam Deck |

## The entry-id contract

```
/Games/<platform>/<region>.<title_slug>[.<ext>]
```

The entry name **minus its extension** is the game id, used verbatim as the
server path, the catalog key, the launcher filename and the CLI argument.

- Matches `^[a-z]{3,5}\.[a-z0-9][a-z0-9_]*$` — lowercase `a-z 0-9 _` only.
- `region` is `usa`, `eur`, `jpn` or `world`.
- Files keep their real extension; directories get none.

`Legend of Zelda, The - Majora's Mask (USA).z64` becomes
`/Games/n64/usa.legend_of_zelda_majoras_mask.z64`, id
`usa.legend_of_zelda_majoras_mask`.

Ids are unique per platform but **not globally** — the same title appears in both
the N64 and SNES sets. A bare id works whenever it is unambiguous; otherwise
qualify it as `snes/usa.bugs_life`.

## Using the client

```bash
# One-time, from a terminal
nix build ~/Documents/GOTG#gotg -o ~/.local/state/gotg/app
~/.local/state/gotg/app/bin/gotg login          # server URL + credentials, saved 0600

gotg list                                        # what is on the server, and what is here
gotg install usa.legend_of_zelda_majoras_mask    # download + build emulator + write launcher
```

`install` prints the path of a `play-<id>.sh` script. Add that to Steam with
**Games → Add a Non-Steam Game → Browse**. Launching it downloads the game if it
is missing (with a progress dialog) and then starts the emulator.

`gotg install` and `gotg sync` build things with nix, so run them from a terminal.
Steam's launch environment cannot evaluate nix — `gotg play` only ever execs
binaries that were built ahead of time.

### Per-game tweaks

Copy `client/data/overrides.json` to `~/.config/gotg/overrides.json` and edit it;
the local copy wins. Keys are `id` or `platform/id`:

| Field | Meaning |
|---|---|
| `emulator` | name from `client/data/emulators.json` |
| `target` | glob for the file to launch, relative to the installed game |
| `args` | replaces the emulator's argument template entirely |
| `unzip` | unpack a zipped ROM after download, for emulators that cannot read archives |

## Why launching is the way it is

Steam is a hostile launch environment, and each of these was learned the hard way
(see `client/templates/launcher.sh.tpl`):

- It provides a minimal `PATH`, so nix has to be put back on it.
- It sets SDL variables that suppress controller detection for non-Steam apps,
  which have to be undone or the gamepad is invisible.
- It `LD_PRELOAD`s its overlay into everything, which breaks nix's wrapper
  scripts — they die at startup with "cannot open shared object file".
- It swallows stdout and stderr, so every launch logs to
  `~/.local/state/gotg/logs/<id>.log`.
- It cannot evaluate nix, so emulators are pre-built into GC roots and the
  launcher only execs an existing store path.

## Development

```bash
nix develop        # python, pytest, ruff, shellcheck, bats
nix flake check    # every test suite and linter
```

`nix flake check` runs 119 importer tests (pytest), 31 client tests (bats,
against a stand-in File Browser over real HTTP), ruff and shellcheck.
