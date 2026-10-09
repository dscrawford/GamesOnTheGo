# The overlay over a game

`gotg-killswitch` runs beside every game, on danstick's socket. Pads are
republished via `/dev/uinput`, seated by holding a button, and bound before
the emulator starts: ares (`settings.bml`), Dolphin (`GCPadNew.ini`), Ryujinx
(`Config.json`), Cemu (`controllerProfiles/*.xml`). Motion goes over DSU
(`127.0.0.1:26760`, slot = player − 1); `DANSTICK_DSU_PORT=0` turns it off.

## Seats

Every session starts with nobody seated, picker and game alike. Pick a
controller up, hold a button for a second and a half (`theme.timeouts.pair_hold`),
and the bar at the top fills in your colour: you are player one, the next to
hold is player two, and that is the numbering every game is bound against. A
controller danstick has no buttons for gets them walked right there, over the
game. A pad that goes away (switched off, a flat battery) gives up its seat and
gets it back on return.

## The menu: L + R + Select, held half a second

Comes down for that player alone. Every hold in it is half a second.

| On | Does |
|---|---|
| the row of seats | each controller icon in player order |
| the game's controller | every press puts the presser's icon beside the button it hit; A starts a button test for you alone (your presses stop moving the menu), Select held ends it |
| A on a seat | rebind that controller: danstick walks its buttons again, lighting each as it is pressed |
| A held, then left/right | carry it to another seat, swapping with whoever is there |
| X | take it out of its seat |
| Y | its game port off or on |
| a save (games with saves of their own) | A lists them, A on one asks, A held loads it: the game restarts on it with every seat kept |
| **Exit**, A held | stop the game and push its saves |
| B held | close the menu |

While it is open, danstick holds that pad back from the game.

## Stopping and the ports' own menus

**L + R + Start** held three seconds stops any game (`GOTG_KILLSWITCH_HOLD_MS`,
default 3000; `GOTG_KILLSWITCH=0` disables the chord, `GOTG_KILLSWITCH_OVERLAY=0`
its drawing).

In the libultraship ports (Paper Mario, Ocarina of Time, Majora's Mask, Smash)
**Select** opens the port's own menu -- settings, enhancements, controller
screen -- which they ship with that turned off. The launch leaves them full
screen at the screen's own resolution, the frame rate matched to its refresh
(Smash has no interpolation to match) and 4x MSAA, as defaults: anything
changed in the port's menu stays changed.

## The picker's half

Only pads danstick has published move the picker; the keyboard takes a seat
too (hold `Space`), and a controller that is also a keyboard (a Steam
Controller in lizard mode, a Bluetooth Xbox pad's extra collections) is held
quiet at the kernel while the picker runs. The requirement is tested against
a real daemon and real kernel devices:

```bash
nix run github:dscrawford/GamesOnTheGo#test-controllers   # from a checkout (or GOTG_DEV_ROOT=<one>); /dev/uinput writable, no danstick daemon up
```

The plan and its decisions: [controllers-usability.md](controllers-usability.md).
