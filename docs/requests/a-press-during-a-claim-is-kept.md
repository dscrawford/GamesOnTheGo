# A press made while a claim is being handled is kept

## What GOTG is doing

Four people pick up pads and hold A a moment apart; each should be seated in
the order they pressed, by one hold each. Since bf6606d a claim no longer
throws everybody else's hold away -- thank you -- and the room now seats in
order.

## What happens today (padmap e0092be)

A press that lands while a claim is being handled is still lost. After a
claim, padmap publishes, writes the room's files, sends `state`, and then
reopens the pads it watches; a button that went down in between is queued on
the closed descriptor and gone. It stays down, the kernel sends no new edge,
and that person's hold never starts until they let go and press again.

Measured on the cluster (k8s/controllers): two seated, a third claims, a
fourth pressed 50 ms after that claim -- no `progress` for the fourth pad at
all, however long it is held. The window is the claim-to-`state` time below,
250-460 ms on the pod, so it is not rare when a room picks up pads on "go".

## What would be enough

A hold that was already down when a pad's descriptor is (re)opened counts
from the reopen: read the key state on open (EVIOCGKEY) and start a hold for
any button already down -- or keep the descriptors open across the claim so
nothing is queued on a closed one. Either way a thumb that never lifted is
still a hold. (padmap's note that a space bar held before it starts reading is
invisible "on purpose" is the same trade; for a pad already being watched
the reopen is padmap's own doing, not a hold that predates it.)

## How it would be checked

GOTG's `tests/e2e/test_pairing.py::test_a_press_made_while_a_claim_republishes_is_not_lost`
(strict xfail today): the fourth pad, pressed 50 ms after the third claims,
is seated by that one hold.
