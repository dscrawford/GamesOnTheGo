# The Deck's own controls stay a pad when another pad is connected

## What GOTG is doing

On a Steam Deck in Game Mode, the person holding the Deck and a friend with
an Xbox pad both hold A in the picker, and both take a seat.

## What happens today (padmap ac0a3dc, and e0092be before it)

Neither of them does, and the Deck's controls are not offered at all. On the
Deck, with an Xbox Wireless Controller over Bluetooth:

    $ padmap list
    1 pad(s), in the order RetroArch would enumerate them:
      [0] Xbox Wireless Controller   /dev/input/event11
    Left out, on purpose:
      Microsoft X-Box 360 pad 0 (28de:11ff)  /dev/input/event10  Steam's virtual gamepad mirrors a controller padmap already reads
      Microsoft X-Box 360 pad 1 (28de:11ff)  /dev/input/event18  Steam's virtual gamepad mirrors a controller padmap already reads

In Game Mode Steam holds the Deck's controls (hidraw 28de:1205), so hid-steam
publishes no gamepad node for them; Steam's virtual pad is the only way their
presses reach anything. `without_steam_mirrors` (padmap-input/src/pad.rs)
drops every 28de:11ff node as soon as one real pad is readable. Pad 1 does
mirror the Xbox pad padmap reads; pad 0 mirrors the Deck, which padmap does
not read -- so turning the Xbox pad on made the Deck disappear. Alone, the
Deck works, because then the mirror is the only pad and is kept.

(The Xbox pad not seating either was GOTG's: the picker grabbed its joystick
node, which on the Deck also carries a `kbd` handler. Fixed on our side.)

## What would be enough

Drop a Steam mirror only when the controller it mirrors is one padmap reads.
One mirror per controller Steam drives; if Steam drives more controllers than
padmap reads (the Deck's built-in being the usual extra), keep that many
mirrors. Telling which mirror is which is the hard part -- Steam's virtual
pads are numbered in the order Steam opened the controllers, and the Deck's
built-in is normally first -- but keeping one mirror too many is a far
smaller fault than hiding the Deck: at worst a pad appears twice.

## How it would be checked

On the Deck in Game Mode with an Xbox pad connected: `padmap list` offers the
Xbox pad and one Steam virtual pad (the Deck's), and holding A on each in the
picker seats two players. A padmap journey with a STEAM_VIRTUAL fixture
beside an XBOX_360 one and a Valve 28de:1205 hidraw node present covers it
off the Deck.
