# Two people pairing at once

Most of this has landed -- thank you: named progress in press order
(d2000c5), the same hold length no longer resetting holds (26754a9), a full
room stopping one hold rather than everybody's (bc61806), and `state` saying
whether seating listens and for how long (ee56811).

**One item is left, and it is now the one people hit.** Reported from the
sofa this week: "in different screens, if someone claims a controller, it
cancels another controller and they have to hold A again." Four people
picking up pads at a party is the case: each hold after the first is thrown
away by the claim before it. GOTG's e2e watches for it --
`test_the_seat_goes_to_whoever_pressed_first`, a strict xfail, and a
four-pad suite beside it -- and they say so the day it is fixed.

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

### 1b. A press made during a claim's republish is lost

After a claim, `tick_seating` sends the claim, makes every clone again, sends
`state`, and only then reopens the watched pads. A button pressed in between
is queued on the old handle and thrown away. It stays down, so the kernel
sends no new edge, and that person's hold never starts; they have to let go
and press again. On the cluster that window was 0.3-1.5 s, growing with every
seat (see a-join-keeps-everybody-elses-clone.md). So even the workaround,
pressing again, fails if you press too soon. Carrying the held state across
the reopen (read the key state with `EVIOCGKEY` on reopen, and start a hold
for any button already down) would cover both this and a pad switched on
mid-hold. Checked by `test_a_press_made_while_a_claim_republishes_is_not_lost`.

### 1a. Two claims in one tick seat by stale indices

With the reset gone this is the next thing a room of four meets. `tick`
returns `claimed.pads` as indices into the pad list as it was, and the loop
over them calls `refresh_seating` after each claim -- which rebuilds that list
without the pad just seated. The second index in the same tick then points
one pad along: the wrong pad is seated, or `get(index)` misses and a finished
hold is dropped with no claim. Two people who started within one 20 ms tick
of each other is not rare when a room picks up pads on "go".

What is wanted: resolve every claimed index to its pad before the first
refresh (or refresh once, after the loop), so the loop seats pads rather than
positions.

Also seen from here: the picker used to re-send `seating` ~200 ms after every
claim, which at 26754a9 is harmless and before it wiped the room. GOTG no
longer does (`assign.Watch` asks once per connection, after a session, and
when a full room frees a seat).

## 2. Changing the hold length throws away every hold in flight

*Answered -- see above.*

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

*Answered -- see above.*

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

*Answered -- see above.*

The `state` event carries `state`, `slots`, `players`, `following` and the
rest, but not whether seating is listening or how long its hold is. A
front-end that knew could stop re-sending `seating` when nothing has changed
-- which is what walks into 1 above. Two fields on `state` would do it:

```json
{"event": "state", ..., "seating": true, "hold": 1.5}
```

## How these would be checked

GOTG's `tests/e2e/test_pairing.py` is all of the below against a real daemon,
four fake pads at once; each open item is a strict xfail that fails the suite
the day it passes.

* Four pads pressed 0.3 s apart and held: four claims, seats 1-4 in press
  order, no second press. And the evening it was reported, replayed: the
  second pad 1.22 s behind, its fill climbing straight through the first
  claim.
* Four pads pressed in one tick: four different pads seated, each once.
* A pad switched on while another is holding: the hold carries on.
* Two pads, one pressed a third of a second before the other, both held all
  the way: two claims, in press order. This is GOTG's
  `test_the_seat_goes_to_whoever_pressed_first`, which gets one claim today.
* `set_hold_seconds(1.5)` twice with a hold in flight: the hold survives the
  second call, and still claims at its own time.
* A `seating` command with the same `hold` as the open one, sent mid-hold:
  same thing, through the socket.
* Seats full, one pad holding to completion and another mid-hold: the second
  pad's fill keeps climbing, and the first gets told something.
