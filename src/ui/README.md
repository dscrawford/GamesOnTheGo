# gotg-ui

A grid to pick a game from. `gotg list` is a good way to find a game and a bad
way to browse one; in Big Picture on a Deck there is no terminal at all.

Design and the decisions behind it: [docs/ui-plan.md](../../docs/ui-plan.md).

```bash
nix run .#gotg-ui                      # the grid
nix run .#gotg-ui -- --list            # page one as text, no window
nix run .#gotg-ui -- --platform n64 --search zelda --list
nix run .#gotg-ui -- --region usa --list        # usa plus the region-free world set
nix run .#gotg-ui -- --installed --list         # only what is downloaded here
nix run .#gotg-ui -- --play snes/eur.asterix   # the handoff, without the grid
nix run .#gotg-ui -- --platform snes --refresh # forget cached art and look again
```

| | |
|---|---|
| d-pad / arrows | move; off either side turns the page |
| shoulders / PgUp PgDn | previous and next page |
| A / Enter | open the action menu — Play, Configure, Add to Steam, and Uninstall for a game that is here |
| Start / I | only installed games — the ones badged with a download arrow |
| Y / Tab | next platform |
| Back / shift-Tab / R | next region — world is region-free and rides along (shift-R back) |
| X / `/` | search — Enter applies, Escape cancels |
| pointer | hover selects, click plays; wheel turns the page |
| B / Escape / Q | quit |

## Layout

| File | What it holds | Needs a display |
|---|---|---|
| `catalog.py` | reading the cached catalog, filtering, paging | no |
| `layout.py` | where the ten tiles go | no |
| `grid.py` | where the cursor is, and on which page | no |
| `browser.py` | the platform filter, the search, and the cursor | no |
| `art.py` | the picture cache, misses included | no |
| `fetch.py` | asking the client's own art sources, off the frame loop | no |
| `menu.py` | the action menu: the verbs, and which side has room | no |
| `installed.py` | asking the client what is downloaded here | no |
| `launch.py` | the handoff to `gotg play` | no |
| `app.py` | drawing, and reading a controller | yes |
| `__main__.py` | arguments, and the one error worth printing | no |

Eight of the ten never import pygame, which is the point: a short last page, a
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

## Narrowing it

Ten tiles over 5674 games is 568 pages, so the filter is not a nicety. Y cycles
the platform and X opens a search; both reset the cursor to the first page
rather than clamping it, because a filter is a new question and keeping page
300 across one that has three is how a grid ends up blank with nothing on
screen explaining why. `snes` + `mario` is 23 games in 3 pages.

## Picking

A on a tile opens the menu rather than launching outright: Play Game,
Configure, Add to Steam, beside the tile on whichever side has room, with the
rest of the grid dimmed a step. Play and Configure go through the loader when
the game needs work and then exec the client's verb; Add to Steam runs
`gotg steam add` through the same loader and comes back to the grid.

## State

All four milestones. `--platform` and `--search` still work from the command
line and mean the same thing.
