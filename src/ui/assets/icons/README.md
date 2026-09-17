# Controller icons

Small, glanceable drawings of *controllers* — one per model a person might be
holding — for the strip along the top of the picker that says who is in which
seat. Not the same thing as `../controllers/`, which holds one diagram per
*console* with an `anchor-<input>` circle on every button so a binding can be
labelled. These have no anchors and are never annotated; they are read at about
28 pixels.

Rasterised at build time, like the diagrams next door and for the same reason
(`build-controllers.py --icons`), to one height so a row of them shares a
baseline. The artwork is a black silhouette and the runtime tints it to the
player's colour, so nothing here needs a light and a dark copy.

`generic.svg` is the one that matters most: it stands in for every controller
without a rule of its own, which on a shelf of third-party pads is most of
them.

## Where these came from

| File | Source | Licence |
| --- | --- | --- |
| `gamecube.svg` | ES-DE `gamepad_nintendo_gamecube.svg` | MIT |
| `xbox.svg` | ES-DE `gamepad_xbox.svg` | MIT |
| `playstation.svg` | ES-DE `gamepad_playstation.svg` | MIT |
| `wii.svg` | ES-DE `wii_remote_nintendo.svg` | MIT |
| `switch.svg` | ES-DE `joycon_pair_nintendo.svg` | MIT |
| `keyboard.svg` | ES-DE `keyboard_generic.svg` | MIT |
| `keyboard-mouse.svg` | ES-DE `keyboard_and_mouse_generic.svg` | MIT |
| `mouse.svg` | ES-DE `mouse_generic.svg` | MIT |
| `steam.svg` | Drawn here | CC0 |
| `generic.svg` | ES-DE `gamepad_generic.svg` | MIT |
| `n64.svg` | ES-DE `gamepad_nintendo_64.svg` | MIT |
| `nes.svg` | ES-DE `gamepad_nintendo_nes.svg` | MIT |
| `snes.svg` | ES-DE `gamepad_nintendo_snes.svg` | MIT |
| `megadrive.svg` | ES-DE `gamepad_sega_md_6_buttons.svg` | MIT |

ES-DE is [EmulationStation Desktop Edition](https://gitlab.com/es-de/emulationstation-de),
MIT, © Northwestern Software AB / Leon Styhre / Alec Lofquist. The grant is kept
verbatim beside the other set, in `../controllers/LICENSE-MIT-ES-DE`, and covers
these files too — it is one licence for one project, not one per file.

## The wordmarks are removed, same as next door

MIT grants copyright, not trademark, so every wordmark and logo is deleted when
a file is vendored — completely, since half a wordmark renders as a glitch
rather than as restraint. Removed here:

| File | What went |
| --- | --- |
| `gamecube.svg` | "NINTENDO GAMECUBE" (`g157`) |
| `xbox.svg` | the guide-button logo (`g5600`) |
| `playstation.svg` | "PlayStation" (ten elements around x=120-140, y=103-111) |
| `wii.svg` | "Wii" (`rect126`, `rect128`, `rect130`, `rect132`, `polygon134`) |

The keyboard and mouse drawings carry no wordmark: nobody brands a generic
keyboard, and ES-DE's are drawn rather than traced from a product.

The button *letters* stay. A, B, X and Y beside a face button are what the
button is called, and a person matching the icon to the pad in their hands
needs them; the trademark is the sphere in the middle, and that is what went.

They are paths rather than `<text>` — ES-DE converts its text to curves — so
they cannot be found by searching for text. An id beginning `text` is not one
either: on the GameCube pad those turned out to be the START and Z labels, and
deleting them took the labels and left the wordmark. Each was found by asking
`inkscape --query-all` for every element's box and looking for a row of small
shapes where the render shows lettering.

## Steam is drawn rather than found

There is no free vector of a Steam Controller. Commons has
`Steam Controller colored logo.svg`, which is the Steam *logo*, public domain as
a text logo and marked `Restrictions: trademarked` — the wrong thing twice over.
So `steam.svg` is ours: flat, unbranded, and CC0, in the same shape language as
the ES-DE pads so the strip does not look like two sets of artwork.

## Which icon a pad gets

`icons.py` decides, and falls back to `../controllers/` for the consoles that
already have a drawing there — `n64`, `nes`, `snes`, `megadrive`, `gameboy`,
`gba` and `generic`. Those were vendored and de-branded once; copying them here
would be the same bytes under a second name, free to drift.

## Adding one

1. Find or draw the pad. Flat, unbranded, no text.
2. Delete every wordmark and logo, and write what you deleted in the table
   above — the next person should not have to find them again.
3. Render it at 28 pixels and look at it. Most of these read as a silhouette at
   that size, which is the whole job.
