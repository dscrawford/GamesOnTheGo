# Games as Nix outputs

Status: on `feat/nix-games` -- phases 0 to 6 done; 5 checked on a real Deck,
6's fresh Deck from nothing still to do. A game is played only from its
library: `gotg play` with none configured says how to make one, and the
installer makes one.

Verified, against a local copy of the service on 127.0.0.1 (nothing reached
the real server): a library flake fetched the catalog through a netrc and
updated its pin with `nix flake update catalog`; DK64, Melee and Four Swords
Adventures 2p (its split-screen sway) ran from their outputs' specs under
`gotg qa --spec` and passed. Pretending to be a Deck on this NVIDIA desktop
fails as it does on master: nixpkgs' mesa drives no NVIDIA card. On the real
Deck -- the library flake pinned to this branch, its catalog through a netrc
from the same local service over an ssh tunnel, isolated config and state --
DK64's output built in ~14 minutes, fetched and rooted the mesa its
environment names (no /run/opengl-driver there), and passed `gotg qa --spec`
on the Deck's own GPU. And on 2026-10-02, by hand on this desktop:
`nix run /tmp/e2e/lib#n64.usa.donkey_kong_64` against that local service,
with no `gotg` typed at all -- the shape phase 7 keeps.

## What it is

```bash
nix run gotg#n64.usa.donkey_kong_64                 # platform.region.game
nix run gotg#usa.donkey_kong_64                     # when the id is on one platform only
nix run gotg#n64.usa.legend_of_zelda_ocarina_of_time_rev2.rando   # a variant
nix run gotg#gb.usa.tetris_2                        # usa.tetris_2 is on gb and nes
```

A game is a flake output: a small app that runs `gotg launch --spec` with that game's
spec baked in, its environment (emulator or port, settings, mods, recipe) a
Nix dependency of it. Evaluating or building one game touches that game alone.

Everything a launch does today still happens, the same way: firmware and
keys, saves pulled on the way in and pushed on the way out, danstick and the
controllers, the overlay and its menu, the session that lets a save be loaded
mid-game. What changes is who decides what runs: Nix, from the attribute,
instead of the client's catalog lookup and its GC roots.

`gotg.usa.donkey_kong_64` is not flake syntax; `gotg#...` is, with `gotg` a
registry name. Dots in an id are attribute nesting, which is why the platform
leads: ids repeat across platforms and an attribute path cannot carry the
`platform/id` slash.

## Decisions

1. **Game files stay out of the store.** `/nix/store` is readable by every
   user, would be copied into any cache, is garbage-collected, and a 17 GiB
   Switch dump would be copied into it whole. The launcher fetches into
   `~/Games` on first run, as `gotg install` does, verified against the
   catalog's sha256. The catalog, the environment, the launcher and every
   argument are Nix; the bytes and the state are runtime.
2. **The same launch, set up the Nix way.** Firmware, keys, saves, controllers,
   danstick, the overlay and the session are the launcher's, unchanged.
3. **Per-game outputs**, rooted for the couch: the picker and Steam launch a
   game's build kept as a GC root, so a launch neither evaluates a flake nor
   needs the network; `gotg update` rebuilds the games that have
   roots. That is sync, per game.

## Two inputs: the tools and the catalog

- **`gotg`** is this repository: environments, launcher, overlay, picker,
  `lib.mkLibrary`.
- **The catalog** is the service's `GET /catalog`, which the website updates
  regularly, as a flake input pinned by `flake.lock` (`nix flake update
  catalog` refreshes it). Its `files_url` names where the bytes are, and the
  library names the server, so the upstream is specified in Nix:

```nix
# a library flake (nix flake init -t gotg#library)
{
  inputs.gotg.url = "git+ssh://git@github.com/dscrawford/GamesOnTheGo";
  inputs.catalog = {
    url = "file+https://gotg.dcraw.net/catalog";
    flake = false;
  };
  outputs = { gotg, catalog, ... }:
    gotg.lib.mkLibrary {
      server = "https://gotg.dcraw.net";
      catalog = catalog;
    };
}
```

The catalog is private, and a flake input authenticates only through Nix's
`netrc-file`, which sends HTTP Basic: the service accepts the token as a
Basic-auth password on `GET /catalog`, and nowhere else. `gotg login` keeps
`~/.config/gotg/netrc` (0600) beside `api.json` and points the user's Nix at
it -- a user setting is enough, the fetch is the evaluator's, not the
daemon's -- unless Nix already reads a netrc, whose other credentials a second
one would hide: then it says the line to add. Tokens never enter the store.

`nix flake init -t gotg#library` writes that flake.

The picker still searches the catalog: the cached copy the launcher keeps
fresh, so a game added on the website shows before the lock is updated;
choosing one the lock does not have yet updates the `catalog` input first.

## What becomes of each client job

| Today | After |
|---|---|
| `refresh`, `manifest.json` | the `catalog` input, parsed by `gotg.lib.catalog`; the launcher's cache for the picker |
| id → environment (`env_attr`) | the same rule in Nix; the attribute is the resolution |
| `env_build`, `env_ensure`, `sync`, environment GC roots | gone from a launch: the environment is a dependency |
| `install`: download, verify, recipe | `gotg launch`, from the game's spec |
| firmware, keys, versions, DLC | `gotg launch` |
| saves sync, `saves list`/`restore` | `gotg launch` and its library |
| danstick, bindings, overlay, session | `gotg launch`, unchanged |
| `login`, `admin`, `saves`, `qa`, `controllers` | `nix run gotg#gotg -- <command>` |
| `play`, `install`, `sync`, `refresh` | `play`/`install` build the library's output; `sync` is `update`; `refresh` is the picker's cache |

## Phases

| # | Work | Verified by |
|---|---|---|
| 0 | this document | -- |
| 1 | catalog as Nix data: Basic auth on `/catalog`; `gotg.lib.catalog`; id → environment in Nix | service tests; `lib.runTests`; Nix and bash resolvers agree on a fixture catalog |
| 2 | `gotg launch --spec`: the play path from a JSON spec, minus environment building; `gotg play` shares it (`play_launch`) | bats moved over; QA through `gotg play` |
| 3 | per-game outputs from `mkLibrary`, variants, unique-id aliases, laziness | an eval check that one game forces no other; dry runs; QA through an attribute |
| 4 | the library flake template, registry, netrc for the catalog | a second library against the test service |
| 5 | `gotg qa --spec`; `gotg play` through a configured library (so the picker and Steam), per-game roots, `gotg update` | picker tests; Steam bats; controller e2e; QA desktop and `--machine deck`; the Deck |
| 6 | retire the old path: environment building, its roots and `sync` (now `update`) gone from the client; `play`/`install` kept as the library's front door; the installer makes a library | all checks; a fresh Deck from nothing |
| 7 | no client: the launcher a hidden dependency, the picker the only thing a person runs (below) | all checks; the Deck with no `gotg` in its profile |

## Phase 7: no client

Decided 2026-10-02. Nix cannot do the runtime -- something fetches the bytes
into `~/Games`, pulls and pushes saves, starts danstick, writes the
emulator's bindings, starts the overlay and runs the game -- so a launcher
exists. What goes is the client as a thing a person installs, upgrades and
types to. After phase 6 the launcher is already a dependency of every game
output (`exec gotg launch --spec`); the rest of `gotg` is a front door Nix
and the picker replace.

```bash
nix run library#n64.usa.donkey_kong_64   # a game; the launcher inside it, invisible
nix run gotg#login                        # token + netrc, once per machine
nix run gotg#ui                           # the picker
nix run library#steam                     # Steam shortcuts for the picker and the games
```

| Today | After |
|---|---|
| `gotg launch`: download, verify, recipe, firmware, keys, versions, saves sync, danstick, bindings, overlay, session, the restart for a save | stays as `gotg-launch`, bash: a per-game dependency, never on PATH, never in a profile |
| `saves list`/`check`/`restore`, which the overlay and picker ask at runtime | stays, inside the launcher |
| `login`, `admin`, `qa` | each its own flake app, bash |
| `steam add`/`picker`, artwork | `library#steam`, and a home-manager module for the declarative machine |
| the picker's catalog: `manifest.json`, `refresh` | the library's pinned `catalog` input, read from the store -- one source of truth; a game newer than the lock is `nix flake update catalog`, which the picker runs |
| the picker's Install (A on a game) | `library-play.sh`'s build-and-root moves into the picker: `nix build library#x -o games/x`, then run it |
| the picker's questions: ready, installed, saves, storage | file reads in Python, or the launcher's `saves`; no shelling out to a general `gotg` |
| `configure storage` (`~/Games`, an SD card) | a library setting, `gamesDir` |
| the `gotg` profile entry, `update`, `library`, `install`, `play`, `list`, `info`, `download`, `uninstall`, `versions`, `complete`, `configure`, `controllers` | gone; the installer is Nix, uinput and the Steam entry |

Where "press A to install" lives is the one real decision: it is imperative
by nature, so it is the picker's, with a `programs.gotg.games = [...]` module
beside it for a NixOS or home-manager machine; both make the same roots.

Steps, on `feat/no-client`:

| # | Work | Verified by |
|---|---|---|
| 7.1 | the catalog is the library's pin: `#catalog` on a library, the client's cache a root to it, `refresh` moves the pin | bats; the local library |
| 7.2 | apps: `gotg#{login,admin,qa,controllers,ui}`, a library's `#{ui,steam,update,login}` with the library baked in | bats; `nix run` each |
| 7.3 | retire what only a person typed: `list`, `info`, `download`, `version`, `sync`; usage says what the launcher is | bats; docs |
| 7.4 | the installer: Nix, uinput, a library, the Steam entry; no profile; `uninstall.sh` to match | install.bats; a fresh Deck |
| 7.5 | docs: README, AGENTS, install.md; `.envrc`'s rev check reads the library's pin | -- |
| 7.6 | `programs.gotg` home-manager module | a NixOS eval check |

Sequencing: `feat/nix-games` merges first -- checked on the Deck, and
`launch --spec` is the seam this cut runs along -- then phase 7 on its own
branch. About phase 6's size: most of the bash stays (it is the launcher),
the bats suite loses the retired commands, the picker grows.

## Watch

- Evaluation with ~9.5k games: values lazy, one game's evaluation measured.
- Offline: only rooted games launch without the network.
- A launcher change rebuilds every game's tiny app; environments rebuild only
  when they change.
