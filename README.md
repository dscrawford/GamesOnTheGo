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

### Controllers

Bindings are written for you. `gotg play` points the emulator at whatever
controller is attached, every launch — so a new pad, a new console or a new
per-game environment needs no visit to a settings screen.

```bash
gotg controllers list          # what SDL sees, as the emulators see it
gotg controllers order         # who is player 1, player 2, and so on
gotg controllers apply --all   # write bindings now, without launching
```

It works by asking SDL which raw input drives each standard button on *that*
model, so one table covers every controller rather than needing a column each.
The identity string it binds by is built the same way the emulator builds it,
and compared as a string — which is why `list` prints it: that is what tells you
whether a controller is the same one a binding was written for.

**Making a device readable is the host's job, not this project's.** An ordinary
pad needs nothing at all. One that talks raw HID — the current Steam Controller
has no evdev node — needs the steam-devices udev rules, which
`programs.steam.enable` already installs, as does the `steam-devices` package
elsewhere. `list` says so when it can see nothing.

Each attached controller is seated in the console port of its own number, up to
the four every ares console has. The order is SDL's enumeration order by
default, which is what the emulators go by too — so `order` and the bindings
cannot disagree.

To choose instead:

```bash
gotg controllers order --set xbox     # that pad is player 1, the rest follow
gotg controllers order --clear        # back to SDL's order
gotg controllers order --json         # the same answer, for a UI
```

Naming one controller is enough: anything unnamed keeps SDL's order behind the
ones named. A pad can be named by any part of its name or by the `identity/slot`
the bindings are keyed on — a name matching two pads is refused rather than
guessed at. The choice lives in `~/.config/gotg/controllers.json`, deliberately
apart from `config.json`, which holds the server password and is kept 0600.

A pinned controller that is not attached leaves no gap; the ones behind it move
up. An order naming a pad you have put away is therefore harmless, which is
what makes it safe to keep one order across machines that do not have the same
controllers.

**The stick and the D-pad are interchangeable.** On the consoles that never had
a stick — SNES, NES, the Game Boys — both are bound to the same input, so
either moves you; ares keeps three bindings per input and any of them drives
it. Where the console does have a stick, N64 and GameCube, the stick is the
stick and the D-pad drives it too, so a game that only ever reads the stick
still answers to the D-pad. A game reading both then gets both, which is the
price of that.

| Emulator | Consoles | Written into |
|---|---|---|
| ares | SNES, NES, N64, Game Boy, Game Boy Color, Game Boy Advance | `settings.bml` |
| Dolphin | GameCube, and a Wii game played with a GameCube controller | `GCPadNew.ini` |

The two halves work differently, because the emulators do. ares binds a raw
input index, so `client/data/ares-pads.json` maps each console input to a
standard gamepad element and SDL is asked what that element is on *this* pad.
Dolphin names standard elements itself, so there is no table — only the device
line, which is inert if it names a device Dolphin cannot see.

Three present limits: a console needs an entry in `ares-pads.json`; an emulator
creates a console's section the first time that console runs, so the first
launch of a platform has nothing to write into and the one after it does; and
Wii remotes are not generated, only GameCube pads.

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
itself and whatever differs:

```nix
# client/env/snes.nix
{ helpers, ... }:
helpers.aresPlatform {
  platform = "snes";
  system = "Super Famicom";      # the directory ares files its saves under
  console = "SuperFamicom";      # the settings.bml section its bindings go in
}
```

Both of those are read off a real `settings.bml` rather than derived from the
platform slug, and a platform without them stays dormant for that feature rather
than guessing. A platform that needs to differ more stops calling the helper.

A per-game file states only its differences:

```nix
# client/env/games/snes/world.super_metroid.nix
{ ... }:
{
  isolate = true;   # its own config and saves, under ~/.local/state/gotg/env
}
```

It can also set `args`, `env`, `configFiles` (seeded on first run), `preLaunch`,
or a different `emulator` outright, and it receives the platform's attributes as
`base` — so `args = base.args ++ [ … ]` adds to the command line rather than
replacing it.

### Variants

A game can have more than one environment, named by a third component on the
file: `usa.super_mario_sunshine.bse.nix` is reached as `gotg play
usa.super_mario_sunshine bse`. Without a variant you get the plain game.

```bash
gotg play usa.super_mario_sunshine        # the game as it shipped
gotg play usa.super_mario_sunshine bse    # + BetterSunshineEngine
gotg play usa.super_mario_sunshine bsmso  # + Better Super Mario Sunshine Online
```

The two Sunshine variants install Kuribo mods, which are changes to the game's
own files rather than settings: the first launch opens the disc image, writes
the mod in, rebuilds the image and keeps it beside that variant's saves. It
takes a few minutes and happens once. The download in `~/Games` is never
touched, and each variant is its own environment — so the plain game, `bse` and
`bsmso` coexist, and removing a mod is deleting one directory.

BSE is a framework rather than a mod with a front end, so a correct install
looks exactly like Super Mario Sunshine at the title screen. What tells a
working install from a failed one is Kuribo's own log: boot with `OSREPORT`
logging on and it names each module as it loads it.

**BSMSO's online play does not work here.** The multiplayer is not in the disc
modules — a Windows launcher drives it by reading and writing the memory of the
running Dolphin process, no Linux build is published, and Wine cannot reach a
native `dolphin-emu` from inside its prefix. What the variant gives you is the
disc: the engine, the Better Sunshine Moveset and the BSMSO module. Hosting and
joining are not available.

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

**Saves only.** A pull restores what you played, not what you installed. Cemu's
`mlc01/usr/title` — installed updates and DLC — is deliberately excluded: it is
not a save, it can run to gigabytes, and it is not reconstructible from the ROM,
so a machine that has only ever pulled will have your progress and still need
those installed by hand.

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

The `gotg` on PATH in the dev shell runs `client/bin/gotg` from the working
tree, with the packaged wrapper's own dependency list on PATH. Edits apply on
save — there is nothing to rebuild and no shell to re-enter. `nix run .#gotg`
gives you the packaged article when that is what you want to test.

`nix flake check` runs 119 importer tests (pytest), 85 client tests (bats,
against a stand-in File Browser over real HTTP), ruff and shellcheck.
