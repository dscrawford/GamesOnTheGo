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

The demos run **against the real machine** — the recorder's own catalog,
installed games and Steam entries. That is deliberate: a mocked library would
drift from the client without anything failing. It means a re-record wants a
machine that has run `gotg login` at least once, and it will show whatever is
installed there, so the `[*]` marks may move.

Nothing reaches the network: `demo.bashrc` pins `GOTG_MANIFEST_MAX_AGE` past
any plausible age, so `list` and `info` answer from `~/.local/state/gotg/manifest.json`
rather than spending ten seconds on a DNS timeout and three warnings before
their first line of output.

## Keeping them small

Under ~1 MB each. `Set Framerate 12`, `Set Width`/`Set Height` no larger than
the output needs, and short `Sleep`s. `Set Height` is the one to watch: too
small and the opening lines scroll off the top before the last frame, which is
the frame GitHub shows before the GIF loops.

## Two things that bite

**Tab completion needs a bash with readline.** Inside `nix develop`, `bash` on
PATH is the minimal build — no `complete`, no `bind` — and a tape recorded
under it shows three commands and no completions, with nothing on screen to say
why. `demo-shell.sh` picks a shell that has them.

**vhs films a login shell**, which runs whoever's rc file. Here that execs
tmux, which lands a status bar across the top of the recording and eats the
first command typed into it. `render.sh` gives that shell an empty `HOME` and
hands the real one to `demo-shell.sh`, which is the shell actually on camera.
