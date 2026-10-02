# The README's demos

Each `.tape` is a [vhs](https://github.com/charmbracelet/vhs) script; the GIF
beside it is what that script records. The tape is the source of truth — edit
it, re-record, commit both.

```bash
docs/demos/render.sh                            # every tape
docs/demos/render.sh docs/demos/gotg-find.tape  # just one
```

`render.sh` uses `vhs` if it is installed and `nix run nixpkgs#vhs` otherwise,
so nothing has to be added to the dev shell for a one-off re-record.

## What a recording needs

The demos run against a **real library** -- `nix search gotg …` and `nix run
gotg#…`, with `gotg` a flake-registry name for one -- and the recorder says
which: `GOTG_DEMO_RC` names a file the demo shell sources, exporting
`XDG_CONFIG_HOME` (holding `nix/registry.json` that maps `gotg` to the library,
and a `nix/nix.conf` with flakes on), `GOTG_CONFIG_DIR`, `XDG_STATE_HOME` and
`GOTG_LIBRARY`. The one these were last recorded with was a library of a local
copy of the service with a few hundred real catalog rows, so nothing on
camera is anybody's token, and nothing reaches the network: the search and
the list answer from the lock.

```bash
GOTG_DEMO_RC=/tmp/gotg-demo.rc docs/demos/render.sh
```

## Keeping them small

Under ~1 MB each. `Set Framerate 12`, `Set Width`/`Set Height` no larger than
the output needs, and short `Sleep`s. `Set Height` is the one to watch: too
small and the opening lines scroll off the top before the last frame, which is
the frame GitHub shows before the GIF loops.

## One thing that bites

**vhs films a login shell**, which runs whoever's rc file. Here that execs
tmux, which lands a status bar across the top of the recording and eats the
first command typed into it. `render.sh` gives that shell an empty `HOME` and
hands the real one to `demo-shell.sh`, which is the shell actually on camera.
