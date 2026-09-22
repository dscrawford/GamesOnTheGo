# The analog stick is missing from the layouts

`padmap-core/data/layouts/gamecube.json` lists sixteen controls and none of
them is the stick everybody plays with:

```
a b x y start dpup dpdown dpleft dpright leftshoulder rightshoulder
righttrigger rightstick_up rightstick_down rightstick_left rightstick_right
```

The C-stick is there four times over; the main stick is not there at all. The
same hole is in `switch.json` and `wiiu.json` (neither has `leftstick_*` or
`rightstick_*`) and in `n64.json`, whose stick is what an N64 game is played
with. Reported in GOTG as "some of the controllers (gamecube) are missing
analog stick options".

## What it costs

The capture wizard walks the layout, so it never asks for the stick, and the
profile it writes has no binding for it. That is fine for a clone -- the axes
are forwarded whether or not anything captured them -- and not fine for
anything reading the profile to find out what the stick is:

* An emulator whose port bindings are written from the capture gets a pad with
  no stick. GOTG writes ares' from its own table for that reason, which works
  only for the consoles that table covers.
* A front-end cannot say which physical axis a console's stick is on. GOTG now
  draws a stick as a ring with a dot in it, read from SDL's standard axis
  order instead -- the drawing has to mark the stick itself, because the
  layout cannot say where it is.
* A pad whose stick is not where SDL thinks it is -- an adapter, something
  exotic -- has no way to be told so.

## What is asked

`leftstick_up`, `leftstick_down`, `leftstick_left`, `leftstick_right` in the
layouts whose console has an analog stick: gamecube, switch, wiiu, n64.
`rightstick_*` as well for switch and wiiu, which have two sticks and list
neither.

Labels in the console's own words, as the existing ones are: "Control stick
up" for a GameCube, "Left stick up" for a Switch.

An axis is not a button, so the wizard's step for one wants to be "push it all
the way" and to capture the direction and the sign -- which is what
`kind: "axis"` with a `value` already records for the C-stick, so the shape
exists.

## How it would be checked

A capture run for a GameCube pad ends with `leftstick_up` bound to an axis in
the written profile, and the four C-stick controls as they are today. GOTG's
side is `tests/ui/test_schemes.py::test_the_controls_are_padmaps_own`, which
holds our labels to padmap's layouts exactly, so it will say so the moment
these land.
