"""Drawing the on-screen keyboard: SDL and pygame only. The keys are keyboard.py's."""

from __future__ import annotations

import pygame

from .draw_grid import SCALED
from .keyboard import LABELS, SPACE, Keyboard
from .theme import BACKGROUND, PANEL, TEXT, TEXT_DIM, TILE, TILE_SELECTED

# The bottom row in units of a letter key: Space wide enough to find.
_WEIGHTS = {SPACE: 4}
_FOOTER = "A type   X delete   Y space   B or Close brings it down"


def key_rects(keyboard: Keyboard, font_at, size) -> list[list[tuple[int, int, int, int]]]:
    """One rect per key, by row. Computed here and only here, so the drawing
    and the pointer cannot disagree about where a key is."""
    width, height = size
    key_h = font_at(26).get_height() + 14
    gap = 6
    board_w = min(int(width * 0.8), 900)
    units = max(len(row) for row in keyboard.rows)
    unit_w = (board_w - gap * (units - 1)) // units
    left = (width - board_w) // 2
    bottom = height - font_at(22).get_height() - 28
    top = bottom - len(keyboard.rows) * (key_h + gap)
    rects = []
    for r, row in enumerate(keyboard.rows):
        y = top + r * (key_h + gap)
        weights = [_WEIGHTS.get(key, 1) for key in row]
        spare = units - sum(weights)
        # Whatever the row's weights leave over goes to the wide key, so every
        # row ends at the same right edge.
        if spare and SPACE in row:
            weights[row.index(SPACE)] += spare
        x = left
        line = []
        for weight in weights:
            w = unit_w * weight + gap * (weight - 1)
            line.append((x, y, w, key_h))
            x += w + gap
        rects.append(line)
    return rects


def key_at(keyboard: Keyboard, font_at, size, pos) -> tuple[int, int] | None:
    """Which key a point is on, as (row, col), or None for the gaps."""
    for r, row in enumerate(key_rects(keyboard, font_at, size)):
        for c, (x, y, w, h) in enumerate(row):
            if x <= pos[0] < x + w and y <= pos[1] < y + h:
                return r, c
    return None


def draw_keyboard(screen, font_at, keyboard: Keyboard) -> None:
    """The keys over the bottom of whatever is up, the text being typed above
    them, and the hints under them."""
    width, height = screen.get_size()
    rects = key_rects(keyboard, font_at, (width, height))
    line = font_at(30)
    text_h = line.get_height() + 16
    top = rects[0][0][1] - text_h - 16

    wash = SCALED.get(("keyboard", width, height - top))
    if wash is None:
        wash = pygame.Surface((width, height - top), pygame.SRCALPHA)
        wash.fill((*BACKGROUND, 240))
        SCALED.put(("keyboard", width, height - top), wash)
    screen.blit(wash, (0, top))

    board_x, board_w = rects[0][0][0], rects[0][-1][0] + rects[0][-1][2] - rects[0][0][0]
    pygame.draw.rect(screen, PANEL, (board_x, top, board_w, text_h), border_radius=8)
    pygame.draw.rect(screen, TEXT_DIM, (board_x, top, board_w, text_h), width=1, border_radius=8)
    typed = line.render(f"{keyboard.text}_", True, TEXT)
    screen.blit(typed, (board_x + 14, top + (text_h - typed.get_height()) // 2))

    for r, row in enumerate(keyboard.rows):
        for c, key in enumerate(row):
            x, y, w, h = rects[r][c]
            selected = (r, c) == (keyboard.row, keyboard.col)
            pygame.draw.rect(screen, TILE_SELECTED if selected else TILE, (x, y, w, h), border_radius=6)
            label = font_at(26).render(LABELS.get(key, key), True, TEXT if selected else TEXT_DIM)
            screen.blit(label, (x + (w - label.get_width()) // 2, y + (h - label.get_height()) // 2))

    footer = font_at(22).render(_FOOTER, True, TEXT_DIM)
    last = rects[-1][0]
    screen.blit(footer, ((width - footer.get_width()) // 2, last[1] + last[3] + 12))
