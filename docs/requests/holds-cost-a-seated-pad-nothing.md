# Four people holding cost a seated player nothing

## What GOTG is doing

A game is running, one player is seated and playing, and three more pick up
pads and hold A to join. The seated player's presses have to keep reaching
the game inside a frame (16.7 ms) the whole time -- GOTG's e2e has measured
that since the seating scan was throttled
(`seating-costs-the-game-its-input.md`, answered).

## What happens today (padmap e0092be)

The seated player's tail latency went up by about 2 ms between d2000c5 and
e0092be, and now crosses a frame. From GOTG's
`tests/e2e/test_pairing.py::test_four_holds_at_once_cost_a_seated_pad_nothing`
on the cluster (k8s/controllers, same pod, same image apart from the pin),
a press timed from the source pad's write() to the clone's read():

| padmap | quiet p50 | four holding p50 | four holding p95 |
|---|---|---|---|
| d2000c5 (run 1) | 11.80 ms | 11.84 ms | 15.03 ms |
| d2000c5 (run 2) | 11.92 ms | 11.86 ms | 14.81 ms |
| e0092be (run 1) | 11.81 ms | 11.91 ms | **16.85 ms** |
| e0092be (run 2) | 11.81 ms | 11.84 ms | **16.90 ms** |

The typical press is untouched; it is the slow ones, while other pads are
holding, that got slower. Candidates by timing, not measured: the space-bar
reading added in e0092be (every keyboard read while seating is open, which
is the whole game), and the work a claim or hold now does per tick.

## What would be enough

The seated pad's p95 under four holds back under a frame -- where it was at
d2000c5, ~15 ms on this pod -- and the forwarding path not sharing a tick
with anything that grows with the number of keyboards or holds.

## How it would be checked

GOTG's test above (a strict xfail from now until it passes again), run on
the cluster: p95 < 16.7 ms and p50 within 2 ms of the quiet p50.
