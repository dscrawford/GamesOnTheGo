# Controllers, made intuitive

The plan agreed on 2026-09-18, and where it stands. The older
[controllers-plan.md](controllers-plan.md) is the layer underneath this one:
danstick as the single controller layer. This is about what it feels like to
use.

## Decisions

Taken with the user, in this order, and not to be relitigated by whoever
picks this up next:

1. **Finish a rebind by holding A.** The wizard already treats a 0.8s hold as
   "skip this control" (danstick `capture.rs`, `SKIP_HOLD_SECONDS`), so the
   finish hold is a longer tier drawn as its own filling ring, not a
   replacement for skip.
2. **Steam's virtual gamepad (28de:11ff) is kept when it is the only pad.**
   Dropped only when the physical pad it mirrors is present too.
3. **Seating stays open while a game runs.** A second player arriving
   mid-level is the point; a stray pad on the sofa being able to take a seat
   is the accepted cost.
4. **Seats are first come, first served, like the Switch.** No reorder
   screen. To change order, a pad leaves and rejoins.
5. **Binding is drawn on the console's diagram, not the player's model.** The
   anchored artwork under `src/ui/assets/controllers/` is the picture; no
   per-model art.

## Phases

| Phase | What | Where | Status |
|---|---|---|---|
| 0 | Measure the Steam Controller double | danstick | partial, see below |
| 1 | The daemon is with the picker and the game for their whole lifetime — and no longer: `ensure-daemon --fresh --follow <pid>` starts it unseated and ends it with the session | GOTG | done |
| 2 | One physical controller is one pad | danstick, one line in GOTG | next |
| 3 | Joining is ambient: hold a button anywhere in the picker | GOTG | after 2 |
| 4 | A way to finish a rebind from the pad | danstick + GOTG | |
| 5 | Request a rebind per player from the picker | GOTG + one danstick request | |

Phase 2 must land before phase 3 is on by default: with ambient seating
open, a doubled controller silently seats one person as two players on the
first held button.

## Phase 0, what was measured

On the desktop, 2026-09-18, with Steam running and no game launched from it:
the Steam Controller Puck (28de:1304) was attached with the controller
itself off, so danstick reported "not reporting as a controller (nothing paired
to it)", and **no 28de:11ff node existed**. Steam creates its virtual gamepad
for an application it launches, not merely by running -- consistent with the
double appearing only when the picker is started from Steam. The Deck's own
launch log for Four Swords shows the kill switch watching both a "Steam Deck
Controller" and a "Steam Virtual Gamepad" under Steam.

Still to record, with the controller on and the picker launched from Steam:
`danstick list --json`, `/dev/input/by-id`, and the HID driver per node. That
decides whether the dedupe in phase 2 drops the mirror when *any* physical
pad is present, or only when the mirrored pad is.

## Phase 1, what changed

- `DANSTICK_SKIP_DAEMON_CHECK` is set only after a successful check, on both
  sides. It used to be set on the way out regardless, so one bad start at the
  picker disabled the check for every game launched from it afterwards.
- "Published" means published by *this* daemon: a marker made before the
  daemon is asked after, and the mappings file must be newer than it. A file
  left behind by a daemon that died earlier in the session no longer counts.
  Only a daemon the call actually started is waited on; one already current
  may have nothing to publish yet, and waiting on that is waiting on a
  button press.
- A keeper outlives the launch the way the kill switch does, polling
  `danstick ensure-daemon --check` and starting a daemon only when there is
  *none*. A daemon running older code is left alone: that is a sync's
  business, and swapping it mid-level would drop the clones the game holds.
  `GOTG_DANSTICK_KEEPER=0` turns it off.
- The picker asks after the daemon again on an interval while it has no
  connection, past the latch, so a daemon that died leaves "danstick not
  running" on screen for seconds rather than for the evening.
