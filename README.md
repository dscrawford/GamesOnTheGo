# GamesOnTheGo (GOTG)

Your games, on a server you host; every one of them a Nix flake output.

![nix search gotg majora, then the picker listing the N64 Zeldas](docs/demos/gotg-search.gif)

```bash
nix run gotg#n64.usa.legend_of_zelda_majoras_mask
```

First run: the bytes come down from your server, the emulator or port is
built, the room's controllers mapped. Every run: saves pulled before, the
overlay up, Exit pushes them after; the Deck continues where the desktop
left off.

**GOTG ships no games and downloads none from anyone but your own server.**
It is for games you own and dumped yourself, or bought digitally -- console
keys and firmware too. Do not index material you have no right to copy.

| Component | Does | Runs |
|---|---|---|
| [indexer](src/gotg/indexer/) | searches a folder for games and indexes them into the catalog, hardlinked into `/Games` | CronJob |
| [service](src/gotg/service/) | serves the catalog, artwork and saves | Deployment |
| [library](templates/library/) | a flake naming your server and pinning its catalog; every game an output | Desktop, Steam Deck |
| [launcher](src/client/) | `gotg` — what a game's output runs: fetch, saves, controllers, overlay | inside every game |
| [picker](src/ui/) | `nix run gotg#ui` — controller-driven game grid | Desktop, Steam Deck |

## Install

Steam Deck (Desktop Mode, Konsole) or any Linux:

```bash
curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/dscrawford/GamesOnTheGo/master/install.sh \
  | bash -s -- --server https://gotg.example.org --claim <invite url>
```

`bash -s -- --dry-run` prints the steps and changes nothing:

```console
  GOTG — installing onto this machine
==> Nix is here (already done)
==> flakes are on (already done)
==> making a library in /home/you/.config/gotg/library, of https://gotg.example.org
   would run: nix flake new /home/you/.config/gotg/library -t github:dscrawford/GamesOnTheGo#library
   would run: replace https://gotg.dcraw.net with https://gotg.example.org in /home/you/.config/gotg/library/flake.nix
==> signing in with the invite link
   would run: nix run github:dscrawford/GamesOnTheGo#login -- --claim <invite url>
==> building the picker, and any game already here, from /home/you/.config/gotg/library
   would run: nix flake lock /home/you/.config/gotg/library
   would run: nix run /home/you/.config/gotg/library#update
   would run: /home/you/.local/state/gotg/app/bin/gotg library /home/you/.config/gotg/library
==> letting GOTG publish controllers (/dev/uinput)
   would run: write KERNEL=="uinput", … to /etc/udev/rules.d/99-gotg-uinput.rules
   would ask for your password
==> putting GOTG in your Steam library
   would run: /home/you/.local/state/gotg/app/bin/gotg steam picker
Dry run done. Nothing was changed.
```

Nothing goes in a Nix profile. The **library** is the install: a flake in
`~/.config/gotg/library` naming your server (`--server`) and pinning its
catalog, each game an output of it ([docs/nix-games.md](docs/nix-games.md)).
`--claim <url>` signs in with an admin's invite link; without one the token
is asked for. By hand, and after a SteamOS update: [docs/install.md](docs/install.md).

```bash
nix flake update gotg --flake ~/.config/gotg/library && nix run gotg#update   # upgrade (or the installer again)
curl … /uninstall.sh | bash -s -- --games        # uninstall; --games takes games and saves too, Nix stays
```

## Play

One game, standalone -- no picker, no Steam, no `gotg` on PATH:

```bash
# a machine with a token and nothing else: makes ~/.config/gotg/library and runs the game from it
GOTG_SERVER=https://gotg.example.org GOTG_TOKEN=… nix run github:dscrawford/GamesOnTheGo#play -- n64.usa.donkey_kong_64
nix run github:dscrawford/GamesOnTheGo#play -- usa.donkey_kong_64      # from then on: the token is in ~/.config/gotg/api.json
nix run ~/.config/gotg/library#n64.usa.donkey_kong_64                   # any library, by its path
nix build ~/.config/gotg/library#n64.usa.donkey_kong_64 && ./result/bin/gotg-n64-usa-donkey_kong_64   # built once, run as a program
```

`#play` pins its library to the GOTG it ran from and adds no udev rule:
until `install.sh` has, keyboard and mouse are the controllers.
`nix registry add gotg ~/.config/gotg/library` (the installer does it) makes
a library `gotg#…` from anywhere:

```console
$ nix search gotg majora
* legacyPackages.x86_64-linux.n64.usa.legend_of_zelda_majoras_mask
  Legend Of Zelda - Majora's Mask (n64)

$ nix run gotg#n64.usa.legend_of_zelda_majoras_mask
```

An id is `<region>.<title_slug>`, unique per platform; the attribute is
`<platform>.<region>.<title>`, or `gotg#usa.<title>` when the id is on one
platform only.

```bash
nix run gotg#n64.usa.legend_of_zelda_ocarina_of_time_rev2.rando        # a variant
nix run gotg#n64.usa.donkey_kong_64.emulate                              # a native port, back on the emulator
nix run gotg#switch.world.legend_of_zelda_tears_of_the_kingdom -- --version 1.4.2   # one of its updates
nix flake update catalog --flake ~/.config/gotg/library                  # games added on the server since
~/.local/state/gotg/app/bin/gotg help     # the launcher inside every game: built by the library, never installed
```

The `gotg …` lines below are that program. What a game printed is kept only
with `gotg logs on` ([Session logs](#session-logs)); the picker Steam starts
logs to `~/.local/state/gotg/logs/gotg-ui.log`.

## Picker

```console
$ nix run gotg#ui -- --list --search zelda --platform n64
n64       jpn.zelda_no_densetsu_mujura_no_kamen_rev1 Zelda No Densetsu - Mujura No Kamen
n64       usa.legend_of_zelda_majoras_mask         Legend Of Zelda - Majora's Mask
n64       usa.legend_of_zelda_ocarina_of_time_master_quest Legend Of Zelda - Ocarina Of Time - Master Quest
n64       usa.legend_of_zelda_ocarina_of_time_rev2 Legend Of Zelda - Ocarina Of Time
…
page 1 of 1 · 7 games
$ nix run gotg#ui                         # the grid itself
$ nix run ~/.config/gotg/library          # the same, by path: a library's default app
$ nix run gotg#steam -- picker            # in Steam, as "Games On The Go"
```

![The picker's mod menu on Four Swords Adventures: Back, vanilla, 2p, 3p, 4p](docs/ui-mods-menu.png)

| Pad | Keyboard | Does |
|---|---|---|
| D-pad | arrows | move |
| A | Enter / tap `Space` | game menu: Play, Install, mods, versions, saves, Steam |
| B | Esc / q | back / quit |
| LB / RB | PgUp / PgDn | page |
| X | `/` or `f` | search, on a keyboard drawn over the grid: D-pad walks the keys, A types one, X deletes, Y is a space; **Close** at the bottom right (or B) brings it down. A real keyboard types straight in; Esc puts the search back |
| Y | — | next platform |
| Start | Tab | menu: Platform, Region, Installed, Search, Clear all, View |
| Select | — | update, when the chip at the top right says one is available ([docs/variants.md](docs/variants.md)) |
| hold any button | hold `Space` | take a free seat — the pad (or keyboard) icon above the grid fills in, then you are that player |
| — | `i` | installed only |
| — | `s` | storage |

The picker opens on the platform, region, search and view it was closed
with (`~/.local/state/gotg/picker.json`). Install from the menu runs behind
the grid, a ring on the tile; Play on a game that is not here yet shows the
download and the build on screen, then becomes the game.

![Four Swords Adventures, split-screen for two](docs/four-swords-2p.png)

## Controllers

```console
$ gotg controllers list
Steam Controller
  binds as   03002854de2800000413000002006800/0
  reached by /dev/hidraw2 (raw HID, no evdev node)
  mapping    32 elements
  motion     gyro and accelerometer — used by Ryujinx and Cemu
```

Launches run through [danstick](https://github.com/dscrawford/danstick):
pads republished via `/dev/uinput`, seated by a held button, bound before
the emulator starts. **Every session starts with nobody seated.** Hold a
button for a second and a half: the bar at the top fills in your colour, you
are player one, the next hold is player two, and games are bound in that
order. Over any game:

| Chord | Does |
|---|---|
| L + R + Select, held ½ s | that player's menu: the seats, the game's controller lit by every press, rebind, reorder, unseat, port off/on, a save to load, **Exit** (pushes saves) — [docs/overlay.md](docs/overlay.md) |
| L + R + Start, held 3 s | stop the game |
| Select | a libultraship port's own menu (Paper Mario, Ocarina, Majora, Smash) |

Only pads danstick has published move the picker; the keyboard takes a seat
too (hold `Space`), and a controller that is also a keyboard is held quiet
at the kernel. Tested against a real daemon and real devices:

```bash
nix run github:dscrawford/GamesOnTheGo#test-controllers      # from a checkout (or GOTG_DEV_ROOT=<one>); /dev/uinput writable, no danstick daemon up
gotg controllers list --as-game      # what a game sees: inside danstick's sandbox, clones only
gotg controllers order --set xbox    # pin player 1; --clear to undo
gotg controllers apply [<id>|--all]  # write bindings without launching
```

## Steam

```console
$ nix run gotg#steam -- list
Legend Of Zelda - Majora's Mask
  /home/you/Games/n64/play-usa.legend_of_zelda_majoras_mask.sh
Paper Mario (paperboat)
  /home/you/Games/n64/play-usa.paper_mario-paperboat.sh
Games On The Go
  /home/you/.local/state/gotg/launchers/gotg-ui.sh
```

`picker` adds GOTG itself; `add <id> [variant]` writes the launcher, the
shortcut and the artwork; `remove <id> [variant]` (`remove picker`);
`pending` is what queued while Steam was open; `art <id> [variant] [--force]
[--from <file|url>] [--as tile|capsule|hero|logo|icon]`. Steam reads that
file once, at startup, and rewrites it from memory when it exits: close it
before changes, restart it to see them. The rest of what it cost to find out:
[docs/steam.md](docs/steam.md).

## Variants

```bash
nix run gotg#n64.usa.legend_of_zelda_four_swords_adventures.2p
```

| Game | Variants |
|---|---|
| `gamecube.usa.super_mario_sunshine` | `bse`, `bsmso` |
| `gamecube.usa.legend_of_zelda_four_swords_adventures` | `2p`, `3p`, `4p` (split-screen) |
| `n64.usa.super_mario_64` | `pc`, `pc-2p`, `pc-3p`, `pc-4p` (sm64coopdx, split-screen) |
| `n64.usa.legend_of_zelda_majoras_mask` | `rando` |
| `n64.usa.legend_of_zelda_ocarina_of_time_rev2` | `rando`, `2p`, `3p`, `4p` (Anchor co-op, split-screen) |
| `n64.usa.paper_mario` | `paperboat` (MasterKillua's Refolded textures, from a mod release at `/Games/n64/mods/usa.paper_mario/<release>/`) |
| `gba.world.pokemon_emerald_version` | `rogue` |
| `switch.world.legend_of_zelda_breath_of_the_wild` | `60fps`, `120fps` (needs 1.6.0) |
| `switch.world.legend_of_zelda_tears_of_the_kingdom` | `60fps`, `120fps`, `enhanced` (needs 1.1.0–1.4.2) |
| `switch.world.legend_of_zelda_skyward_sword_hd` | `120fps` (needs 1.0.1) |
| `switch.world.luigis_mansion_2_hd` | `60fps`, `120fps` |
| `switch.world.kirby_and_the_forgotten_land` | `60fps` |
| `switch.world.paper_mario_the_thousand_year_door` | `60fps` (text always skippable; ZR + D-pad Down fast, ZR + D-pad Up instant) |
| `switch.world.super_mario_rpg` | `120fps` |

A variant is a file, `src/client/env/games/<platform>/<id>.<variant>.nix`;
one whose version range matches nothing installed is hidden. Native ports
(Ship of Harkinian, 2S2H, the recomps, BattleShip, Open Nectar, sm64coopdx,
PaperBoat, ACGC, melee-pc) replace the emulator, and `.emulate` puts a
whole-game port back on it. The **Update available** chip, the **!** badge,
`gotg update`, mods placed on the server by hand, and the Deck profile the
Switch variants get (`GOTG_MACHINE`, `GOTG_EXTERNAL_DISPLAY`):
[docs/variants.md](docs/variants.md).

## Saves

```bash
gotg saves status [<id>|--all]      # compare; writes nothing
gotg saves push   [<id>|--all] [--force]
gotg saves pull   [<id>|--all]
gotg saves check  <id> [variant] [--json]       # conflict? which machine, and when
gotg saves keep   <id> [variant] here|remote    # settle one
```

A launch pulls when the server is ahead and nothing local changed; the
overlay's **Exit** pushes (so does loading another save from it), and a
plain quit keeps saves here until the next `gotg saves push`. **Both sides
changed since they last matched** is a conflict: the picker shows both
saves, machine and time, and keeps the one you pick, the other set aside. A
push against a newer server copy is refused (409) unless `--force`. Local
history: `~/.local/state/gotg/saves/local/`, last 3 (`GOTG_SAVES_KEEP`).
Wii U saves are not synced yet.

## QA

```console
$ gotg qa usa.donkey_kong_64 --machine deck --overlay-at 20
running Donkey Kong 64 headless for 60s here, standing in for a deck

run:     ~/.local/state/gotg/qa/runs/20261001-171902-Cxih
machine: deck
  boots      pass
  audio      pass
  video      pass
  controller pass
  graphics   skip
  overlay    pass
pass: usa.donkey_kong_64
```

A minute of the game under a headless compositor with a virtual pad, graded
for boot, sound, motion, input and the overlay coming down. `--machine deck`
is a Steam Deck's profile (no host GL, X11, C locale) and says whether it ran
on the real one or a stand-in; `--spec <file>` is a game exactly as its
output launches it; `--bless` keeps the frame as the golden image.
`nix run github:dscrawford/GamesOnTheGo#qa`.

## Session logs

```bash
gotg logs on | off | status
```

Off unless you turn it on. On, every launch keeps what the game, danstick
and the overlay printed (`console.log`, 16 MiB at most), danstick's own log
and a `session.json` (which game, variant, machine, gotg rev, when), and a
watcher uploads the bundle to the service when the game ends -- tokens,
keys and anything that looks like a credential redacted first. The server
keeps 250 MiB per person, oldest sessions dropped first; nobody but an admin
reads them (`gotg admin logs`, below). Pending uploads wait in
`~/.local/state/gotg/sessions/` for the next launch.

## Admin

```console
$ gotg admin scan
+ gba      usa.mother_3                         Mother 3
- snes     usa.chrono_trigger                   Chrono Trigger (1 of 1 file(s) gone)
1 added, 1 missing since 2026-08-14T09:11:02Z — 2431 in the catalog
```

```bash
gotg admin invite <name>          # a claim url for one person and device; nix run github:dscrawford/GamesOnTheGo#login -- --claim <url> redeems it
gotg admin tokens                 # every token and when it was last used
gotg admin revoke <name>
gotg admin import [--follow] [--match <re>]   # run the indexer now
gotg admin art warm|status|search|set
gotg admin scan                   # asks the service at the admin URL
gotg admin logs [<user>]          # the session logs people opted into sharing
gotg admin logs get <user> <session> [dir]    # unpack one; logs rm <user> <session> drops it
nix run github:dscrawford/GamesOnTheGo#admin  # the same, with nothing installed
```

Administration can live on its own listener, off the public internet:
`GOTG_ADMIN_PORT`, with `GOTG_ADMIN_URL` (where it is reached) and
`GOTG_PUBLIC_URL` (where claim links point), exposed only on a private
network; the public `/admin` then answers 404 and says where it went.
`<admin url>/admin/` in a browser invites someone (the claim link and the
command they run), lists tokens with their last use, and revokes. The
terminal finds it through `admin_url` in `~/.config/gotg/api.json` or
`GOTG_ADMIN_URL`.

## home-manager

```nix
programs.gotg = {
  enable = true;
  library = inputs.library;      # a flake made from gotg#library
  games = [ "n64.usa.donkey_kong_64" "gamecube.usa.super_smash_bros_melee_rev2" ];
};
```

`inputs.gotg.homeManagerModules.gotg`: the picker and those games on PATH
(`picker = false` leaves the picker off it). The token stays `#login`'s,
0600, never in the store.

## Configuration

| File | Contents |
|---|---|
| `~/.config/gotg/library/` | the library flake: server, catalog pin (`flake.lock`) |
| `~/.config/gotg/api.json` | `{"url": "...", "token": "..."}` |
| `~/.config/gotg/netrc` | the token, for Nix to fetch the catalog |
| `~/.config/gotg/config.json` | `library`: the writable library games are built from |
| `~/.config/gotg/steamgriddb.json` | `{"api_key": "..."}` |
| `~/.config/gotg/video.json` | `{"resolution": "default\|native\|720p\|1080p\|1440p\|4k\|5k\|1x…8x"}` (Dolphin) |
| `~/.config/gotg/controllers.json` | pinned player order |
| `~/.config/gotg/overrides.json` | per-game `target` / `unzip`; replaces `src/client/data/overrides.json`, so copy that to start |
| `config/theme.yaml` | picker colours, window, grid/list shape, timeouts |
| `config/icons.yaml` | controller name or `vendor:product` → icon |
| `config/controllers/*.yaml` | per-controller platforms, danstick layout, control names |

The `config/` files are the checkout's: the packaged picker pins its own
copy, so edits reach a dev shell and nothing else.

| Variable | Effect |
|---|---|
| `GOTG_LIBRARY` | the library to build from (else `library` in config, else the one an app was run from) |
| `GOTG_SERVER` | the server for `install.sh` and `#play` (default `https://gotg.dcraw.net`) |
| `GOTG_KILLSWITCH=0` / `GOTG_KILLSWITCH_OVERLAY=0` | disable the stop combo / its overlay |
| `GOTG_KILLSWITCH_HOLD_MS` | stop-combo hold time (default 3000) |
| `GOTG_MACHINE` / `GOTG_EXTERNAL_DISPLAY` | `deck`, and `1` with a television on: the Deck profile, guessed from DMI and DRM |
| `GOTG_FULLSCREEN` | `1`/`0`; by default a launch is full screen unless started from a terminal |
| `GOTG_SAVES_KEEP` | local save history kept per game (default 3) |
| `GOTG_CONFIG` | picker config directory (dev shell only) |
| `GOTG_UI_FPS=1` / `GOTG_UI_TRACE=<file>` | the picker prints what a frame cost / logs every pad and press |
| `GOTG_ANY_PAD=1` | the picker takes pads danstick has not seated |
| `GOTG_CONFIG_DIR` / `GOTG_STATE_DIR` | `~/.config/gotg` / `~/.local/state/gotg` |
| `GOTG_ADMIN_TOKEN`, `GOTG_INDEX_TOKEN` | admin and importer credentials |
| `GOTG_ART_DIR`, `GOTG_UPSTREAM_RATE` | service: art cache, upstream requests/s |

## Development

```bash
nix develop                         # gotg, gotg-ui, danstick, gotg-test-controllers from the working tree; uv, ruff, shellcheck, bats
nix flake check                     # python-tests, client-tests, ruff, shellcheck, rust; resolver, platforms and catalog agreeing; every environment building
uv lock                             # after changing pyproject.toml
```

The rest -- build caps on a shared desktop, the e2e, the Deck -- is
[CLAUDE.md](CLAUDE.md); the demo above is remade by [docs/demos/](docs/demos/).

## License

[MIT](LICENSE).
