# A join costs the same however full the room is

## What GOTG is doing

Somebody joining a game in progress should be seated as fast as the first
person was: the bar over the game shows their drawing filling in, and the
seat should land the moment the hold ends.

## What happens today (padmap e0092be)

Much better than it was -- the fourth seat's `state` used to come 1.19 s
after the first's and now 206 ms -- but it still grows with every seat
already taken. On the cluster, claim to `state` for seats one to four:
252, 265, 379, 458 ms; claim to clone: 1 ms each. So publishing is instant
and something after it scales with the room -- 11182f3 says a join writes
the room's files, and that may be most of it.

It shows up for people as a seat that lands late: four pads pressed 0.3 s
apart and all held are seated in the right order, but the second of them
603 ms after its own hold ended, because its claim waited behind the
first's writing.

## What would be enough

A claim's `state` within ~100 ms of the first seat's whatever the room holds
(GOTG's bound), and a claim that lands at its own hold's length rather than
after the previous one's files: write only what the new seat changes, or send
`claim`/`state` first and write the files after.

## How it would be checked

GOTG's `tests/e2e/test_pairing.py::test_a_join_costs_the_same_however_full_the_room`
and `test_four_pads_pressed_apart_take_seats_one_to_four_in_press_order`
(both strict xfails today): the fourth seat's `state` within 100 ms of the
first's, and every staggered claim within 0.4 s of its own hold.
