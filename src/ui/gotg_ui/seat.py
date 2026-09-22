"""`gotg-seat` — the screen between pressing play and the game starting.

The first window of every launch, from a terminal, from the grid, or from
Steam: whatever the daemon remembers is forgotten, and the person about to
play holds a button to be player one. Then, and only if this pad has never
been mapped for this console, the buttons are walked.

Never silent. It used to be: with no daemon to ask it printed a line to
stderr and returned, which under Steam is a line in a log nobody reads, and
the game came up with nothing to play it with and no word why. Now the
window opens either way. With padmap gone it says so and counts down,
because a screen no controller can dismiss must not be a trap -- the
keyboard skips it at once, and eight seconds skip it for everybody else.

Exits 0 whatever happens. This is in the way of a game somebody asked for,
and a controller problem is not a reason to refuse to start one.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import replace

from . import config

# Before pygame is imported, because it prints its banner at import time. This
# runs in front of every launch, and the one line it is entitled to in a game's
# log is one that says something went wrong.
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import pygame  # noqa: E402 - the line above only works ahead of the import

from . import devices, profiles, trace
from . import pads as sdl_pads
from .bindings import console_for, pad_controls
from .controllers import Diagram, assets_dir, draw_reveal, draw_tick, icon_surface
from .controllers import draw as draw_diagram
from .gate import (
    CHECKING,
    MAPPING,
    READY,
    SEATING,
    SKIPPED,
    Fade,
    Gate,
    GoHold,
    apply,
    decide,
    rebind,
    without_controllers,
)
from .hush import Hush
from .padmap import Padmap, ensure_daemon
from .padstrip import EMPTY_RING, LABEL, LABEL_DIM, PANEL, colour_for
from .padstrip import READY as SETTLED_GREEN  # gate.READY is a state; this is a colour
from .pressing import (
    controls_for,
    controls_on,
    element_for_button,
    element_on_axis,
    elements_on_axis,
)

WINDOW = tuple(config.get("theme.window", [1280, 800]))
BACKGROUND = config.colour("theme.colours.background", (18, 18, 20))

# How long to wait for the daemon to say anything at all before giving up and
# launching. A socket that is there answers in milliseconds; this is the bound
# on a socket that is there and silent.
FIRST_STATE_TIMEOUT = float(config.get("theme.timeouts.first_state", 3.0))

# How long the window stays when there is no padmap to ask, before the game
# starts anyway. Long enough to read; short enough that a television with no
# keyboard in the room is not stuck on it. The environment wins, for tests.
COUNTDOWN = float(os.environ.get("GOTG_SEAT_COUNTDOWN") or config.get("theme.timeouts.seat_countdown", 8.0))

# The hold that starts the game, once padmap has accepted the seats. A full
# second, on a press that began on this screen: the hold that took the seat
# ran straight into padmap's confirm, and one press seated somebody and
# started the game before they had let go. The daemon's accept is padmap's
# business; this second is ours, read from the clone it just published.
GO_HOLD = float(os.environ.get("GOTG_SEAT_GO_HOLD") or config.get("theme.timeouts.seat_go_hold", 1.0))

# How long a press stays lit beside its seat. Long enough to see a tap from
# across a room, short enough that four people pressing at once still reads as
# four separate answers rather than four lights left on.
PRESS_SHOWN = 0.45

# The pause between pairing and being able to start. The hold that claims a
# seat is padmap's quarter second, and a thumb does not come off a button that
# fast: the same press ran straight into the go hold, so the game started
# while somebody was still looking at the screen they had just reached. Now
# there are three moments and the middle one is doing nothing -- pair, let go,
# ready -- and nothing counts as a go until every pad has been quiet this long.
PAUSE = float(os.environ.get("GOTG_SEAT_PAUSE") or config.get("theme.timeouts.seat_pause", 1.0))

# SDL's standard axis order, as which stick and which of its two numbers.
# The triggers (4 and 5) are not a stick and keep their labels.
STICK_AXES = {0: ("left", 0), 1: ("left", 1), 2: ("right", 0), 3: ("right", 1)}

# Below this a stick is at rest. A pad reads a percent or two off centre for
# ever, and a dot that never sits still looks like a broken controller.
STICK_DEAD = 0.12

# The hold on Y that walks the buttons again. A tap was what it was, and a
# thumb that brushed Y reaching for A landed in the wizard -- so it is a hold,
# and the same length as the one that starts the game.
REBIND_HOLD = float(os.environ.get("GOTG_SEAT_REBIND_HOLD") or config.get("theme.timeouts.seat_rebind_hold", 1.0))


def draw(
    screen, font_at, gate: Gate, title: str, diagram: Diagram | None = None, progress: float | None = None
) -> None:
    """The gate, whatever it is waiting for.

    `progress` is the reveal's fraction as the clock sees it (`gate.Fade`),
    rather than the last reading padmap sent: a hold let go says nothing, and
    the drawing has to fall back to nothing by itself.
    """
    width, height = screen.get_size()
    screen.fill(BACKGROUND)

    heading = font_at(34).render(title, True, LABEL_DIM)
    screen.blit(heading, ((width - heading.get_width()) // 2, int(height * 0.10)))

    prompt = font_at(56).render(gate.prompt, True, LABEL)
    screen.blit(prompt, ((width - prompt.get_width()) // 2, int(height * 0.22)))

    if gate.state == SEATING:
        # The seat somebody is about to take, in the colour they will have:
        # the generic pad, dark, filling in as they hold. One, because one
        # controller is what this is waiting for.
        centre = (width // 2, int(height * 0.52))
        filling = gate.progress if progress is None else progress
        draw_reveal(screen, centre, None, colour_for(1), filling, int(height * 0.22))

    if gate.state == MAPPING and diagram is not None:
        # The pad, with the button being asked for ringed on it. "press Z" is
        # a sentence somebody has to already know the answer to; a ring on the
        # drawing is the answer.
        # Sized from the height it is allowed rather than the width, because a
        # pad is wider than it is tall only for some consoles -- the GameCube
        # drawing is square, and a width-sized one runs off the bottom of the
        # screen and through the progress bar.
        budget_h = int(height * 0.48)
        pad_w = min(int(budget_h * diagram.size[0] / diagram.size[1]), int(width * 0.52))
        pad = diagram.surface(pad_w)
        pad_x = (width - pad.get_width()) // 2
        pad_y = int(height * 0.31) + (budget_h - pad.get_height()) // 2
        screen.blit(pad, (pad_x, pad_y))
        for name in gate.scheme.anchor_names(gate.control):
            uv = diagram.anchors.get(name)
            if uv is None:
                continue
            at = (pad_x + int(uv[0] * pad.get_width()), pad_y + int(uv[1] * pad.get_height()))
            pygame.draw.aacircle(screen, colour_for(1), at, 17, 3)
            break

    if gate.state == MAPPING and gate.captured:
        # What the previous press became, so a binding is seen as it is made.
        control, binding = list(gate.captured.items())[-1]
        said = font_at(24).render(f"{control} is now {profiles.describe(binding)}", True, LABEL_DIM)
        screen.blit(said, ((width - said.get_width()) // 2, int(height * 0.29)))

    if gate.state == MAPPING and gate.total:
        bar_w, bar_h = int(width * 0.5), 10
        left, top = (width - bar_w) // 2, int(height * 0.82)
        pygame.draw.rect(screen, PANEL, (left, top, bar_w, bar_h), border_radius=5)
        filled = int(bar_w * (gate.index / gate.total))
        pygame.draw.rect(screen, colour_for(1), (left, top, filled, bar_h), border_radius=5)
        counted = font_at(24).render(f"{gate.index} of {gate.total}", True, LABEL_DIM)
        screen.blit(counted, ((width - counted.get_width()) // 2, top - 30))

    if gate.message:
        said = font_at(24).render(gate.message, True, EMPTY_RING)
        screen.blit(said, ((width - said.get_width()) // 2, int(height * 0.76)))

    # Nothing about Esc while the buttons are being walked: it is refused
    # there, and offering it was offering a half-bound pad.
    keys = "S skip this button   ·   hold a button to finish" if gate.state == MAPPING \
        else "Esc play without a controller"
    footer = font_at(22).render(keys, True, LABEL_DIM)
    screen.blit(footer, ((width - footer.get_width()) // 2, int(height * 0.92)))


def _said(why: str) -> None:
    """One line on stderr and one in the trace, for every way out of here.

    Every exit from this gate used to be a silent `return 0`, which is the
    same thing on a terminal as the gate never having run -- and "it went
    straight to the game" is then a report nobody can act on. `gotg play`
    keeps stderr, so this lands in front of whoever launched it.
    """
    print(f"gotg-seat: {why}", file=sys.stderr)
    trace.say("seat-exit", why=why)


def run(platform: str, title: str) -> int:
    """Ask what needs asking, then get out of the way. Always returns 0."""
    # The same session name the picker used, when there was one: this pid is
    # the picker's after its execvp, and padmap leaves a daemon following it
    # alone. A launch with no picker -- Steam -- starts one of its own, clean.
    # The same two rules the picker sets for the daemon it starts: no
    # session opened by the daemon itself, and no seat but by a hold.
    os.environ.setdefault("PADMAP_NO_AUTOSETUP", "1")
    os.environ.setdefault("PADMAP_NO_AUTOATTACH", "1")
    trouble = ensure_daemon(fresh=True, follow=os.getpid())
    pads = Padmap()
    if trouble is not None or not pads.connect():
        # No daemon, no questions to ask -- but a window all the same, or a
        # game starts with nothing to play it with and no word why.
        print(f"gotg-seat: {trouble or pads.error}; starting anyway", file=sys.stderr)
        return _hold_the_door(title, trouble or pads.error or "")

    gate = Gate(platform=platform)

    # Decide before opening a window. The common case is a machine that is
    # already set up, and it should cost a socket round trip rather than a
    # display mode -- opening one and closing it again flickers the screen in
    # front of every launch.
    deadline = time.monotonic() + FIRST_STATE_TIMEOUT
    while time.monotonic() < deadline:
        for event in pads.poll():
            gate = apply(gate, event)
        if pads.state:
            break
        time.sleep(0.02)
    if not pads.state:
        print("gotg-seat: padmap said nothing; starting anyway", file=sys.stderr)
        pads.close()
        return _hold_the_door(title, "padmap said nothing")

    gate, command = decide(gate)
    if gate.done:
        # Nothing to ask: every seat is taken and mapped already. Said out
        # loud, because this is the one exit that shows no window at all and
        # a launch that skipped the gate in silence is indistinguishable from
        # a gate that was never called.
        _said(f"nothing to ask ({gate.state}, {gate.seated} seated); starting")
        return 0
    if command is not None:
        pads.send(command)

    pygame.init()
    pygame.display.set_caption("GamesOnTheGo")
    screen = pygame.display.set_mode(WINDOW)
    clock = pygame.time.Clock()
    fonts: dict[int, pygame.font.Font] = {}

    def font_at(size: int) -> pygame.font.Font:
        if size not in fonts:
            fonts[size] = pygame.font.Font(None, size)
        return fonts[size]

    # Loaded once, and absence is survivable: a console with no artwork, or a
    # build with none, still gets the words.
    try:
        diagram: Diagram | None = Diagram(assets_dir(), gate.scheme.artwork)
    except (OSError, ValueError, KeyError) as error:
        print(f"gotg-seat: no controller drawing: {error}", file=sys.stderr)
        diagram = None

    # A controller is a keyboard too, and the gate never knew.
    #
    # The picker holds the keyboard and mouse nodes that belong to a
    # controller with EVIOCGRAB -- a Steam Controller in lizard mode types
    # Enter when A is pressed, and an Xbox pad over Bluetooth carries its own
    # keyboard collection. This screen did not, and it takes Enter as "start
    # the game now": so pairing a Steam Controller pressed A, the kernel sent
    # Enter, and the game started with nobody having held anything. That is
    # the leaking input. See hush.py.
    hush = Hush()
    hush.refresh()
    trace.say("gate-hush", held=sorted(hush.held))

    # The reveal's own clock. padmap stops sending `progress` when a button is
    # let go rather than sending a zero, so this is what makes the drawing
    # empty again -- see gate.Fade.
    fade = Fade()
    # The diagram, once, shared with the door: it is the same screen.
    cache: dict = {}

    # Seating stays open into the game, which is what it was for.
    #
    # It used to be closed here, because it cost about a hundred milliseconds
    # a press: padmap rescanned every input device on every 20 ms tick while
    # seating was open, one scan took ~100 ms, and the forwarding waited
    # behind it. Melee felt like treacle and it was that. The gate closed
    # seating and gave up mid-game joining for it --
    # docs/requests/seating-costs-the-game-its-input.md asked for the scan to
    # be throttled, and padmap has done it. The e2e's strict xfail turned into
    # an XPASS, which is the day this note said to take the close out: a pad
    # switched on in the middle of a level can take a seat again, and the
    # latency tests hold the other half to under a frame.

    try:
        while True:
            while not gate.done:
                for event in pygame.event.get():
                    if event.type in (pygame.JOYDEVICEADDED, pygame.JOYDEVICEREMOVED):
                        # padmap publishing a clone is a device arriving, and
                        # its keyboard siblings arrive with it.
                        hush.refresh()
                        devices.forget()
                    elif event.type == pygame.QUIT:
                        gate = _leave(pads, gate)
                    elif event.type == pygame.KEYDOWN:
                        if event.key in (pygame.K_ESCAPE, pygame.K_b) and gate.wizard:
                            # Not while the buttons are being walked. Leaving
                            # half way through leaves a pad half bound, and
                            # every way out of that screen belongs to padmap:
                            # S skips a control, and a long hold finishes.
                            trace.say("gate-key-ignored", key=event.key, state=gate.state)
                        elif event.key in (pygame.K_ESCAPE, pygame.K_b):
                            gate = _leave(pads, gate)
                        elif event.key == pygame.K_s and gate.state == MAPPING:
                            # A control this pad does not have. Every layout here
                            # is a superset of somebody's controller.
                            pads.send({"cmd": "skip_control"})

                for message in pads.poll():
                    gate = apply(gate, message)
                    # Per message, not per frame: the reveal empties because
                    # the readings stop, and asking the gate every frame would
                    # keep answering with the last one for ever.
                    fade.saw(gate.progress, time.monotonic())
                if not pads.connected:
                    _said("padmap went away while the gate was up; starting anyway")
                    break
                gate, command = decide(gate)
                if command is not None:
                    pads.send(command)

                if gate.state in (CHECKING, SEATING, READY):
                    # The same screen the door draws, from the first frame:
                    # this game's controller, and the seats filling in under
                    # it. What was here before was a sentence in the middle of
                    # an empty window asking the same question.
                    _draw_go(
                        screen, font_at, gate, title, cache,
                        joining=fade.now(time.monotonic()),
                        settled=False,
                    )
                else:
                    draw(screen, font_at, gate, title, diagram, fade.now(time.monotonic()))
                pygame.display.flip()
                clock.tick(60)
            if not (gate.state == READY and gate.seated):
                _said(f"no door: {gate.state}, {gate.seated} seated")
                break
            # The door: the bindings on the pad, a press ringed as it happens, a
            # tap of Y to walk the buttons again, and the second that starts.
            verdict, gate = _wait_for_go(screen, font_at, clock, pads, gate, title, cache, hush)
            if verdict == "rebind":
                gate, command = rebind(gate)
                if command is not None:
                    pads.send(command)
                    continue
            if verdict == "map":
                # Somebody joined at the door. Back through the loop, which
                # asks padmap to walk that pad's buttons and then comes here
                # again with both of them seated.
                gate = replace(gate, state=CHECKING)
                continue
            break
    finally:
        hush.release()
        pygame.quit()
        pads.close()
    return 0


class Door:
    """The go screen's decision, one event at a time, so a test can drive it.

    Opened over the pads SDL has now -- the clones padmap just published --
    and asks them what is down. A button already held is the hold that
    finished the wizard and rode through padmap's confirm; it does not
    count, and nothing does until it has come up.
    """

    # What starts the game, held. Not "any button": the door also takes a tap
    # of Y for "walk the buttons again", and with any button arming the hold
    # a thumb that brushed Y on the way to starting went back to the wizard
    # instead -- which reads exactly like bindings not being remembered.
    GO = (sdl_pads.A, sdl_pads.START)

    def __init__(
        self,
        sticks,
        now: float,
        seconds: float = GO_HOLD,
        buttons: dict | None = None,
        buttons_by: dict[int, dict] | None = None,
        names: dict[str, str] | None = None,
    ):
        self.sticks = sticks
        self.hold = GoHold(seconds=seconds, opened=now, held_at_open=sticks.any_button_down())
        # padmap's profile for the seated pad, so a raw input can be named:
        # which control the button under the thumb is. Ringed on the drawing
        # while it is down.
        self.buttons = buttons or {}
        # And each seat's own table where they differ: player one on an Xbox
        # pad and player two on a DualSense do not share button numbers, and
        # naming player two's press from player one's profile named the wrong
        # control on the drawing.
        self.buttons_by = buttons_by or {}
        # padmap's control id -> what this console's drawing calls it. Without
        # it a press answered `leftshoulder` and every label was called `L`,
        # so nothing ever lit: `bindings.pad_controls`.
        self.names = names or {}
        # Which controls each player is holding, for the dots beside the
        # labels. A set, because two thumbs press two things: it was one
        # control per player, and an axis coming back to the middle then took
        # the dot off a button that was still held.
        self.lit_by: dict[int, set[str]] = {}
        # Where each player's sticks are: player -> {"left"|"right": (x, y)},
        # y down. Drawn as a dot inside the ring that replaced that stick's
        # four labels, because a stick is a position and four words are not.
        self.sticks_by: dict[int, dict[str, tuple[float, float]]] = {}
        # When Y went down, for the hold that rebinds.
        self.y_since: float | None = None
        # Said once, when the hold finishes: a trace with sixty lines a second
        # of "done" in it is a trace nobody reads.
        self.said_done = False
        # Since when every pad has had nothing down. None means something is
        # being held right now -- the claim, most likely -- and until this has
        # stood for PAUSE seconds no press is a go. A door that opens onto
        # pads with nothing held starts its pause at once; one that opens onto
        # the button that paired somebody waits for it to come up.
        self.quiet_since: float | None = None if self.hold.held_at_open else now
        # Whose hold is on the clock, and when each seat was last heard from.
        # The go ring used to be drawn in player one's colour at the corner of
        # the screen whoever was holding, which told the second player nothing
        # and the first player something untrue.
        self.holder: int | None = None
        self.pressing: dict[int, float] = {}
        trace.say("door-open", pads=len(sticks), held_at_open=self.hold.held_at_open)

    def handle(self, event, now: float) -> bool:
        """One pygame event. True when the door should close without waiting."""
        if event.type == pygame.JOYDEVICEADDED:
            self.sticks.add(event.device_index)
            # A pad arriving with a button down is the same old hold.
            if self.sticks.any_button_down():
                self.hold.held_at_open = True
                self.hold.armed = False
                trace.say("door-pad-arrived-held")
        elif event.type == pygame.JOYDEVICEREMOVED:
            self.sticks.remove(event.instance_id)
        elif event.type == pygame.QUIT:
            return True
        elif event.type == pygame.KEYDOWN and event.key in (
            pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_ESCAPE, pygame.K_b
        ):
            # Not during the pause. A controller in lizard mode types Enter
            # when A is pressed -- hush.py holds those nodes now, but a key
            # that arrives in the first moment of this screen is far more
            # likely to be the press that paired somebody than a person
            # asking for the game, and this is the screen they just reached.
            if not self.settled(now):
                trace.say("door-key-ignored", key=event.key, quiet=self.quiet_since is not None)
                return False
            trace.say("door-key", key=event.key)
            return True
        elif sdl_pads.button(event) is not None:
            pressed = sdl_pads.button(event)
            if pressed in self.GO and self.settled(now):
                self.hold.pressed(now)
                self.holder = sdl_pads.player(event) or self.holder
            # Y, held, is "walk the buttons again". Tapped it was too easy to
            # mean: a thumb brushing it on the way to A went back to the
            # wizard, which reads exactly like bindings not being remembered.
            if pressed == sdl_pads.Y:
                self.y_since = now
            trace.say(
                "door-press",
                button=pressed,
                player=sdl_pads.player(event),
                armed=self.hold.armed,
                settled=self.settled(now),
                counted=self.hold.since is not None,
                quiet_for=None if self.quiet_since is None else round(now - self.quiet_since, 3),
            )
        elif sdl_pads.released(event):
            self.y_since = None
            self.hold.released(now)
            trace.say("door-release")
        seat = sdl_pads.player(event)

        # One vocabulary per pad, chosen by whether SDL maps it -- not by
        # what kind of event this is. A pad SDL maps says which control it is
        # itself, in the standard layout the binding tables are written
        # against; a pad it does not map can only be named by padmap's
        # capture. Choosing per event ran both for an axis, and one trigger
        # lit two labels: a GameCube pad's right trigger read as R *and* Z.
        known = sdl_pads.mapped(event)
        standard = element_for_button(sdl_pads.button(event))
        if standard and seat is not None:
            self.pressing[seat] = now
            self.lit_by[seat] = self.lit_by.get(seat, set()) | {self._one(standard)}
        gone = element_for_button(sdl_pads.button_up(event))
        if gone and seat is not None:
            self._let_go(seat, [self._one(gone)])

        moved = sdl_pads.axis_move(event) if known else None
        if moved is not None and seat is not None:
            index, value = moved
            self.pressing[seat] = now
            stick, axis = STICK_AXES.get(index, (None, None))
            if stick is not None:
                # Under a deadzone it is the middle: a stick at rest reads a
                # percent or two off centre and a dot that never sits still
                # looks like a fault rather than a reading.
                where = list(self.sticks_by.setdefault(seat, {}).get(stick, (0.0, 0.0)))
                where[axis] = 0.0 if abs(value) < STICK_DEAD else max(-1.0, min(1.0, value))
                self.sticks_by[seat][stick] = (where[0], where[1])
            pushed = element_on_axis(index, value)
            ends = [self._one(name) for name in elements_on_axis(index)]
            if pushed:
                self.lit_by[seat] = self.lit_by.get(seat, set()) | {self._one(pushed)}
                self._let_go(seat, [name for name in ends if name != self._one(pushed)])
            else:
                self._let_go(seat, ends)

        released = sdl_pads.raw_release(event)
        if released is not None and seat is not None and not known:
            # A pad SDL does not map: padmap's capture is the only thing that
            # can name its buttons, and the release has to be named the same
            # way `lit_by` holds them.
            self._let_go(seat, self._named(controls_on(self._table(seat), "button", released)))

        raw = sdl_pads.raw_input(event)
        if raw is not None and not known:
            # Who it was, so the screen can light that seat. An axis coming
            # back to rest is still that seat being heard from: a stick a
            # player waggles to check it works is exactly what this answers.
            if seat is not None:
                self.pressing[seat] = now
            table = self._table(seat)
            kind, index, value = raw
            down = set(self._named(controls_for(table, kind, index, value)))
            # And what that same input drives but is not driving now: an axis
            # crossing back through the middle, a hat let go.
            up = set(self._named(controls_on(table, kind, index))) - down
            if seat is not None:
                if down:
                    self.lit_by[seat] = self.lit_by.get(seat, set()) | down
                self._let_go(seat, up)
        return False

    def _table(self, seat: int | None) -> dict:
        """The profile this seat's presses are named from."""
        return self.buttons_by.get(seat or 0) or self.buttons

    def _named(self, controls: list[str]) -> list[str]:
        """padmap's control ids, as the drawing's labels."""
        return [self.names.get(name, name) for name in controls]

    def _one(self, element: str) -> str:
        """One element, as the drawing's label for it."""
        return self.names.get(element, element)

    def settled(self, now: float) -> bool:
        """Whether the pause is over and a press may start the game.

        Every pad quiet for PAUSE seconds. The claim hold is padmap's quarter
        second and a thumb stays down longer than that, so without this the
        press that paired a controller was still down when the door opened and
        went on to start the game.
        """
        return self.quiet_since is not None and now - self.quiet_since >= PAUSE

    def pausing(self, now: float) -> float:
        """How far through that pause, 0 to 1, for the screen to say so."""
        if self.quiet_since is None:
            return 0.0
        return min(1.0, (now - self.quiet_since) / PAUSE)

    def tick(self, now: float) -> None:
        """The holds, against what is actually down right now.

        Events are not enough. A release can go missing -- padmap republishes
        a clone and the button that was down on the old one never comes up on
        the new, and a Steam Controller forwards state rather than events --
        and a hold that kept its start time then "finished" three seconds
        later without anybody holding anything. That read as the gate opening
        the instant A was touched. The pads are asked instead: nothing down,
        nothing counting.
        """
        if self.sticks.any_button_down():
            if self.quiet_since is not None:
                trace.say("door-busy")
            self.quiet_since = None
            return
        if self.quiet_since is None:
            self.quiet_since = now
            trace.say("door-quiet", pause=PAUSE)
        if self.hold.since is not None:
            self.hold.released(now)
        self.y_since = None

    def _let_go(self, seat: int, controls) -> None:
        left = self.lit_by.get(seat, set()) - set(controls)
        if left:
            self.lit_by[seat] = left
        else:
            self.lit_by.pop(seat, None)

    def done(self, now: float) -> bool:
        finished = self.hold.done(now)
        if finished and not self.said_done:
            self.said_done = True
            trace.say(
                "door-go",
                why="hold",
                held_for=None if self.hold.since is None else round(now - self.hold.since, 3),
                seconds=self.hold.seconds,
                holder=self.holder,
            )
        return finished

    def progress(self, now: float) -> float:
        return self.hold.progress(now)

    def rebinding(self, now: float) -> float:
        """How far through the hold that walks the buttons again, 0 to 1."""
        if self.y_since is None:
            return 0.0
        return min(1.0, max(0.0, (now - self.y_since) / REBIND_HOLD))

    def heard(self, now: float, window: float = PRESS_SHOWN) -> set[int]:
        """The seats that have sent anything in the last moment."""
        return {player for player, when in self.pressing.items() if now - when <= window}


def _wait_for_go(
    screen, font_at, clock, pads: Padmap, gate: Gate, title: str, cache: dict | None = None, hush=None
) -> tuple[str, Gate]:
    """Seated and mapped; the game starts on a three-second hold of A.

    padmap is polled here, which it was not: a second player holding a button
    was claimed by the daemon and nothing on this screen knew, so the seat
    that had just been taken was invisible and the pad that took it did
    nothing. Now the seats are redrawn as they arrive, and a pad that arrives
    with no idea what this console's buttons are sends the gate back to ask.

    Returns "go", "rebind" when Y was held, or "map" for a seat that needs
    walking -- with the gate as the daemon has left it.
    """
    first = gate.seats[0] if gate.seats else None
    profile = profiles.for_pad(first.name) if first else None
    # One profile per seat, by the name padmap gave it: whose press it is
    # decides which table names the control.
    by_seat = {
        # This console's capture, falling back to the universal one, which is
        # what padmap itself falls back to: the top-level table is the
        # universal capture alone, and a pad bound for N64 has controls there
        # that it does not have here.
        seat.player: profiles.bindings(profiles.for_pad(seat.name), gate.scope)
        for seat in gate.seats
        if seat.name
    }
    door = Door(
        sdl_pads.init(), time.monotonic(),
        buttons=profiles.bindings(profile, gate.scope),
        buttons_by=by_seat,
        names=pad_controls(console_for(gate.platform)),
    )
    fade = Fade()
    cache = cache if cache is not None else {}
    while True:
        now = time.monotonic()
        for message in pads.poll():
            gate = apply(gate, message)
            fade.saw(gate.progress, now)
        if not pads.connected:
            _said("padmap went away at the door; starting")
            return "go", gate
        # A pad seated at this screen that has never been mapped for this
        # console: back to the wizard, and back here after it.
        if gate.unmapped is not None:
            return "map", gate
        for event in pygame.event.get():
            if hush is not None and event.type in (pygame.JOYDEVICEADDED, pygame.JOYDEVICEREMOVED):
                hush.refresh()
            if door.handle(event, now):
                trace.say("door-go", why="key-or-quit")
                return "go", gate
        door.tick(now)
        if door.rebinding(now) >= 1.0:
            trace.say("door-rebind")
            return "rebind", gate
        if door.done(now):
            return "go", gate
        _draw_go(
            screen, font_at, gate, title, cache,
            fraction=door.progress(now),
            pressing=door.lit_by,
            sticks=door.sticks_by,
            holder=door.holder,
            heard=door.heard(now),
            joining=fade.now(now),
            settled=door.settled(now),
        )
        pygame.display.flip()
        clock.tick(60)


def _footer(gate: Gate, settled: bool) -> str:
    """The one line under the drawing: what to do next, and only that.

    Three moments, and each says its own: nobody paired yet, somebody paired
    and still holding the button that paired them, and everybody ready.
    """
    if not gate.seats:
        return "hold a button on a controller to join   ·   Esc play without a controller"
    if not settled:
        return "let go of the button   ·   then hold A for three seconds to start"
    return (
        "hold A for three seconds to start   ·   hold Y to change these   ·   "
        "another player: hold a button   ·   Enter or Esc start now"
    )


def _draw_go(
    screen,
    font_at,
    gate: Gate,
    title: str,
    cache: dict,
    fraction: float = 0.0,
    pressing: dict[int, set[str]] | None = None,
    sticks: dict[int, dict[str, tuple[float, float]]] | None = None,
    holder: int | None = None,
    heard: set[int] | None = None,
    joining: float = 0.0,
    settled: bool = True,
) -> None:
    """The gate's one screen: this game's controller, and who is on it.

    There used to be a screen before this one -- a sentence in the middle of
    an empty window asking somebody to hold a button -- and then this. They
    were the same question twice. The pad for the console being played is up
    from the first frame, the seats fill in along the bottom as people pair,
    and the line underneath says which of the three moments this is: nobody
    paired, somebody paired and still holding, everybody ready.

    A hold is drawn on the badge of whoever is holding, not in the corner: the
    corner drawing was player one's colour whoever was pressing, which told
    the second player their pad was doing nothing.
    """
    width, height = screen.get_size()
    try:
        names = ", ".join(seat.name or "pad" for seat in gate.seats)
        draw_diagram(
            screen, assets_dir(), gate.platform, font_at, cache,
            pressing=pressing or {},
            sticks=sticks or {},
            heading=f"{title}  —  {names}" if names else title,
            keys="",
            footer=_footer(gate, settled),
        )
    except Exception as error:  # noqa: BLE001 - a drawing must not start a game
        # The drawing is a courtesy; the gate is not. A missing table or
        # artwork falls back to the words, and the hold still has to be held
        # -- a traceback here once exited the gate and started the game.
        screen.fill(BACKGROUND)
        heading = font_at(34).render(title, True, LABEL_DIM)
        screen.blit(heading, ((width - heading.get_width()) // 2, int(height * 0.10)))
        prompt = font_at(48).render(_footer(gate, settled).split("   ·   ")[0], True, LABEL)
        screen.blit(prompt, ((width - prompt.get_width()) // 2, int(height * 0.40)))
        note = font_at(20).render(f"(no drawing: {error})", True, LABEL_DIM)
        screen.blit(note, ((width - note.get_width()) // 2, int(height * 0.92)))

    # Who has control, in the colours the strip and the grid use, so a second
    # player holding a button sees themselves appear rather than wondering.
    _draw_seats(
        screen, font_at, gate, int(height * 0.80), fraction, holder, heard or set(), joining,
    )


# How much of a settled seat's controller drawing is left. Down from opaque,
# because by this screen the picture has done its job -- the person checked it
# against what is in their hands two screens ago -- and what is worth the eye
# now is the tick saying this one is ready.
SETTLED = 130


def _draw_seats(
    screen,
    font_at,
    gate: Gate,
    middle: int,
    fraction: float = 0.0,
    holder: int | None = None,
    heard: set[int] | None = None,
    joining: float = 0.0,
) -> None:
    """One controller drawing per seat, left to right, in player colours.

    The drawing is the whole badge now. There was a coloured disc beside it
    carrying the player number, then the number went because the order says
    it, and then the disc went too: the colour is on the drawing itself, and
    a picture of the pad in somebody's hands is the thing they can check
    against what they are holding.

    It says three things at once. Dimmed, it is a seat that is settled and
    idle; full colour, it is a seat being pressed right now; and while
    somebody holds A it fills in clockwise from twelve over its own dark
    silhouette -- the same reveal the strip and the launch gate use for a
    hold, so a hold looks like one thing wherever it happens. The green tick
    on its shoulder is "this one is ready".
    """
    width = screen.get_width()
    heard = heard or set()
    height = 44
    step = 96
    # Room for the one arriving, so the row does not jump sideways the moment
    # somebody pairs: a seat that is filling in is already taking its place.
    shown = gate.seated + (1 if joining > 0 else 0)
    left = (width - step * max(1, shown)) // 2 + step // 2
    for index, seat in enumerate(gate.seats):
        centre = (left + index * step, middle)
        colour = colour_for(seat.player)
        ids = devices.ids_for(seat.node)

        if holder == seat.player and fraction > 0:
            draw_reveal(screen, centre, seat.name, colour, fraction, height, ids)
        else:
            icon = icon_surface(seat.name, height, colour, ids)
            if icon is None:
                # No artwork for this pad: a disc in its colour still says a
                # seat is taken, which is the question.
                pygame.draw.aacircle(screen, colour, centre, 13)
            else:
                # Dim while idle, full colour while pressed. Nothing else on
                # this screen moves, so a drawing brightening under a thumb
                # is unmistakably an answer to it.
                icon.set_alpha(255 if seat.player in heard else SETTLED)
                screen.blit(icon, icon.get_rect(center=centre))
                icon.set_alpha(255)

        corner = (centre[0] + height // 2 - 2, centre[1] + height // 3)
        pygame.draw.aacircle(screen, (16, 22, 18), corner, 9)
        draw_tick(screen, corner, 11, SETTLED_GREEN)

    if joining > 0:
        # The seat being claimed right now, in the colour it is about to be:
        # padmap's own progress, drawn where that player will sit.
        centre = (left + gate.seated * step, middle)
        draw_reveal(screen, centre, None, colour_for(gate.seated + 1), joining, height)


def _hold_the_door(title: str, reason: str) -> int:
    """The window when there is no padmap to ask. Counts down, then starts."""
    pygame.init()
    pygame.display.set_caption("GamesOnTheGo")
    screen = pygame.display.set_mode(WINDOW)
    clock = pygame.time.Clock()
    fonts: dict[int, pygame.font.Font] = {}

    def font_at(size: int) -> pygame.font.Font:
        if size not in fonts:
            fonts[size] = pygame.font.Font(None, size)
        return fonts[size]

    deadline = time.monotonic() + COUNTDOWN
    try:
        while time.monotonic() < deadline:
            for event in pygame.event.get():
                if event.type == pygame.QUIT or event.type == pygame.KEYDOWN:
                    return 0
            width, height = screen.get_size()
            screen.fill(BACKGROUND)
            heading = font_at(34).render(title, True, LABEL_DIM)
            screen.blit(heading, ((width - heading.get_width()) // 2, int(height * 0.10)))
            said, footer = without_controllers(reason, deadline - time.monotonic())
            prompt = font_at(48).render(said, True, EMPTY_RING)
            screen.blit(prompt, ((width - prompt.get_width()) // 2, int(height * 0.40)))
            keys = font_at(22).render(footer, True, LABEL_DIM)
            screen.blit(keys, ((width - keys.get_width()) // 2, int(height * 0.92)))
            pygame.display.flip()
            clock.tick(30)
    finally:
        pygame.quit()
    return 0


def _leave(pads: Padmap, gate: Gate) -> Gate:
    """Back out of whatever is open, and let the game start.

    Seating is left open -- it is the picker's and the game's as much as the
    gate's, and a pad held later still takes a seat. A capture is ended,
    since it holds one pad.
    """
    if gate.state == MAPPING:
        pads.send({"cmd": "configure_end"})
    return replace(gate, state=SKIPPED)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gotg-seat",
        description="Check there is a controller for this game, and that it is mapped.",
    )
    parser.add_argument("--platform", default=os.environ.get("GOTG_PLATFORM", ""))
    parser.add_argument("--title", default="")
    args = parser.parse_args(argv)
    try:
        return run(args.platform, args.title)
    except Exception as error:  # noqa: BLE001 - a launch is never blocked by this
        print(f"gotg-seat: {error}; starting anyway", file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
