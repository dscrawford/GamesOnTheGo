"""`gotg-seat` — the screen between pressing play and the game starting.

Runs only when it has something to ask. A machine with a controller that is
already mapped for this console never sees it: the check is a socket round trip
and an exit, fast enough to sit in front of every launch.

Exits 0 whatever happens, including when padmap is not running at all. This is
in the way of a game somebody asked for, and a controller problem is not a
reason to refuse to start one -- the worst case is the launch that would have
happened anyway, which is what it was before this existed.
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

from .controllers import Diagram, assets_dir
from .gate import MAPPING, SEATING, SKIPPED, Gate, apply, decide
from .padmap import Padmap, ensure_daemon
from .padstrip import EMPTY_RING, LABEL, LABEL_DIM, PANEL, colour_for

WINDOW = tuple(config.get("theme.window", [1280, 800]))
BACKGROUND = config.colour("theme.colours.background", (18, 18, 20))

# How long to wait for the daemon to say anything at all before giving up and
# launching. A socket that is there answers in milliseconds; this is the bound
# on a socket that is there and silent.
FIRST_STATE_TIMEOUT = float(config.get("theme.timeouts.first_state", 3.0))


def draw(screen, font_at, gate: Gate, title: str, diagram: Diagram | None = None) -> None:
    width, height = screen.get_size()
    screen.fill(BACKGROUND)

    heading = font_at(34).render(title, True, LABEL_DIM)
    screen.blit(heading, ((width - heading.get_width()) // 2, int(height * 0.10)))

    prompt = font_at(56).render(gate.prompt, True, LABEL)
    screen.blit(prompt, ((width - prompt.get_width()) // 2, int(height * 0.22)))

    if gate.state == SEATING:
        # One ring, because one controller is what this is waiting for. The
        # colour is player one's, so the seat somebody is about to take looks
        # like the seat they will have.
        centre = (width // 2, int(height * 0.52))
        pygame.draw.circle(screen, PANEL, centre, 46)
        pygame.draw.aacircle(screen, colour_for(1), centre, 46)
        pygame.draw.aacircle(screen, colour_for(1), centre, 45)

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


def run(platform: str, title: str) -> int:
    """Ask what needs asking, then get out of the way. Always returns 0."""
    trouble = ensure_daemon()
    pads = Padmap()
    if trouble is not None or not pads.connect():
        # No daemon, no questions to ask. The game gets whatever SDL finds by
        # itself, exactly as it did before padmap.
        print(f"gotg-seat: {trouble or pads.error}; starting anyway", file=sys.stderr)
        return 0

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
        return 0

    gate, command = decide(gate)
    if gate.done:
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

    try:
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
                break
            gate, command = decide(gate)
            if command is not None:
                pads.send(command)

            draw(screen, font_at, gate, title, diagram)
            pygame.display.flip()
            clock.tick(60)
    finally:
        pygame.quit()
        pads.close()
    return 0


def _leave(pads: Padmap, gate: Gate) -> Gate:
    """Back out of whatever is open, and let the game start."""
    if gate.state == SEATING:
        pads.send({"cmd": "cancel"})
    elif gate.state == MAPPING:
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
