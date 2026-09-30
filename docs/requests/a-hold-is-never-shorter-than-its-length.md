# A hold is never shorter than its length

## What GOTG saw

A Steam Controller takes a seat the moment a button is pressed: no 1.5 s
hold, no filling bar to speak of. The daemon's log from 2026-09-30:

```
16:37:20.553Z seating open: 4 seat(s), 1.50s hold
16:37:21.472Z /dev/hidraw2: controller paired
16:37:22.411Z seating: player 1 <- Steam Controller (hidraw2)
```

0.94 s from the pad being readable at all to its seat, against a 1.5 s hold.
The person pressing says it was immediate. It has also been seen with two
controllers pairing at once.

## Why

`seating.rs` `read` dates each press by the event's own stamp, so a hold
counts from the press rather than from when the loop got to it:

```rust
let at = now - clone::event_age(event);
```

A Triton source makes its events with `InputEvent::new(...)`
(`triton.rs`), which leaves the stamp at zero -- 1970. `event_age` then
answers `MAX_EVENT_AGE`, 2.0 s, for every one of them. Every press from a
Steam Controller is taken as having started two seconds ago, which is past
any hold GOTG asks for, so the first read of a press is a finished hold.

Any source whose events are made rather than read from the kernel has the
same stamp, and any hold shorter than `MAX_EVENT_AGE` is skipped by it.

## What would be enough

- **Events danstick makes carry when they were made**: a Triton source (and
  anything else built with `InputEvent::new`) stamps its events at read time,
  or from the report's own clock mapped onto the wall clock.
- **A stamp that cannot be right is not believed**: `event_age` of an event
  stamped at zero, or from before the source was opened, is 0 -- the event is
  as old as the read that found it -- rather than the 2 s cap.
- **A hold never starts before its pad was watched**: a press is not dated
  earlier than the moment seating opened that pad's source, so no stamp,
  right or wrong, can make a hold shorter than its length.
- A test that holds a Triton pad (or a source with zero-stamped events) for
  less than the hold and sees no claim, and for the hold and sees one, at its
  length.

GOTG's e2e measures the hold through the daemon with evdev fake pads, which
carry real stamps, so it could not see this; it has no hidraw fake.
