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
from .controllers import Diagram, assets_dir, draw_reveal, draw_ring, draw_tick, icon_surface
from .controllers import draw as draw_diagram
from .gate import (
    CHECKING,
    MAPPING,
    READY,
    SEATING,
    SKIPPED,
    Gate,
    GoHold,
    apply,
    decide,
    rebind,
    without_controllers,
)
from .padmap import Padmap, ensure_daemon
from .padstrip import EMPTY_RING, LABEL, LABEL_DIM, PANEL, colour_for
from .padstrip import READY as SETTLED_GREEN  # gate.READY is a state; this is a colour
from .pressing import controls_for

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


def draw(screen, font_at, gate: Gate, title: str, diagram: Diagram | None = None) -> None:
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
        draw_reveal(screen, centre, None, colour_for(1), gate.progress, int(height * 0.22))

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

    keys = "S skip this button   Esc play without it" if gate.state == MAPPING \
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

    def stop_listening() -> None:
        """Seating closed, for the length of the game.

        It costs a hundred milliseconds of input lag. padmap rescans every
        input device on every 20 ms tick while seating is open, and one scan
        -- a udev walk plus a liveness probe of every hidraw node -- takes
        about 100 ms on this machine, so the daemon's loop never gets back to
        forwarding in time. Measured through a clone: 0.03 ms for a press
        with seating closed, 108 ms with it open, against 0.01 ms for the
        same press read straight off its own device. Melee felt like treacle
        and it was this.

        docs/requests/seating-costs-the-game-its-input.md asks padmap to
        throttle that scan. When it does, this goes and a pad can join
        mid-level again -- the e2e has a strict xfail watching for it.
        """
        pads.send({"cmd": "seating", "open": False})

    try:
        while True:
            while not gate.done:
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        gate = _leave(pads, gate)
                    elif event.type == pygame.KEYDOWN:
                        if event.key in (pygame.K_ESCAPE, pygame.K_b):
                            gate = _leave(pads, gate)
                        elif event.key == pygame.K_s and gate.state == MAPPING:
                            # A control this pad does not have. Every layout here
                            # is a superset of somebody's controller.
                            pads.send({"cmd": "skip_control"})

                for message in pads.poll():
                    gate = apply(gate, message)
                if not pads.connected:
                    _said("padmap went away while the gate was up; starting anyway")
                    break
                gate, command = decide(gate)
                if command is not None:
                    pads.send(command)

                draw(screen, font_at, gate, title, diagram)
                pygame.display.flip()
                clock.tick(60)
            if not (gate.state == READY and gate.seated):
                _said(f"no door: {gate.state}, {gate.seated} seated")
                stop_listening()
                break
            # The door: the bindings on the pad, a press ringed as it happens, a
            # tap of Y to walk the buttons again, and the second that starts.
            verdict, gate = _wait_for_go(screen, font_at, clock, pads, gate, title)
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
            stop_listening()
            break
    finally:
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
        self.lit: str | None = None
        # The control under each player's thumb, for the dots beside the
        # labels. Separate from `lit`, which is one ring on one control.
        self.lit_by: dict[int, str] = {}
        self.y_down = False
        self.rebind = False
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
            return True
        elif sdl_pads.button(event) is not None:
            pressed = sdl_pads.button(event)
            if pressed in self.GO:
                self.hold.pressed(now)
                self.holder = sdl_pads.player(event) or self.holder
            # Y, tapped, is "walk the buttons again", and it cannot also be
            # the hold that starts the game.
            if pressed == sdl_pads.Y:
                self.y_down = True
            trace.say(
                "door-press", button=pressed, armed=self.hold.armed,
                counted=self.hold.since is not None,
            )
        elif sdl_pads.released(event):
            if self.y_down and not self.hold.done(now):
                self.rebind = True
            self.y_down = False
            self.hold.released(now)
            self.lit = None
            self.lit_by.pop(sdl_pads.player(event) or 0, None)
            trace.say("door-release")
        raw = sdl_pads.raw_input(event)
        if raw is not None:
            # Who it was, so the screen can light that seat. An axis coming
            # back to rest is still that seat being heard from: a stick a
            # player waggles to check it works is exactly what this answers.
            seat = sdl_pads.player(event)
            if seat is not None:
                self.pressing[seat] = now
            named = controls_for(self.buttons_by.get(seat or 0) or self.buttons, *raw)
            if named:
                self.lit = named[0]
                if seat is not None:
                    self.lit_by[seat] = named[0]
            elif raw[0] != "button":
                # An axis or hat back at rest clears it; a button's rest is
                # its release, handled above.
                self.lit = None
                if seat is not None:
                    self.lit_by.pop(seat, None)
        return False

    def done(self, now: float) -> bool:
        return self.hold.done(now)

    def progress(self, now: float) -> float:
        return self.hold.progress(now)

    def heard(self, now: float, window: float = PRESS_SHOWN) -> set[int]:
        """The seats that have sent anything in the last moment."""
        return {player for player, when in self.pressing.items() if now - when <= window}


def _wait_for_go(screen, font_at, clock, pads: Padmap, gate: Gate, title: str) -> tuple[str, Gate]:
    """Seated and mapped; the game starts when somebody holds A for a second.

    padmap is polled here, which it was not: a second player holding a button
    was claimed by the daemon and nothing on this screen knew, so the seat
    that had just been taken was invisible and the pad that took it did
    nothing. Now the seats are redrawn as they arrive, and a pad that arrives
    with no idea what this console's buttons are sends the gate back to ask.

    Returns "go", "rebind" when Y was tapped, or "map" for a seat that needs
    walking -- with the gate as the daemon has left it.
    """
    first = gate.seats[0] if gate.seats else None
    profile = profiles.for_pad(first.name) if first else None
    # One profile per seat, by the name padmap gave it: whose press it is
    # decides which table names the control.
    by_seat = {
        seat.player: (profiles.for_pad(seat.name) or {}).get("buttons") or {}
        for seat in gate.seats
        if seat.name
    }
    door = Door(
        sdl_pads.init(), time.monotonic(),
        buttons=(profile or {}).get("buttons") or {},
        buttons_by=by_seat,
    )
    cache: dict = {}
    while True:
        now = time.monotonic()
        for message in pads.poll():
            gate = apply(gate, message)
        if not pads.connected:
            _said("padmap went away at the door; starting")
            return "go", gate
        # A pad seated at this screen that has never been mapped for this
        # console: back to the wizard, and back here after it.
        if gate.unmapped is not None:
            return "map", gate
        for event in pygame.event.get():
            if door.handle(event, now):
                return "go", gate
            if door.rebind:
                return "rebind", gate
        if door.done(now):
            return "go", gate
        _draw_go(
            screen, font_at, gate, title, door.progress(now), door.lit_by, cache,
            holder=door.holder, heard=door.heard(now),
        )
        pygame.display.flip()
        clock.tick(60)


def _draw_go(
    screen,
    font_at,
    gate: Gate,
    title: str,
    fraction: float,
    pressing: dict[int, str],
    cache: dict,
    holder: int | None = None,
    heard: set[int] | None = None,
) -> None:
    """The door: what every button does on this pad, the one being pressed
    ringed, and the seats along the bottom.

    The hold is drawn around the badge of whoever is holding, not in the
    corner: the corner drawing was player one's colour whoever was pressing,
    which told the second player their pad was doing nothing.
    """
    width, height = screen.get_size()
    try:
        names = ", ".join(seat.name or "pad" for seat in gate.seats)
        draw_diagram(
            screen, assets_dir(), gate.platform, font_at, cache,
            pressing=pressing,
            heading=f"{title}  —  {names}",
            keys="",
            footer=(
                "hold A for a second to start   ·   tap Y to change these   ·   "
                "another player: hold a button   ·   Enter or Esc start now"
            ),
        )
    except Exception as error:  # noqa: BLE001 - a drawing must not start a game
        # The drawing is a courtesy; the door is not. A missing table or
        # artwork falls back to the words, and the second still has to be
        # held -- a traceback here once exited the gate and started the game.
        screen.fill(BACKGROUND)
        heading = font_at(34).render(title, True, LABEL_DIM)
        screen.blit(heading, ((width - heading.get_width()) // 2, int(height * 0.10)))
        prompt = font_at(48).render("hold A for a second to start", True, LABEL)
        screen.blit(prompt, ((width - prompt.get_width()) // 2, int(height * 0.40)))
        note = font_at(20).render(f"(no drawing: {error})", True, LABEL_DIM)
        screen.blit(note, ((width - note.get_width()) // 2, int(height * 0.92)))

    # Who has control, in the colours the strip and the grid use, so a second
    # player holding a button sees themselves appear rather than wondering.
    _draw_seats(screen, font_at, gate, int(height * 0.80), fraction, holder, heard or set())


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
) -> None:
    """A badge and a controller per seat, left to right, player colours.

    Three things on one badge, and they do not collide: the drawing behind it
    faded, a green tick over it saying this seat is settled, and -- while
    somebody holds A -- a ring closing around the one badge that is holding.
    A press on any seat lights its own badge for a moment, which is the only
    thing on this screen that answers "is my controller doing anything".
    """
    width = screen.get_width()
    heard = heard or set()
    radius = 22
    step = radius * 2 + 78
    left = (width - step * max(1, gate.seated)) // 2 + step // 2
    for index, seat in enumerate(gate.seats):
        centre = (left + index * step, middle)
        colour = colour_for(seat.player)

        icon = icon_surface(seat.name, 40, colour, devices.ids_for(seat.node))
        if icon is not None:
            icon.set_alpha(SETTLED)
            screen.blit(icon, icon.get_rect(center=(centre[0] + radius + 30, middle)))
            icon.set_alpha(255)

        if holder == seat.player and fraction > 0:
            draw_ring(screen, centre, radius + 10, colour, fraction, 5)
        pygame.draw.aacircle(screen, colour, centre, radius)
        number = font_at(28).render(str(seat.player), True, (20, 20, 24))
        screen.blit(number, number.get_rect(center=centre))

        # The tick sits on the badge's shoulder rather than across it: over
        # the number it hid the one thing the badge is for, and a player
        # counting seats on a sofa reads the number first.
        corner = (centre[0] + radius - 3, centre[1] + radius - 3)
        pygame.draw.aacircle(screen, (16, 22, 18), corner, 11)
        draw_tick(screen, corner, 13, SETTLED_GREEN)

        if seat.player in heard:
            # A press, on the seat that made it. Drawn as this seat's own
            # colour, brightly, outside the badge: nothing else on this screen
            # moves, so a flicker here is unmistakably an answer to a thumb.
            pygame.draw.aacircle(screen, colour, (centre[0], centre[1] - radius - 16), 8)


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
