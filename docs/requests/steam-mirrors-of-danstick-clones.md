# Steam's mirrors of danstick's own clones must never take a seat

## What GOTG is doing

A Deck in Game Mode, fixed slots (DANSTICK_SLOTS=fixed, xbox360-numbered),
the Deck's own controls and an Xbox Wireless Controller over Bluetooth.
Somebody holds A on the Xbox pad to take a seat.

## What happens today (danstick 572f90a)

One hold seats three players. The Deck's danstick.log, 2026-09-25:

    19:06:06.415 4 fixed slot(s) standing as xbox360-numbered: [1, 2, 3, 4]
    19:06:07.964 Microsoft X-Box 360 pad 2 attached ...
    19:06:08.716 Microsoft X-Box 360 pad 3 attached ...
    19:06:08.890 Microsoft X-Box 360 pad 4 attached ...
    19:06:09.376 seating: player 1 <- Microsoft X-Box 360 pad 0 (event10)
    19:06:40.894 seating: player 2 <- Xbox Wireless Controller (event11)
    19:06:41.042 seating: player 3 <- Microsoft X-Box 360 pad 1 (event18)
    19:07:00.474 seating: player 4 <- Microsoft X-Box 360 pad 3 (event24)
    19:07:03.534 Microsoft X-Box 360 pad 2 held a button but every seat is taken

Steam Input wraps every Xbox 360 pad it sees in a virtual gamepad
(28de:11ff), and under fixed slots danstick's four clones are 360 pads
(045e:028e) standing from the start -- so Steam made pads 1-4 for them
within two seconds. 871a4f7 keeps `max(mirrors - readable pads, Decks)`
Steam mirrors: five mirrors less one readable pad left four, all offered.
The Xbox pad's hold seated player 2; its presses went to slot 2's clone,
through Steam's mirror of that clone, and 150 ms later the same hold seated
player 3 from the mirror. Any seated player holding A -- to ready up at the
door, say -- seats another the same way.

Under mirror identity the Deck alone never looped, because a clone of a
Steam virtual pad is 28de:11ff and Steam does not wrap its own id; fixed
slots made every clone a 360 pad, which it does.

## What would be enough

Count danstick's own clones among the controllers Steam mirrors: keep
`max(mirrors - readable pads - own clones Steam can see, Decks)`, lowest
Steam slot first (the built-in is opened first, and a clone's mirror comes
after it). Better still if a mirror can be tied to its source -- Steam
virtual pads carry no phys or uniq, but a mirror whose every input follows
one of danstick's clones is that clone -- but the count alone stops the loop.

## How it would be checked

On the Deck in Game Mode, fixed slots, Deck + Xbox pad: holding A on the Xbox
pad seats exactly one player; holding A again on a seated pad seats nobody;
`danstick list` offers the Deck's mirror and no mirror of a clone. A journey
with four STEAM_VIRTUAL fixtures beside four fixed slots and one XBOX_360
covers the count off the Deck.
