# The keyboard's seat is the keyboard and the mouse

## What GOTG is doing

The picker seats the keyboard as a player when somebody holds the space bar
(`seat_keyboard`, `docs/EVENTS.md`), and draws that seat, in the picker's seat
strip and in the bar over a running game, with a drawing of a keyboard *and a
mouse*. As of GOTG `config/icons.yaml` the keyboard seat is drawn that way on
purpose: the person sitting at the keyboard has the mouse under their other
hand, and some games want it -- a PC port's camera, the Wii pointer in
Dolphin, the N64 and SNES mice in ares, touch in a DS emulator, RetroArch
cores with a mouse or lightgun device.

## What happens today

- `claim` and `state` name the seat `"Keyboard"` with `"icon": "keyboard"`,
  so the seat says it is a keyboard alone. GOTG maps the name to its
  keyboard-and-mouse drawing itself; padmap's `icon` field and GOTG's
  drawing disagree.
- The mouse belongs to no seat. `docs/EMULATORS.md`, "The keyboard", binds
  keys for player N and says nothing of the pointer: Dolphin's Wii Remote 1
  "stays on the mouse and keyboard as Dolphin ships it", whichever player the
  keyboard is; ares' mouse ports, RetroArch's `input_player{N}_mouse_index`
  and Cemu's are not written at all. So "player 2 is the keyboard" gives
  player 2 the keys and leaves the mouse wherever the emulator defaulted it,
  often player 1's port or nowhere.

## What would be enough

1. **Say it.** The keyboard's seat is `"name": "Keyboard and Mouse"`, `"icon":
   "keyboard-mouse"` in `claim` and in `state`'s `players[]` (keeping
   `"keyboard": true`, and adding `"mouse": true` if that is the simplest way
   for a front-end to tell). GOTG's rules already map "keyboard and mouse" to
   the keyboard-and-mouse drawing, so the new name needs nothing on this side.
2. **Bind it.** Wherever an emulator has a mouse or pointer input for a port,
   the keyboard's seat gets it along with the keys, and a port a pad holds
   does not:
   - Dolphin: the keyboard's GameCube port is already
     `XInput2/0/Virtual core pointer`, which is the mouse too; Wii Remote N
     for the keyboard's seat N gets the pointer (IR) from the same device,
     rather than Wii Remote 1 always.
   - ares: the mouse device for the keyboard's port where the system has one
     (N64 Mouse, SNES Mouse).
   - RetroArch: `input_player{N}_mouse_index` for the keyboard's player.
   - Cemu: whatever the keyboard profile can carry of the mouse.
   - Ryujinx: its mouse-as-touch where the keyboard's player is player 1.
   Where an emulator has no such input, nothing changes, and
   `docs/EMULATORS.md` says so per emulator, as it does for the keys.
3. **Nothing grabbed.** As with the keyboard: the mouse stays the
   compositor's. A seated *pad*'s own mouse nodes (a Steam Controller in
   lizard mode, an Xbox pad over Bluetooth) are still held as they are today
   ("A seated pad's keyboard and mouse are held too"); this is only the
   desk's mouse.

## How it would be checked

- `seat_keyboard`: the `claim` and `state` entry carry the new name and icon.
- Keyboard seated as player 2 beside a pad on player 1, then Dolphin's
  `WiimoteNew.ini`: Wii Remote 2 is on `XInput2/0/Virtual core pointer` with
  its IR on the pointer, and Wii Remote 1 is the pad's, not the mouse's.
- The same seat in RetroArch's launch config: `input_player2_mouse_index` is
  set, `input_player1_mouse_index` is not the desk's mouse.
- ares: an N64 game with the keyboard on port 2 lists the mouse on port 2's
  device choices, bound.
