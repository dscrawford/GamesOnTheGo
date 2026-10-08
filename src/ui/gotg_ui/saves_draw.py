"""Drawing the Saves screen (saves_list.py is what it shows).

The other screens are drawn in draw_grid.py and draw_screens.py; the colours
are the theme's (theme.py) in all of them.
"""

from __future__ import annotations

import pygame

from .saves_list import Saves
from .theme import BACKGROUND, PANEL, TEXT, TEXT_DIM, TILE, TILE_SELECTED

# Rows on screen at once; the list scrolls to keep the lit one among them.
VISIBLE = 6


def draw_saves(screen, font_at, saves: Saves) -> None:
    width, height = screen.get_size()
    screen.fill(BACKGROUND)
    margin = height // 16
    heading = font_at(32).render(f"Saves — {saves.game.title[:50]}", True, TEXT)
    screen.blit(heading, (margin, margin))
    y = margin + heading.get_height() + margin // 2

    listing = saves.listing
    if listing is None or not listing.entries:
        if listing is None and not saves.failed:
            said = "Looking for saves…"
        elif saves.failed:
            said = "The saves could not be listed — the client did not answer."
        else:
            said = "No saves yet. Play, and one is kept each time the game is closed."
        line = font_at(24).render(said, True, TEXT_DIM)
        screen.blit(line, (margin, y))
        _keys(screen, font_at, margin, "B / Escape — back")
        return

    if listing.offline:
        note = font_at(18).render("The service could not be asked: these are this machine's own.", True, TEXT_DIM)
        screen.blit(note, (margin, y))
        y += note.get_height() + 12

    row_h = font_at(26).get_height() + font_at(18).get_height() + 28
    first = max(0, min(saves.selected - VISIBLE // 2, len(listing.entries) - VISIBLE))
    for index in range(first, min(first + VISIBLE, len(listing.entries))):
        entry = listing.entries[index]
        lit = index == saves.selected
        box = pygame.Rect(margin, y, width - 2 * margin, row_h - 10)
        pygame.draw.rect(screen, TILE_SELECTED if lit else TILE, box, border_radius=10)
        title = font_at(26).render(saves.title(entry), True, TEXT)
        screen.blit(title, (box.x + 20, box.y + 8))
        detail = font_at(18).render(saves.describe(entry), True, TEXT if lit else TEXT_DIM)
        screen.blit(detail, (box.x + 20, box.y + 12 + title.get_height()))
        y += row_h

    if saves.confirming and saves.entry is not None:
        _prompt(screen, font_at, saves.title(saves.entry))
        return
    _keys(screen, font_at, margin, "Up / Down — choose   ·   A / Enter — load this save   ·   B / Escape — back")


def _prompt(screen, font_at, when: str) -> None:
    """What loading means, said before it is done."""
    width, height = screen.get_size()
    box = pygame.Rect(width // 6, height // 3, width * 2 // 3, height // 3)
    pygame.draw.rect(screen, PANEL, box, border_radius=14)
    pygame.draw.rect(screen, TILE_SELECTED, box, width=3, border_radius=14)
    lines = (
        (f"Load the save from {when}?", 30, TEXT),
        ("What you have now is kept as an archive on this machine,", 20, TEXT_DIM),
        ("and can be loaded again from this list.", 20, TEXT_DIM),
        ("A / Enter — load and play   ·   B / Escape — not now", 20, TEXT),
    )
    y = box.y + box.height // 8
    for text, size, colour in lines:
        line = font_at(size).render(text, True, colour)
        screen.blit(line, (box.x + (box.width - line.get_width()) // 2, y))
        y += line.get_height() + box.height // 12


def _keys(screen, font_at, margin: int, text: str) -> None:
    hint = font_at(18).render(text, True, TEXT_DIM)
    screen.blit(hint, (margin, screen.get_height() - margin - hint.get_height()))
