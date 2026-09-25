# A claim comes at its hold's length, however full the room

## What GOTG is doing

Somebody picks up a pad mid-game and holds A; the bar over the game fills
their drawing and the seat should land the moment the ring closes. The same
for the second, third and fourth person in the picker.

## What happens today (padmap ac0a3dc)

a03dc32 made `state` follow a claim in ~1 ms for every seat -- the answer to
a-join-costs-the-same-however-full.md -- but the cost seems to have moved in
front of the `claim` rather than gone. Four pads held one after another on
the cluster, hold end -> `claim`, two runs:

    seat 1   2    3    4
    15   153  175  237 ms
    15   113  235  196 ms

The first seat is as prompt as ever; every later one is late by roughly
what the room's work costs, and more so the fuller the room. For a person
that is a ring that has visibly closed and a seat that has not landed yet.

## What would be enough

A `claim` within ~150 ms of its hold's length whatever the room holds (GOTG's
typical bound; 400 ms at worst), with the room's files after it as they are
now after `state`.

## How it would be checked

GOTG's `tests/e2e/test_pairing.py::test_a_hold_claims_at_its_length_and_is_published_promptly`
(a strict xfail from ac0a3dc): the median hold -> claim over the four seats
under 150 ms.
