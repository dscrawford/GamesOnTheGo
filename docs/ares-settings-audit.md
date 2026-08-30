# ares settings: what a fresh profile gets wrong

*2026-08-30 · ares v148 · Confidence: High — every default below was read out of
the v148 source in the nix store, and the two that changed behaviour were
measured through PipeWire rather than argued.*

What ares does with a profile nobody has configured, and which of those defaults
a launcher has to override. The question behind it: an environment built here is
brand new every time, so anything ares only gets right after a human visits its
settings screen, it never gets right.

## The defaults

From `desktop-ui/settings/settings.hpp` at v148. `settings.cpp` explicitly seeds
only three keys on first run — `Video/Driver`, `Audio/Driver`, `Input/Driver` —
and everything else inherits the compiled-in value.

| Key | Fresh default | Verdict |
| --- | --- | --- |
| `Audio/Latency` | `0` | **Broken.** Pinned to 60. |
| `Audio/Frequency` | `0` | Self-heals, pinned anyway. |
| `General/noFilePrompt` | `false` | **Blocks a sofa launch.** Pinned via `--no-file-prompt`. |
| `Audio/Blocking` | `true` | Keep — this is the throttle. |
| `Audio/Dynamic` | `false` | Open question, see below. |
| `Video/Blocking` | `false` | Open question, see below. |
| `Input/Defocus` | `"Pause"` | Harmless here — see below. |
| `Video/Driver` | `OpenGL 3.2` on Linux | Fine. |
| `Boot/Fast`, `General/Rewind`, `General/RunAhead` | `false` | Correct off. |
| `General/autoSaveMemory` | `true` | Correct on — this is what flushes saves. |

## Fixed: audio latency of zero

`ruby/audio/sdl.cpp` sizes its buffer as `(latency * frequency) / 1000`. A fresh
profile has latency 0, and it never recovers: `initialize()` writes the
negotiated rate back into `frequency` but never into `latency`, so
`Audio::latency()` keeps returning 0, `hasLatency(0)` stays false, and
`audioLatencyUpdate()` assigns 0 to 0 on every launch.

A zero buffer is not silence, which is what made it hard to place. `output()`
blocks while `bytesRemaining > _bufferSize`, so with a buffer of 0 it waits for
the device queue to drain *completely* before every sample. Permanent underrun.

Measured, not argued: the ares node runs at quantum 960/48000 without the pin
and 2880/48000 with it — exactly the `(60 * 48000) / 1000` the source asks for.
The settings file cannot show this, because ares restores command-line overrides
before saving, so a fresh `settings.bml` reads 0 either way.

Upstream: never filed as an issue. Fixed incidentally by the June 2026 audio
refactor, which gave the base class `_latency = 40` and `_frequency = 48000`
([commits](https://github.com/ares-emulator/ares/commits/master/ruby/audio)).
No release contains that yet, so v148 needs the override.

60 rather than 40: `hasLatencies()` differs per driver, and 60 is present in
every list ares can pick from on Linux — SDL `{10,20,40,60,80,100}`, PulseAudio
/ ALSA / OpenAL `{20,40,60,80,100}`. A value outside the list is rejected and
replaced by the same broken 0, so the value has to be one they all share.

## Fixed: the modal dialog in front of ten games

`General/noFilePrompt` defaults false, and `desktop-ui/emulator/nintendo-64.cpp`
gates two prompts on it — the 64DD disk, and a Transfer Pak asking for a Game
Boy cartridge for any cart whose database entry sets `tpak`.

That is not a corner. ares' own table in `mia/medium/nintendo-64.cpp` sets
`tpak` for 19 games, and ten of them are in this library: Pokémon Stadium,
Pokémon Stadium 2, Perfect Dark, Mario Golf, Mario Tennis, Transformers Beast
Wars, and their Japanese siblings. Each would open a file browser before the
game — unanswerable from a sofa, where there is no keyboard and a gamepad does
not drive a file dialog.

`--no-file-prompt` suppresses requests for *additional* media only; the game
named on the command line still loads. Verified by launching Mario Golf through
the built environment: `Loaded usa.mario_golf`, audio node running at
2880/48000.

## Looked at and deliberately left alone

**`Input/Defocus = "Pause"`** looks like the same class of bug as Dolphin's
`BackgroundInput`, which this repo does set. It is not. The check is
`defocus == "Pause" && !ruby::video.fullScreen() && !presentation.focused()` —
it only pauses when *not* fullscreen, and a launch from Steam is fullscreen. At
a desk, in a window, pausing on defocus is the behaviour a person wants.

**`Video/Blocking = false` (vsync off) and `Audio/Dynamic = false`.** Out of the
box ares paces on audio alone and does not vsync. Dynamic rate control is what
ares was built to do — nudging the resample ratio so a 59.92Hz console on a 60Hz
panel neither underruns nor drifts — and turning it on with vsync would be the
textbook configuration.

It is left off because the case for changing it is inference, not measurement.
Nobody has reported tearing here, every platform has run this way, and swapping
audio-paced for video-paced sync is the kind of change that trades one artefact
for another. It needs someone watching a real television, not a
`pw-dump`. If it is ever tried, the three move together:
`Video/Blocking=true`, `Audio/Dynamic=true`, `Audio/Blocking=false` — leaving
both Blocking flags on gives two competing clocks.

**Firmware.** Only GBA needs any, and `gba.nix` already places it. Plain Mega
Drive and plain N64 need none; the N64's PIF and CIC ROMs are built in. Mega CD
and 64DD would need firmware, and neither is a platform here.

## A rebuilt environment is not a running one

`env_is_built()` is `[[ -x "$(env_bin "$1")" ]]`, so `gotg play` uses whatever
the GC root already points at and does not rebuild. Every override here lives in
the generated launcher, which means an existing platform keeps the old one until
`gotg sync` rebuilds its root. This was caught by measuring: env-n64 was still
running at quantum 960/48000 after the audio fix was committed.

## Sources

Primary, read locally at v148: `desktop-ui/settings/settings.hpp`,
`desktop-ui/settings/settings.cpp`, `desktop-ui/program/drivers.cpp`,
`desktop-ui/emulator/nintendo-64.cpp`, `mia/medium/nintendo-64.cpp`,
`ruby/audio/*.cpp`, `ruby/video/video.cpp`.

Upstream: [ares](https://github.com/ares-emulator/ares) ·
[audio commit history](https://github.com/ares-emulator/ares/commits/master/ruby/audio) ·
[#983 crackling at 20ms](https://github.com/ares-emulator/ares/issues/983) ·
[#2424 crackling on Arch](https://github.com/ares-emulator/ares/issues/2424) ·
[#1546 N64 Vulkan segfault, open](https://github.com/ares-emulator/ares/issues/1546) ·
[v142 release notes](https://ares-emu.net/news/ares-v142-released) ·
[Near on dynamic rate control](https://archive.ares-emu.net/near.sh/articles/audio/dynamic-rate-control.html)

Worth knowing though it does not bite here: [#1546](https://github.com/ares-emulator/ares/issues/1546)
and [nixpkgs#322204](https://github.com/NixOS/nixpkgs/issues/322204) — the N64
core initialises Vulkan regardless of the video driver, so an ares built without
Vulkan segfaults on N64. This build has it.
