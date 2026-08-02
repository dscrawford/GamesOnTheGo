# GamesOnTheGo (GOTG)

Keep the game library on the server. Download a game to the laptop or the Steam
Deck the first time you launch it, and play it from Steam like anything else.

Two components share one contract:

| Component | What it does | Where it runs |
|---|---|---|
| **[importer](importer/)** | Organizes completed game torrents into a canonical `/Games` tree with hardlinks, and publishes the catalog | In-cluster CronJob |
| **[client](client/)** | `gotg` — fetches a game on demand, builds the environment it runs in, generates a Steam launcher | Desktop, Steam Deck |

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
gotg install usa.legend_of_zelda_majoras_mask    # download + build its environment + write launcher
```

`install` prints the path of a `play-<id>.sh` script. Add that to Steam with
**Games → Add a Non-Steam Game → Browse**. Launching it downloads the game if it
is missing (with a progress dialog) and then starts the emulator.

### Emulator environments

A game does not run "in an emulator" so much as in an **environment** built from
this flake: one derivation holding the emulator, the arguments it wants and any
settings, exposing a single `bin/gotg-play`. One file per platform, plus one per
game that needs something of its own:

```
client/env/snes.nix                            the base for every SNES game
client/env/games/snes/world.super_metroid.nix  what Super Metroid changes about it
```

```nix
# client/env/wiiu.nix
{ pkgs, ... }:
{
  emulator = pkgs.cemu;
  bin = "cemu";
  args = [ "-g" "{target}" ];
}
```

Where several platforms share an emulator and the awkward parts of driving it,
that shape moves into `client/env/helpers.nix` and the platform file names
itself — `snes.nix` is one line calling `helpers.aresPlatform { platform =
"snes"; }`. A platform that needs to differ stops calling the helper.

A per-game file states only its differences, and takes the platform's `base` if
it wants to add to a setting rather than replace it:

```nix
# client/env/games/snes/world.super_metroid.nix
{ base, ... }:
{
  isolate = true;                       # its own config and saves, under ~/.local/state/gotg/env
  args = base.args ++ [ "--fullscreen" ];
  env = { SDL_VIDEODRIVER = "wayland"; };
  configFiles = { "ares/settings.bml" = ./settings.bml; };   # seeded on first run
}
```

`gotg play world.super_metroid` looks at the catalog for the platform, picks
`env-snes-world_super_metroid` if that file exists and `env-snes` otherwise,
builds it from the flake if it is not here yet, and execs it. Adding a platform
is one file; a new emulator is a one-line change to the file that wants it.

### Games that are not emulated

Three games run on native ports built from their decompilations rather than in
an emulator, which is nothing more than a per-game environment naming a
different package:

| Game | Port | nixpkgs |
|---|---|---|
| Ocarina of Time | Ship of Harkinian | `shipwright` |
| Ocarina of Time: Master Quest | Ship of Harkinian | `shipwright` |
| Majora's Mask | 2 Ship 2 Harkinian | `_2ship2harkinian` |

These take the ROM once rather than on every launch: the first run extracts it
into an `.o2r` archive kept with the game's saves, and afterwards the port
starts with no ROM at all. `client/env/helpers.nix` holds that handshake, since
all three share it — the guard matters, because handing these ports a ROM they
have already extracted stops the launch on a confirmation dialog.

They read a bare `.z64`, so the entries are also marked `unzip` below. Only the
dumps the ports support will extract; SoH's list is `docs/supportedHashes.json`
in the [Shipwright](https://github.com/HarbourMasters/Shipwright) repo, and both
catalogued US dumps (NTSC 1.2 and NTSC MQ) are on it.

The build is the one part of a launch that evaluates nix, and Steam is a poor
place for it — the first launch of a platform compiles an emulator, behind a
progress dialog with no terminal to show errors in. Running `gotg install <id>`
once from a terminal keeps it out of the way; after that, launching is a symlink
test and costs nothing.

### Saves

Saves can be carried between machines through the same File Browser that serves
the library — no new infrastructure, the same account, under the hidden `.gotg`
directory the catalog already uses.

```bash
gotg saves setup                  # choose a backend, and prove it works
gotg saves status --all           # what each side has; writes nothing
gotg saves push usa.zelda         # send this machine's saves
gotg saves pull --all             # take the remote's
```

**The unit is the environment, not the game.** `env-snes` is shared by every
SNES title and holds all their saves at once, so an id is resolved the way
`play` resolves it and then the command says which environment it is really
working on.

**Emulators are told where to put their saves**, or there would be nothing
predictable to sync. ares in particular ignores XDG and writes beside the ROM,
so `env-snes` and `env-snes-world_super_metroid` would otherwise fight over the
same `.ram` file in `~/Games`. Saves written before that redirect are copied
forward by `gotg saves adopt` — dry-run by default, `--yes` to act, and it
copies rather than moves, so a wrong guess costs disk and not a save. The first
launch after upgrading does it once by itself, because a Steam shortcut is the
only place many of these games are ever started from.

A save set travels as one deterministic `tar.zst`, so an unchanged one hashes
identically and a push from a machine that has changed nothing uploads nothing.
Each push is a numbered generation kept alongside the last, and one `latest.json`
says which is current.

**Nothing here ever deletes a save.** A push that would overwrite work done
elsewhere stops and prints both sides with the three commands that resolve it; a
`--force` keeps the generation it overtook; a pull archives what was here first,
under `~/.local/state/gotg/saves/local/`. Times and device ids are printed for
you to read and are never used to decide anything — Decks suspend and their
clocks drift, and an mtime rule silently picks the wrong side.

Bundles are **not encrypted**: anyone who can read `/Games/.gotg/saves` can read
your saves.

### Per-game tweaks

*How* a game runs lives in `client/env` above. What is left in
`client/data/overrides.json` is what the CLI has to know before anything is
built, so that `gotg list` still works offline. Copy it to
`~/.config/gotg/overrides.json` to change it per machine; the local copy wins.
Keys are `id` or `platform/id`:

| Field | Meaning |
|---|---|
| `target` | glob for the file to launch, relative to the installed game |
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
- It is a hostile place to evaluate nix, so environments are built into GC roots
  and a launch that finds one there never runs nix at all.

## Development

```bash
nix develop        # gotg on PATH, plus python, pytest, ruff, shellcheck, bats
nix flake check    # every test suite and linter
```

The `gotg` on PATH in the dev shell is the wrapped build, not `client/bin/gotg`
directly, so re-enter the shell (direnv reloads on its own) to pick up edits.

`nix flake check` runs 119 importer tests (pytest), 66 client tests (bats,
against a stand-in File Browser over real HTTP), ruff and shellcheck.
