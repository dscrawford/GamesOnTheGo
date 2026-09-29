# Controller artwork

One SVG per console, each carrying an `anchor-<input>` circle for every button:
the controller the overlay draws when a pad's buttons are walked (a rebind from the overlay's menu,
or a pad danstick has no buttons for). gotg-killswitch embeds these at build time
(`rust/crates/gotg-killswitch/build.rs`) and puts a label and a line on each
anchored control.

An anchor is named for danstick's control (`a`, `dpup`, `righttrigger`), or for
whatever the older drawings called it (the ares input name, `C-Up`,
`X-Axis/Lo`); a console's `config/controllers/<name>.yaml` maps danstick's names
onto the second kind under `anchors:`. The circles are all that is read, so the
artwork itself can be drawn however it likes.

## Where these came from

| File | Source | Licence |
| --- | --- | --- |
| `snes.svg` | ES-DE `gamepad_nintendo_snes.svg` | MIT |
| `nes.svg` | ES-DE `gamepad_nintendo_nes.svg` | MIT |
| `n64.svg` | ES-DE `gamepad_nintendo_64.svg` | MIT |
| `gamecube.svg` | ES-DE `gamepad_nintendo_gamecube.svg` | MIT |
| `megadrive.svg` | ES-DE `gamepad_sega_md_3_buttons.svg` | MIT |
| `gameboy.svg` | Wikimedia Commons `Gameboy pocket.svg` | CC0 |
| `gba.svg` | Openclipart 344577 `game-boy-advance` | CC0 |
| `generic.svg` | Drawn here | CC0 |

ES-DE is [EmulationStation Desktop Edition](https://gitlab.com/es-de/emulationstation-de),
MIT, © Northwestern Software AB / Leon Styhre / Alec Lofquist. Its root `LICENSE`
is a plain MIT grant and none of the 46 files in its `licenses/` directory covers
`resources/graphics/`, so the root licence is what applies. See `LICENSE-MIT-ES-DE`
beside this file, which is that grant kept verbatim as MIT requires.

The three-button Mega Drive pad, the N64 pad and the GameCube pad are the reason
this is ES-DE and not Commons: no CC0 vector of any of them exists. Commons has
photographs.

## The wordmarks are removed on purpose

The ES-DE originals carry "SUPER NINTENDO", "Nintendo", "SEGA", "MEGA DRIVE
CONTROL PAD" and "NINTENDO GAMECUBE". MIT grants copyright, not trademark, so those paths are deleted
when the file is vendored — and deleted *completely*, since half a wordmark
renders as a glitch rather than as restraint. Removing them is also why the
files here differ from upstream, which MIT permits and this note records.

## Adding a console

1. Find or draw the pad. Keep it flat and unbranded.
2. Get each button's centre. `inkscape --query-all file.svg` prints
   `id,x,y,width,height` for every element; divide by the document scale and
   normalise against the viewBox. Colour disambiguates where it can (the N64's
   red Start, green B, blue A, four yellow C); position does the rest (the Mega
   Drive's three identical black buttons).
3. Append an `anchors` layer of unstroked circles named `anchor-<input>`.
   Unstroked because a stroked shape has two different bounding boxes and
   nobody should have to ask a tool which one it reported.
4. Name it as `artwork:` in the console's `config/controllers/<name>.yaml`, with
   `anchors:` for any circle not named after danstick's control.

Inkscape is an authoring aid, not a dependency — the build reads the SVG
itself. A control the artwork has no anchor for is still walked; it simply has
no label on the drawing.
