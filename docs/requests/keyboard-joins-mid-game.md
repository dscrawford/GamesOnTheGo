# The keyboard can take a seat while a game is running

## What GOTG is doing

A person with a keyboard and no pad holds the space bar to take a seat. On
the picker's grid and at the launch gate the picker times that hold itself
and sends `seat_keyboard` when it completes. Pads can join at any time,
mid-game included -- seating stays open, and GOTG draws a bar over the game
while a pad holds to join (from padmap's `progress` and `claim` events). The
keyboard should be able to do the same.

## What happens today

Once the game starts nothing is listening for the space bar. The picker has
exec'd into the game, and padmap never reads a keyboard ("the keyboard stays
the compositor's, and padmap never sees its keys", `docs/EVENTS.md`). So
`seat_keyboard` is never sent from inside a game, and there is no `progress`
for the overlay to draw: somebody holding space mid-game sees nothing happen
and gets no seat.

## What would be enough

1. **While seating is open, a held space bar on any keyboard seats the
   keyboard**, exactly as `seat_keyboard` does, after the same `hold` length
   the pads use (GOTG asks for 1.5 s). Only the space bar: nothing else on the
   keyboard needs to be read.
2. **It reads, it does not grab.** The game sees the space bar too, and that
   is fine -- a character may jump while somebody joins; GOTG is not worried
   about that overlap. Grabbing the keyboard would take it from the game and
   from the desktop, which is worse.
3. **Say it as a pad would**, so a front-end can draw the keyboard arriving
   (this is the part that matters most to GOTG):
   - `progress` while the hold fills, and `frac: 0` when it is let go, with
     `"name": "Keyboard"` (or "Keyboard and Mouse", see
     `keyboard-and-mouse-seat.md`), `"node": ""` or the keyboard's own node,
     and the `player` it is filling towards -- the same fields a pad's hold
     carries;
   - then the `claim` and `state` `seat_keyboard` already sends.
   GOTG's overlay keys a hold by node, else by name, so a hold with an empty
   node and the keyboard's name is drawn as the keyboard (its keyboard-and-
   mouse drawing) filling in, and its `claim` as the seat taken.
4. **Not twice.** With a keyboard already seated, holding space does nothing
   more (as `seat_keyboard` is refused today). A keyboard that has a seat
   types into the game as it always did.

Once padmap does this the picker can stop timing the hold itself and leave it
to padmap everywhere -- one hold, one place, the same on the grid, at the
gate and in a game.

## How it would be checked

- Seating open, a uinput keyboard holds KEY_SPACE for the hold length:
  `progress` events naming the keyboard climb to 1, then `claim` with the
  keyboard's seat, then `state` with `"keyboard": true`.
- Let go half way: `progress` with `frac: 0`, no claim.
- Holding again with the keyboard seated: no `progress`, no second seat.
- The key still reaches a window that has focus while it is held (read, not
  grabbed).
