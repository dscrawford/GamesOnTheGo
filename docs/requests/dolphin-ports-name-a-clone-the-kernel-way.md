# Dolphin's ports name a clone the way the kernel does, and Dolphin never hears it

`padmap-rs emit --dolphin-dir` writes, for a seated pad:

```ini
[GCPad1]
Device = SDL/0/padmap Player 1
```

Dolphin stores a controller as `<backend>/<slot>/<name>` and looks it up by
that string. SDL does not call the clone `padmap Player 1`: a clone mirrors
the pad behind it, so SDL finds 045e:028e in its own database and the device
Dolphin sees is `SDL/0/Xbox 360 Controller`. The port is bound to a device id
nothing answers to, and nothing says so -- the file is complete, the pad is
seated and forwarding, and the game does not move.

Measured tonight, on Four Swords Adventures with one Xbox pad seated:

| file | `Device =` | Dolphin |
|---|---|---|
| `GBA.ini`, written by gotg from `gotg-pads` | `SDL/0/Xbox 360 Controller` | works |
| `GCPadNew.ini`, written by `emit` | `SDL/0/padmap Player 1` | no controls |

The same enumeration inside `padmap-rs exec` reports the clone as
`Xbox 360 Controller`, slot 0, GUID `0500c9a75e04…` -- the GUID keeps the
name-CRC of `padmap Player 1`, which is how it is matched, and the *name* is
already gone by the time anything can read it.

gotg has a workaround in `padmap_name_dolphin_devices` (rewrites the `Device`
lines after `emit`, matching by GUID against a sandboxed `gotg-pads` run), so
nothing is blocked. It is in the wrong place: `emit` knows which clone it is
writing and could ask SDL what that clone is called.

## What would fix it

When `emit` writes a Dolphin device id, use the name SDL reports for the
clone rather than the name the clone was created with. For a pad SDL has no
mapping for the two are the same string, so nothing changes there; for every
pad SDL *does* know -- which is most of them -- it is the difference between
a bound port and a dead one.

## How it would be checked

* A pad SDL recognises (an Xbox pad), seated, `emit --dolphin-dir`: the
  `Device` line matches what `gotg-pads` reports from inside
  `padmap-rs exec`, character for character.
* A pad SDL has never heard of, seated: the line still says
  `SDL/<slot>/padmap Player N`, because that is what SDL calls it.
* Two pads of one model: the slots differ and each port gets its own.
