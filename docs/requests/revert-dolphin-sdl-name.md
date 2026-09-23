# Revert e7a79e1: Dolphin names a clone `padmap Player N`, not SDL's name

`e7a79e1 fix(input): a Dolphin port names the clone the way SDL will` answered
a request GOTG withdrew the next morning, because it was wrong. Please revert
it: with it, every Dolphin player one is bound to a device that sends nothing.

What GOTG measured afterwards, with Dolphin's own controller log turned on
(`[Logs] CI = True` in `Logger.ini`), Four Swords Adventures, an Xbox pad and
a Steam Controller seated, Dolphin running under `padmap-rs exec`:

```
Added device: SDL/0/Xbox 360 Controller     <- the raw pad, grabbed by padmap
Added device: SDL/0/padmap Player 1         <- its clone
Added device: SDL/0/padmap Player 2         <- the Steam Controller's clone
```

So Dolphin -- this build, SDL 3.4.12, with padmap's mapping in its
environment -- lists the clone by padmap's name. The request's evidence was
`gotg-pads`, which reports SDL's *joystick* name and does rename the clone;
Dolphin does not use that name. `emit`'s original `SDL/0/padmap Player 1` was
right, and the "fixed" `SDL/0/Xbox 360 Controller` names the grabbed raw pad:
player one's GameCube pad and GBA both dead, and nothing on screen moved.

After GOTG put padmap's name back, the same game worked with both pads.

## How it would be checked

* `emit --dolphin-dir` with an Xbox pad seated writes
  `Device = SDL/0/padmap Player 1`.
* With Dolphin's CI log on, that string is one of the `Added device:` lines.
