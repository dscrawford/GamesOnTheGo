# config

Everything somebody is expected to edit, in one place, read by one loader.

| file | what it decides |
| --- | --- |
| `theme.yaml` | colours, window, the grid's shape, how long things wait |
| `icons.yaml` | which drawing a controller's reported name means |
| `controllers/` | one file per controller — see its own README |

`gotg_ui.config` reads the lot into a dictionary, once:

```python
config.get("theme.grid.columns")            # 5
config.get("controllers.gamecube.layout")   # "gamecube"
config.colour("theme.colours.text", (0, 0, 0))
```

A file becomes a key named after it; a directory becomes a key holding one
entry per file. `GOTG_CONFIG` says where this directory is — the wrapper sets
it, and a checkout run in place finds the tree without it.

Missing is never an error. Every call site passes a default, so a config
directory that is absent entirely leaves the picker looking exactly as it did
when these were constants in Python. A file too broken to parse costs its own
section and nothing else.

## Why this exists

`BACKGROUND` was defined in three modules — `(18, 18, 20)` in two of them and
`(18, 18, 22)` in the third, close enough that nobody noticed and different
enough to be wrong. The same grey was `TEXT` in one file and `LABEL` in
another. Values written down once cannot drift like that.

## What is deliberately *not* here

Paths that describe where another program puts its files:
`manifest.json`, `env-<platform>/share/gotg/pads.json`, `ui/art`,
`$XDG_RUNTIME_DIR/padmap/padmap.sock`. They are not preferences — they are the
layout of something else's output, and a copy of it in a YAML file is a second
thing to keep in step, wrong only at runtime and only on the machine where it
matters. They stay next to the code that reads them, derived from
`GOTG_STATE_DIR` and the XDG variables as they already were.

The emulator settings under `src/client/env/` are not here either. Nix has no
YAML reader, so a table there would have to be parsed at build time by
something, and those files are already the one place each emulator's knobs are
written down.
