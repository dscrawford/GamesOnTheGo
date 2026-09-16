# Controllers

One file per controller, read by the picker and by the launch-time check. Each
says which platforms are played with it, which padmap layout a capture walks,
which drawing stands for it, and what every control is called.

Per controller rather than per platform: a Wii game is played with a GameCube
pad here, so `gamecube.yaml` claims both. Duplicating sixteen controls into a
`wii.yaml` would be two places to fix one typo.

```yaml
label: GameCube
layout: gamecube          # the padmap layout the capture walks
artwork: gamecube         # src/ui/assets/controllers/<artwork>.svg
players: 4
ares: [SuperFamicom]      # what ares calls it, where ares knows it at all
platforms: [gamecube, wii]

controls:                 # padmap's control id -> what the button says
  a: A (large centre)

anchors:                  # control -> the circle that marks it, when the
  dpup: Up                # drawing does not name its circles after controls
```

## Why `controls` repeats padmap

Because three things describe a GameCube pad — padmap's layout, this file, and
the circles on the drawing — and a screen that walks one while labelling
another points at the wrong button and says nothing about it.
`tests/ui/test_schemes.py` checks this file against the artwork always, and
against padmap's own layouts whenever padmap is checked out beside GOTG.

## `a: B` is not a typo

padmap names a control by where it sits on a modern pad: `a` is the bottom face
button. On a SNES pad that button says **B**. The id is the position and the
label is what is written on the plastic, so `snes.yaml` reads `a: B (bottom)`.

## Adding one

1. Write the file. `layout` has to be a layout padmap actually has —
   `arcade`, `gamecube`, `generic`, `genesis`, `n64`, `ps2`, `snes`, `switch`,
   `wiiu` — or the capture has nothing to walk.
2. `controls` is that layout's control set, verbatim. The test will say so if
   it is not.
3. `artwork` is any SVG in `src/ui/assets/controllers/`. Gaps are allowed: a
   control with no circle is simply not drawn, and the screen says how many of
   how many it showed.
