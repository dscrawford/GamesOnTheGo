# Reserve the seats as part of `exec`

## What GOTG is doing

A person joining Four Swords Adventures mid-game should get a controller
that works, which `reserve` (785ed3c) makes possible: every seat the launch
allows has a clone, bound in the emulator before the game starts, and a claim
adopts it. GOTG wants to use it for FSA first (two to four players, Dolphin,
bound by device name), then for any game whose environment says how many
players it takes.

## Why GOTG cannot do it well from its side

- **Nothing in the launch path can send it.** The client is bash and speaks
  to padmap only through the CLI (`padmap ensure-daemon`, `padmap emit`,
  `padmap-rs exec`); only the picker and the launch gate hold a socket, and
  both run before `gotg play` has decided the environment, its identity or
  its player count.
- **The timing is exec's.** EVENTS.md: "Reserve before the launch, not
  after. The nodes have to be there when `exec` builds its bind plan." exec
  is the one step that knows when that is.
- **The identity changes in the same breath.** FSA would ask for
  `PADMAP_PAD_IDENTITY=xbox360` (reserve refuses anything else). The picker's
  daemon runs `mirror`, so today the launch replaces it; whether the people
  already seated in the picker keep their seats across that is not said
  anywhere, and a reserve sent before the replacement would be lost with the
  old daemon.

## What would be enough

1. **`padmap-rs exec --reserve N -- <program>`** (or an environment variable
   GOTG can set in the environment's launcher): before building the bind
   plan, make sure seats 1..N exist -- the seated ones as they are, the rest
   reserved -- then exec as today. The seated players keep their seats and
   their clones.
2. **The identity it needs, without losing anybody.** When the running
   daemon is `mirror` and the launch asks for the 360 identity (`--reserve`
   implies it, or `PADMAP_PAD_IDENTITY=xbox360` is set), seated players keep
   their seats and get 360 clones; say in EVENTS.md what a front-end sees
   while that happens (`state`, the clones' nodes changing).
3. **Hand them back when the game ends** (`reserve 0`), or say that the
   daemon ending with the session (`--follow`) already does.

GOTG's side would then be one field per environment (how many players it
takes) passed as `--reserve N`, and the bindings that are already written
from `state`'s `reserved` for Dolphin, ares, Cemu and Ryujinx.

## How it would be checked

- Picker seats one pad (mirror), then `padmap-rs exec --reserve 2 -- <probe>`:
  the probe sees two `padmap Player N` devices in its `/dev/input`, player 1
  still the seated pad, `state` shows player 1 seated and player 2 reserved.
- A second pad claims while the probe runs: its presses arrive on the
  probe's already-open player-2 device.
- The probe exits: the reserved seat is gone (or `reserve 0` is documented as
  the front-end's job).
