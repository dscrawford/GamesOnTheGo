# Two people pairing at once

Two controllers held at the same time are indistinguishable from one, and the
seat each ends up with does not follow the order they were pressed in.

Asked for in GOTG as: *"if 2+ controllers are pressing A at the same time to
pair, both controller icons should appear in the order of who pressed A
first. If someone lets go, they lose their place."*

## What the daemon has, and what it says

`Assigner::tick` already knows both things:

```rust
out.progress.push((pad, (elapsed / self.hold_seconds).clamp(0.0, 1.0)));
```

`Tick.progress` is a list of `(pad, fraction)`. The broadcast throws the pad
away:

```rust
// padmap-daemon/src/server.rs, tick_seating
for (index, fraction) in &claimed.progress {
    if let Some(pad) = self.seating.pads().get(*index) {
        let _ = pad;                       // <- the pad, unused
        self.broadcast(&events::progress(*fraction));
    }
}
```

So two pads in flight broadcast two `{"event":"progress","frac":…}` events
per tick, alternating, and a front-end sees one fill jumping between two
values. In a session (`tick_session`) it is worse: the fractions are folded
with `f64::max`, so the furthest-along hold is the only one that exists.

## What is asked

**1. `progress` names the pad.** The same shape a `claim` uses, so a
front-end can key on it:

```json
{"event": "progress", "frac": 0.42, "node": "/dev/input/event9",
 "name": "Xbox Wireless Controller", "player": 2}
```

`frac` stays where it is for anything already reading it. `player` is the
seat this hold would take if it completed now -- which is what makes a
front-end able to draw the pad filling in *where it will sit*.

**2. A release is said out loud.** A front-end cannot tell "player two let
go" from "player two's reading is late" when the readings are anonymous, and
with two pads it cannot tell which of them stopped. One event, or a final
`frac: 0` for that pad:

```json
{"event": "progress", "frac": 0.0, "node": "/dev/input/event9"}
```

**3. Seats go in the order the buttons went down.** Today
`Assigner::tick` walks `self.holding`, which is a `BTreeMap` keyed by pad
index, so two holds finishing in the same tick are ordered by *device number*
-- which is the order they were plugged in, not the order anybody pressed.
Sorting the completions by their `started` timestamp is the whole change, and
it is what "who pressed first" means to a person on a sofa.

**4. Letting go loses the place, and the next in line is promoted.** That
falls out of 3 if nothing is reserved at press time: a hold that does not
finish claims nothing, and the seats go to whoever does finish, earliest
press first. Worth saying explicitly in the docs, because the alternative
design -- reserving a seat when the button goes down -- is what a front-end
would have to do if the daemon did not, and two front-ends would disagree.

## The scenarios that matter

Ordinary: two pads start together, both finish; seats 1 and 2, in press
order.

* One lets go at 80%: the other takes seat 1, not seat 2.
* Both finish in the same tick: press order decides, not pad index.
* Three holders, two free seats: the two earliest presses get them and the
  third keeps filling, claims nothing, and is told so.
* A pad is unplugged mid-hold: its fill goes away; the others are unaffected.
* A pad that already holds a seat presses again: no fill, no claim (this is
  today's behaviour and should stay -- holding B to block in a fighting game
  must not reseat anybody).
* A hold that began before `seating` opened: either it is measured from the
  open, or it is ignored until the button is released. Not a claim the
  instant seating opens, which is what a front-end would look like it had
  done by accident.

## How GOTG will draw it

One filling controller per pad, side by side, in press order, each in the
colour of the seat it would take; a release takes that one off. Until this
lands the picker draws a single anonymous fill, which is what it has always
done and is wrong for two people at once.
