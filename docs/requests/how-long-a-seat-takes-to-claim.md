# How long a hold has to be before it claims a seat

`padmap-core/src/assign.rs` sets `HOLD_SECONDS = 0.25` and nothing can change
it. A quarter of a second is too short for the front of a launch: a person
picking a controller up, or resting a thumb on it while reading the screen,
takes a seat without meaning to, and on a sofa with four pads on it the wrong
one ends up as player one. Asked for in GOTG as "time to pair a controller is
too fast, can we make it 1.5s?".

## What is asked

A way to say how long the hold is, with the 0.25 s default kept for whoever
does not ask. Either shape works:

```json
{"cmd": "seating", "open": true, "players": 4, "hold": 1.5}
```

or an environment variable read at start, beside the two GOTG already sets:

```
PADMAP_HOLD_SECONDS=1.5
```

The command field is the better of the two, because the right length differs
by screen: a launch gate wants deliberation, and a mid-game join wants to be
quick. If only one is possible, the variable is enough — GOTG starts the
daemon itself.

`progress` already reports the fraction, so a front-end drawing the fill needs
no other change; it is the same reveal either way, just slower.

## Why not do it in the front-end

GOTG cannot. The hold is timed inside the daemon, against the device, and the
first the picker hears of it is the `claim` that has already happened. The one
thing a front-end could do is watch raw pads through SDL and keep `seating`
shut until it has seen a long enough hold itself — which means acting on input
from an unseated pad, the one thing GOTG's controller rule forbids, and it
would still be a quarter-second race once seating opened.

## How it would be checked

A `seating` opened with `hold: 1.5`, a pad held for one second: no `claim`.
The same pad held for two: a `claim`, and `progress` events that reach 1.0 at
about 1.5 s rather than 0.25 s.
