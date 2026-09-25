# Fixed slots: controllers exist before anybody sits in them

## What GOTG is doing

Any controller should be able to join any game at any point -- picked up
mid-level, in whatever emulator -- without GOTG knowing each emulator's
controller API. The simplest shape for that: padmap always publishes a fixed
set of virtual controllers, identical from launch to launch, and a physical
pad that takes a seat starts driving one. The emulators are configured once,
for `padmap Player 1..N`, and never again.

This becomes GOTG's default. The current event-based seats stay, chosen per
game where a game needs them.

## What happens today (padmap 871a4f7)

A seat's clone is made when the seat is claimed, mirroring the pad behind it.
So a slot cannot exist before somebody sits in it; emulator bindings written
at launch cover only the seats taken by then; and `exec`'s sandbox shows the
game only the clones that existed when it started. A seat taken mid-game
reaches nothing (Four Swords, and plausibly Ryujinx on the Deck this week).

`exec --reserve N` is most of the answer already, but:

- it requires the `xbox360` identity, under which every clone shares one
  GUID -- and Ryujinx blanks the name CRC to make its device id, so GOTG
  refuses it there;
- it lasts one launch, and is given back after;
- `unseat` destroys a reserved clone, so a player leaving mid-game takes the
  slot with them for the rest of that game.

## What would be enough

A slots mode, with every part of it configurable (daemon flag, env var, and
the `seating`/an equivalent command, whichever fits padmap's shape):

| Setting | Default | Alternatives |
|---|---|---|
| **mode** | `fixed`: N clones made when the daemon starts, kept for its whole life | `on-demand`: today's behaviour, a clone per claim |
| **slots** | 4 | 1-16 |
| **identity** | one known pad for every slot (360 layout), with a **distinct GUID per slot** -- e.g. the version field or product varied by slot -- so Ryujinx and anything else that ignores the name CRC tells them apart | `mirror` (today's default), `padmap`, and the current same-GUID `xbox360` |
| **on leave** | the slot stays and goes quiet; the next hold may take it | `destroy`: today's `unseat` |
| **layout** | physical buttons mapped to the slot's layout by position (as reserved seats do) | by label |

In `fixed` mode an empty slot is a connected pad that sends nothing, as a
reserved seat is today; `state` lists them all, seated or not, and a claim
fills the lowest free slot as now. `exec` needs no `--reserve` there: the
slots already exist when it builds the bind plan. Switching mode on a running
daemon may refuse while a game has clones open, as `identity` does -- GOTG
will choose it before a launch, never during one.

## How it would be checked

In GOTG's `tests/e2e`: a daemon in `fixed` mode publishes four clones before
any hold, each with its own GUID; a hold makes the pad drive slot 1's clone
without a new device node appearing; unseating leaves the node there and
silent, and a second pad's hold takes it again; `on-demand` behaves exactly
as 871a4f7 does. On the Deck: Ryujinx bound once to `padmap Player 1..4`, a
controller paired mid-game moves player 2.
