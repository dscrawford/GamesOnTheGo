# gotg-ui

A grid to pick a game from. `gotg list` is a good way to find a game and a bad
way to browse one; in Big Picture on a Deck there is no terminal at all.

Design and the decisions behind it: [docs/ui-plan.md](../../docs/ui-plan.md).

```bash
nix run .#gotg-ui                      # the grid
nix run .#gotg-ui -- --list            # page one as text, no window
nix run .#gotg-ui -- --platform n64 --search zelda --list
nix run .#gotg-ui -- --play snes/eur.asterix   # the handoff, without the grid
nix run .#gotg-ui -- --platform snes --refresh # forget cached art and look again
```

| | |
|---|---|
| d-pad / arrows | move; off either side turns the page |
| shoulders / PgUp PgDn | previous and next page |
| A / Enter | play it — this process becomes the game |
| B / Escape / Q | quit |

## Layout

| File | What it holds | Needs a display |
|---|---|---|
| `catalog.py` | reading the cached catalog, filtering, paging | no |
| `layout.py` | where the ten tiles go | no |
| `grid.py` | where the cursor is, and on which page | no |
| `art.py` | the picture cache, misses included | no |
| `fetch.py` | asking the client's own art sources, off the frame loop | no |
| `launch.py` | the handoff to `gotg play` | no |
| `app.py` | drawing, and reading a controller | yes |
| `__main__.py` | arguments, and the one error worth printing | no |

Six of the eight never import pygame, which is the point: a short last page, a
filter that emptied the screen and a stick held against the right-hand column
are all cases a screenshot will not show you, and all of them are covered in
`tests/ui/` without a display.

## What it does not do

Anything about how a game runs. It execs `gotg play <platform>/<id>`, as
`cmd-steam.sh` shells out to `python3 artwork.py` today. Environment
resolution, `nix build`, the download with its lock and its resume, the saves
pull — all of that is in `src/client/lib/` and covered by the client suite. A
second copy would be one to keep in step, and the copy nobody runs from a
terminal is the one that rots.

## Where it lives, and why not elsewhere

Beside `src/gotg` and `src/client` rather than inside either. `pyproject.toml`
installs `include = ["gotg*"]`, so anything under `src/gotg` ships inside the
image that faces the internet and holds every credential — stdlib-only is that
image's whole supply-chain posture, and a picker wants a toolkit. `src/client`
is bash, and its Python is helpers the shell calls; this is a program.

The id is always qualified by platform. 222 ids in a real library are on more
than one — `eur.asterix` is on gb, nes and snes — and the grid is the one thing
that knows which tile the cursor was on.

## Art

From the client's own sources — `artwork.py` on `PYTHONPATH`, so the grid asks
SteamGridDB and libretro-thumbnails exactly as `gotg steam art` does, through
the service that holds the real key. Cached under
`$GOTG_STATE_DIR/ui/art/<platform>/<id>.<ext>`, keyed on platform *and* id
because 222 ids are on more than one.

Two things that are not obvious:

**Misses are cached too.** Most of a real library has no art anywhere, and a
cache that only remembered successes would ask the network about thousands of
games on every launch. A miss is a `.miss` sidecar, nothing expires, and
`--refresh` is the only way back to the network.

**Portrait first, then the wide capsule.** libretro files box art by its shape,
and a cartridge box is landscape — asking only for `grids_portrait` finds
nothing for most of a cartridge library. Tiles fit the picture rather than
stretching it, since art arrives in both shapes.

## State

Milestones one to three. What is left is the platform filter and search in the
grid itself; both already exist on the command line.
