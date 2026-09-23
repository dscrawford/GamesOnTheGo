# A join keeps everybody else's clone

GOTG now draws a bar over the game while somebody holds a button to join, so
joining mid-game is the path people will actually take: two are playing, a
third picks up a pad and holds A. The seat lands -- and at that moment players
one and two lose their controllers.

```rust
// tick_seating, after each claim
if let Err(error) = self.start_republisher() { ... }

fn start_republisher(&mut self) -> Result<(), clone::CloneError> {
    self.stop_republisher();                 // <- every clone destroyed
    for slot in &self.slots_assigned { clone::create(...) }   // and made again
```

Every claim tears down every clone and creates them again. The new devices
are new evdev nodes with new SDL instance ids, so to the game the existing
players' pads were unplugged and something else was plugged in:

* An emulator that does not reopen on hotplug keeps reading a dead fd:
  players one and two are frozen for the rest of the session.
* One that does reopen can hand the new devices out in a different order,
  so player one may come back as port two.
* Either way input stops for the length of the republish. GOTG's e2e measures
  it (`test_the_players_already_in_the_game_barely_notice_a_join`) and
  prints the longest gap each run.

It also grows with the room. `state` goes out only after every clone is
made again and every seated player's autoconfig and SDL mapping rewritten, so
on the cluster the claim-to-`state` time was 274, 594, 869 and 1462 ms for
seats one to four, about 0.3 s per seated player. The fourth person waits
five times as long as the first to see their seat, and the pads are unread
for that whole stretch (below).

What is wanted: a claim adds one clone -- the new seat's -- and leaves the
others as they are, the same devices at the same nodes. `start_republisher`
at startup and after an `accept` can stay as it is; seating is the one path
that runs while a game is reading the clones.

## How it would be checked

`test_a_join_costs_the_same_however_full_the_room` (strict xfail): the fourth
seat's `state` lands within 100 ms of the first's.

`tests/e2e/test_pairing.py::test_a_join_leaves_the_players_already_in_the_game_plugged_in`
(a strict xfail today): player one seated with its clone open, player two
holds to join, and player one's clone is the same device afterwards and its
open fd still delivers presses.

## Underneath it: the game's sandbox is fixed at launch

Keeping the clones would stop the republish, but a joiner still reaches
nothing, because of how `padmap-rs exec` isolates the game
(`padmap-rs/src/main.rs`, `padmap-input/src/isolate.rs::bwrap_argv`):

```text
bwrap --dev-bind / / --tmpfs /dev/input --dev-bind <each node present now> ...
```

`/dev/input` inside the game is a tmpfs holding the nodes that existed when
the game started. A clone created mid-game is a new `/dev/input/eventN` that
never appears in there, and SDL's udev hotplug does not cross the user
namespace either. So today, a mid-game join:

* gives the joiner no input in any emulator -- ares, Dolphin and Ryujinx all
  hotplug SDL devices, but the device never exists for them to find;
* cuts off everybody already playing, because their clones are recreated
  (above) and the new nodes are outside the sandbox too. The bind still
  points at the old, destroyed device.

GOTG now draws a "joining" bar over the game for exactly this moment, so
from the sofa it looks like it worked and then everybody's controller dies.

What would make it work, in order of preference:

1. **Seats exist before people do.** Under `exec`, create a clone for every
   seat the launch allows (`--players N`), including the empty ones, and bind
   them all into the sandbox. A claim attaches its source pad to the clone
   that is already there, and nothing is created or destroyed while the game
   runs. This needs an identity whose GUID and layout are known before
   anybody sits down: `padmap` (1209:0001, per-player version) with a
   normalised layout, or `xbox360` (not usable for Ryujinx, which blanks the
   name CRC, so every player gets one id). `mirror` cannot do it: the GUID
   comes from a pad nobody has picked up yet. `env.sh` could then carry
   mapping lines and names for every seat, so emulators can have ports 2-4
   bound at launch (`SDL/0/padmap Player 3` in Dolphin, a GUID in ares and
   Ryujinx).
2. **The sandbox follows the clones.** Bind the real `/dev/input` and cover
   only the raw pads, including ones that appear later (an inotify watch in
   exec's parent that adds `/dev/null` covers), with SDL told to scan
   `/dev/input` itself (`SDL_JOYSTICK_DISABLE_UDEV=1`). A new clone then
   appears as an ordinary hotplug. This is cheaper but racier: a raw pad
   switched on mid-game is visible until it is covered, and SDL mappings for
   late clones are not in `SDL_GAMECONTROLLERCONFIG`.

Either way, GOTG will then write ports 2-4 at launch rather than only the
seated ones (today `pads.sh`, `pads-dolphin.sh` and `pads-ryujinx.sh` bind
only who is seated, and Four Swords Adventures falls back to the keyboard for
an empty seat).
