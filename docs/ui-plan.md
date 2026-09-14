# A grid to pick a game from

`gotg list` is a good way to find a game and a bad way to browse one. On a
Steam Deck in Big Picture there is no terminal, and the shortcuts written by
`gotg steam add` are the whole interface: one entry per game, in a flat list,
found by typing. This is the other half — a screen you can point a stick at.

It is deliberately small. It picks a game and hands off; everything that makes
a game run already exists and is already tested.

## Where it lives

`src/ui/`, beside `src/gotg/` and `src/client/` rather than inside either.

Not `src/gotg/ui`: `pyproject.toml` installs `include = ["gotg*"]`, so anything
under `src/gotg` lands in `gotg-service-env` and ships inside the image that
faces the internet and holds every credential. The service's whole
supply-chain posture is that it is stdlib-only, and a UI wants a toolkit. An
extra would have kept the *dependency* out — that is what `indexer =
["PyYAML"]` does — but the module would still ride along in the image, and the
one thing that keeps that posture true is that there is nothing to argue about.

Not `src/client/`: the client is bash, and its Python (`steam/artwork.py`,
`steam/libretro.py`, `steam/shortcuts.py`) is a set of helpers the shell shells
out to. This is a program, not a helper, and it depends on the client the way a
person does — through the `gotg` command.

So: a third component, its own derivation, `src/ui/README.md` of its own.

## What it does not do

Nothing about how a game runs. It shells out to `gotg play <id>`, exactly as
`cmd-steam.sh` shells out to `python3 artwork.py` today.

Environment resolution, `nix build`, the download with its lock and its resume,
the saves pull, the launcher — all of that is in `src/client/lib/` and covered
by the client test suite. A UI that reimplemented any of it would be a second
copy to keep in step, and the copy nobody runs from a terminal is the one that
rots. `gotg play` already wraps the nix half; wrapping it again is the feature.

## The launch path

One Steam entry, "GamesOnTheGo", which is the UI. Steam launches it, it draws
the grid, and picking a game **execs** `gotg play <id>` — the UI process is
replaced, not kept alive behind the emulator.

That is a choice, and the alternative is worth naming: keeping the UI running
and waiting on the child would return you to the grid when you quit the
emulator, which reads better on a handheld. It is not what this does. Each
game is its own thing — its own environment, its own controller bindings, its
own Steam entry from `gotg steam add` if you want one — and exec means the
process Steam is watching *is* the game, so the overlay, the per-game
controller layout and the playtime all attach to it rather than to a launcher
sitting in front of it.

The per-game shortcuts keep working. This is an additional entry, not a
replacement.

## The grid

Ten games a page, five across and two down.

The catalog is already on disk at `$GOTG_STATE_DIR/manifest.json`, written by
`gotg refresh`, so the UI reads that rather than the network and works offline
exactly as `gotg list` does. No catalog cached is the one error it has to
render: "run gotg refresh".

**Ten a page is right for the screen and wrong for the library.** There are
5674 entries across six platforms, which is 568 pages, and nothing on page 300
is reachable by paging to it. So the model layer is a *filtered* list from the
first commit even though the first commit filters by nothing: a platform filter
(there are six) and a text match over id and title are the two things that make
the grid navigable, and `gotg list` already proves the regex-over-id-title-
platform shape is the right one. Building the pager against an unfiltered list
would mean rewriting it.

## Art

Reuse `src/client/steam/artwork.py` — `SteamGridDBSource`, `LibretroSource` and
the `Artwork` facade that asks them in order. Both already reach SteamGridDB
through the GOTG service, which holds the real key, so the UI needs no
credential of its own.

**The smallest grid we can get.** `best_asset` currently returns the asset's
`url`; every SteamGridDB asset also carries a `thumb`, which is the same
picture at a fraction of the bytes. A tile is a couple of hundred pixels on
screen and there are ten of them, so the full 600×900 is a download and a
decode spent on nothing. `best_asset` grows a `prefer_thumb` argument; the
Steam path keeps taking the full one, because the pictures it writes are the
ones Steam scales into a library page.

Kind is `grids_portrait` (600×900, the box-art shape), which `artwork.py` calls
`tile`.

Cached at `$GOTG_STATE_DIR/ui/art/<platform>/<id>.<ext>` — under the state
directory rather than the store, so it survives a rebuild, same as the catalog
cache and the save archives.

**And now, mostly, from us.** The service keeps the fleet's copy of all of
this (`/art`, warmed by `gotg admin art warm`), so the first place a tile is
asked for is our own endpoint: one request for the whole index at startup,
then a download per picture, and no upstream touched. The sources below stay
as the fallback for a game imported since the last warm. Which also retires
the laziness: what the service holds is pulled in the background when the
picker opens, so paging ahead finds its pictures already on disk.

**Permanently includes the misses.** Most of 5674 games will have no art
anywhere, and a cache that only remembers successes re-asks the network for
every one of them on every launch — which on this library is the difference
between a grid that draws and a grid that hangs. So a miss is recorded too, as
a sidecar naming when it was looked for, and only a deliberate `--refresh`
tries again.

Fetching never happens on the frame loop. A tile draws a placeholder
immediately and a worker fills it in.

## Milestones

1. Read the cached catalog, draw ten placeholder tiles, page with the
   shoulder buttons and the d-pad. No art, no launching. This is the one to put
   on the Deck before anything is built on top of it.
2. Exec `gotg play <id>` on A.
3. Art: cache reads, then the fetcher, then the negative cache. *Done, with
   one correction: the plan said `grids_portrait` and a cartridge box is
   landscape, so libretro files it as `grids`. Both are asked for.*
4. Platform filter and search. *Done — in the grid, and on the command line.*
