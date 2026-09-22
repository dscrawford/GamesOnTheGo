# Two people pairing at once

Most of this landed while it was being written. `progress` now names the pad
and the seat it is filling towards, a release is a `frac: 0` for that pad, and
`Assigner::tick` sorts the pending holds by when the button went down before
handing out seats. GOTG draws one fill per pad from that, in press order --
`src/ui/gotg_ui/joining.py`.

What is left is three ways an in-flight hold is destroyed by something that
happened to somebody else. The first is the one that stops the feature
working at all, and GOTG's e2e now watches for it:
`test_the_seat_goes_to_whoever_pressed_first`, a strict xfail, which says so
the day it is fixed.

## 1. One person finishing throws away everybody else's hold

Two people hold a button together. The first one's hold completes, a `claim`
goes out -- and the second one's fill dies on the spot. Measured, with two
pads on a real daemon: the earlier press claims player one (press order works,
thank you), and the second claim never comes at all until that person lets go
and starts again.

```rust
// tick_seating, after a successful claim
self.seating.reset();                  // <- Assigner::reset(): everybody
self.refresh_seating(&mut Scan::default());
```

`Seating::reset` and `Seating::refresh` both call `Assigner::reset()`, which
clears `holding` in full. A kernel does not re-send a down edge for a button
that is still down, so nothing repopulates it: the second person's hold is
gone until their thumb comes up and goes back down.

What is wanted: a claim removes that pad's hold and leaves the others alone.
`Assigner::tick` already does exactly that for the pad it claims
(`self.holding.remove(&pad)`), so the `reset()` after it looks like belt and
braces that costs the feature.

The `refresh_seating` that follows is the same story one layer up: the pad set
changes when a claimed pad stops being watched, `refresh` rebuilds it, and
`refresh` resets the assigner wholesale rather than dropping the entries for
pads that have actually gone.

## 2. Changing the hold length throws away every hold in flight

```rust
pub fn set_hold_seconds(&mut self, hold_seconds: f64) {
    self.hold_seconds = hold_seconds;
    self.holding.clear();          // <- everybody, not just this
}
```

`Seating::open` calls it whenever the command carries `hold`, **even when the
number is the same as the one already set**:

```rust
pub fn open(&mut self, seats: u32, hold: Option<f64>) {
    self.open = true;
    self.seats = seats.max(1);
    if let Some(seconds) = hold {
        self.assigner.set_hold_seconds(seconds);
    }
}
```

GOTG sends `seating` with the same `hold` on every screen that listens, which
is how it reaches the launch gate: the picker has seating open, somebody
starts holding a button, the picker execs into the launch, and the gate --
the same session, the same daemon, the same hold length -- opens seating
again. The hold in flight is wiped at that moment. From the sofa the fill
goes back to nothing for no reason, and the only way out is to let go and
start again.

The change that was asked for (dropping holds when the *length* changes, so a
press cannot become a claim because the number moved under it) is right. It
just needs to be a change:

```rust
if (hold_seconds - self.hold_seconds).abs() > f64::EPSILON {
    self.hold_seconds = hold_seconds;
    self.holding.clear();
}
```

## 3. One person finding every seat taken clears everybody else's hold

```rust
let player = padmap_core::announce::next_player(&self.taken_seats());
if player > self.seating.seats() {
    info!("{} held a button but every seat is taken", clean(&pad.name));
    self.seating.reset();          // <- everybody, again
    continue;
}
```

Four seats, all taken, and somebody picks up a spare pad and holds it: every
other hold in the room stops. In a four-player game that is the fifth person
at the party cancelling the fourth person joining, which reads as the pad
being broken.

Two things here:

* **Scope.** Whatever is dropped should be that pad's hold, not the map.
* **Say it.** Nothing is broadcast, so a front-end draws a fill that reaches
  the end and then simply stops, with nothing to put on screen. An `error`,
  or a `progress` with `frac: 1` and no `player`, or a `full` event -- any of
  them can be drawn as "every seat is taken" rather than as nothing.

## 4. Does the daemon already have seating open, and at what length?

The `state` event carries `state`, `slots`, `players`, `following` and the
rest, but not whether seating is listening or how long its hold is. A
front-end that knew could stop re-sending `seating` when nothing has changed
-- which is what walks into 1 above. Two fields on `state` would do it:

```json
{"event": "state", ..., "seating": true, "hold": 1.5}
```

## How these would be checked

* Two pads, one pressed a third of a second before the other, both held all
  the way: two claims, in press order. This is GOTG's
  `test_the_seat_goes_to_whoever_pressed_first`, which gets one claim today.
* `set_hold_seconds(1.5)` twice with a hold in flight: the hold survives the
  second call, and still claims at its own time.
* A `seating` command with the same `hold` as the open one, sent mid-hold:
  same thing, through the socket.
* Seats full, one pad holding to completion and another mid-hold: the second
  pad's fill keeps climbing, and the first gets told something.
