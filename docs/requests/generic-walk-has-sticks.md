# The generic walk has both sticks

## What GOTG saw

Rebinding a Switch Pro pad over Donkey Kong 64 (a PC port, so the `generic`
scope) walked fourteen controls: the faces, the d-pad, Select, Start, the
shoulders and triggers. Neither stick was offered. The drawing has a circle
for every stick direction (`anchor-leftstick_up` ... `anchor-rightstick_right`
in GOTG's `generic.svg`), and none of them lit.

## Why that is not enough

`data/layouts/generic.json` has no stick controls, so a generic walk cannot
bind one. The clone's sticks are then whatever `sdl::stick_fields` fills in
from axis codes 0/1/3/4, which leaves nothing to do for a pad that:

- reports its sticks on other codes, or not rested at centre (refused as a
  trigger), or
- had a trigger captured on a stick's axis in the same walk -- the
  `taken`/`halved` rule then drops that stick, and nobody can put it back.
  One of this desktop's Steam Controller walks has `lefttrigger` on axis 1,
  which is its left stick's Y.

In both cases the stick is dead in the game and the walk offers no way to
fix it. The `switch`, `gamecube`, `wiiu` and `n64` layouts already walk their
sticks as eight halves; the generic pad is the only modern pad that does not.

## What would be enough

- `generic.json` gains the eight halves, `leftstick_up` ... `rightstick_right`,
  labelled `Left stick up` ... `Right stick right`, placed on its two stick
  circles; `kind: "stick"` as the other layouts have them.
- Walked after the buttons, so a person fixing a button is not made to push
  sticks first.
- A stick captured whole (both halves of one axis) keeps its analogue range,
  as it does under `switch`.
- Universal's seeding from the first generic walk still works with the new
  controls present.

GOTG adds the same eight controls to `config/controllers/generic.yaml` when it
pins the answer (`tests/ui/test_schemes.py` checks the two agree).
