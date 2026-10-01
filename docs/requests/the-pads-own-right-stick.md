# The pad's own right stick is heard

## What GOTG saw

The overlay's menu draws the game's controller for everybody to try their
buttons on; the menu's owner is heard through `focus`. With a Steam
Controller (`28de:1304`, Triton) as the owner, the left stick's dot moved and
the right stick's never did: no `focus` event with `"stick": "right"` arrives.
The same pad's right stick works in the game.

## Why

`focus` and `native` hear a pad through `own_translator`: the walk under the
named scope, else universal, else the kernel's convention. This pad's
universal is its first generic walk, from before the generic layout had
sticks -- 14 controls, triggers on axes 2 and 5, no stick halves. A
`Translator` built from a non-empty walk carries only the left stick by code
(`carried_axes`: "the left stick, which no capture covers"). Nothing binds
`ABS_RX`/`ABS_RY`, so the right stick is translated to nothing, and the menu
never hears it. The game's clone follows `console:generic`, which has the 22
controls with both sticks, so the game does.

b5aaf6d's note gives the reason only the left stick is carried for the clone:
an adapter's `ABS_RX` can be its trigger. That is about a clone the game
reads; it leaves the pad's own controls without a right stick on every pad
whose universal walk predates the sticks, which is every pad walked before
2026-09-30.

## What would be enough

- The pad's own controls (`focus`, `native`) carry a right stick that no walk
  binds, the way the left one is carried, where `ABS_RX`/`ABS_RY` rest centred
  (`rests_centred`) -- a trigger rests at an end and is not taken for one.
- `focus` says it as `{"stick": "right", "x", "y"}` like the left.
- A test: a Triton source with a universal walk of the 14 generic buttons and
  no stick halves, focused, moves its right stick and is heard.
