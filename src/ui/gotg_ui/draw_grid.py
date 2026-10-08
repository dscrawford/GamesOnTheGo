"""Drawing the grid, the shelf and the per-game menu: SDL and pygame only.

Pulled out of app.py with no change of behaviour, so that file is the loop and
the models it asks. Nothing here decides anything: the layout is layout.py's,
the text gridview.py's, what is selected the models'.
"""

from __future__ import annotations

import math

import pygame

from .browser import SHELF
from .gridview import GridView, row_under_text, status_line
from .layout import corner, grid, shelf, shelf_at, tile_at
from .menu import Menu
from .recent import Recent
from .theme import ATTENTION, BACKGROUND, TEXT, TEXT_DIM, TILE, TILE_SELECTED


def _fit(font_at, text: str, width: int, size: int):
    """The largest of a font that renders `text` inside `width`, down to tiny.

    Titles are free text from the catalog — "The Legend of Zelda: Ocarina of
    Time" next to "Tetris" — so a fixed size either wraps the long ones or
    wastes the short ones.
    """
    while size > 8:
        font = font_at(size)
        if font.size(text)[0] <= width:
            return font
        size -= 1
    return font_at(8)


def draw_badge(screen, tile) -> None:
    """Download-arrow badge, top-left: drawn rather than a loaded asset, so it
    scales with the tile and reads over art of any colour."""
    r = max(10, tile.width // 14)
    cx, cy = tile.x + r + 6, tile.y + r + 6
    pygame.draw.aacircle(screen, BACKGROUND, (cx, cy), r + 2)
    pygame.draw.aacircle(screen, TILE_SELECTED, (cx, cy), r)
    shaft = r // 2
    head = r // 2
    stroke = max(2, r // 5)
    pygame.draw.line(screen, TEXT, (cx, cy - shaft), (cx, cy + shaft // 2), stroke)
    # The arrow head: filled, then an aa outline over it, because pygame has
    # no anti-aliased fill for a polygon and the diagonal edges are what show.
    pygame.draw.polygon(screen, TEXT, [(cx - head, cy), (cx + head, cy), (cx, cy + head)])
    pygame.draw.aalines(screen, TEXT, True, [(cx - head, cy), (cx + head, cy), (cx, cy + head)])
    pygame.draw.line(screen, TEXT, (cx - head, cy + head + 2), (cx + head, cy + head + 2), stroke)


def draw_alert_badge(screen, tile) -> None:
    """An exclamation mark where the download arrow would be: this game is
    here, and the library would build it differently now, or has attached a
    release the install lacks (updates.py)."""
    r = max(10, tile.width // 14)
    cx, cy = tile.x + r + 6, tile.y + r + 6
    pygame.draw.aacircle(screen, BACKGROUND, (cx, cy), r + 2)
    pygame.draw.aacircle(screen, ATTENTION, (cx, cy), r)
    stroke = max(2, r // 4)
    pygame.draw.line(screen, BACKGROUND, (cx, cy - r // 2), (cx, cy + r // 6), stroke)
    pygame.draw.aacircle(screen, BACKGROUND, (cx, cy + r // 2), max(1, stroke // 2 + 1))


_chip_labels: dict[str, object] = {}


def draw_chip(screen, font_at, text: str | None):
    """The words at the top right -- "Update available", and what follows a
    press -- on a rounded chip in the attention colour, or nothing. Returns
    the chip's Tile, for a click to be matched against; None when nothing.
    The label is rendered once per wording: it is on every frame for as
    long as it shows."""
    if not text:
        return None
    width, height = screen.get_size()
    label = _chip_labels.get(text)
    if label is None:
        label = _chip_labels[text] = font_at(18).render(text, True, BACKGROUND)
    chip = corner(width, height, label.get_width(), label.get_height())
    pygame.draw.rect(screen, ATTENTION, chip.rect, border_radius=chip.height // 2)
    at = (chip.x + (chip.width - label.get_width()) // 2, chip.y + (chip.height - label.get_height()) // 2)
    screen.blit(label, at)
    return chip


def draw_ring(screen, centre, radius: int, fraction: float | None, failed: bool) -> None:
    """An install on its way: a ring filling clockwise from the top, or a
    short arc going round when there are no figures yet (a build). Red and
    whole when it failed."""
    stroke = max(3, radius // 5)
    box = pygame.Rect(centre[0] - radius, centre[1] - radius, 2 * radius, 2 * radius)
    pygame.draw.aacircle(screen, BACKGROUND, centre, radius + stroke)
    pygame.draw.circle(screen, TILE, centre, radius, stroke)
    colour = (224, 96, 96) if failed else TILE_SELECTED
    if fraction is None:
        start = -(pygame.time.get_ticks() / 400.0) % (2 * math.pi)
        pygame.draw.arc(screen, colour, box, start, start + math.pi / 2, stroke)
    elif fraction > 0:
        # pygame's arcs run anticlockwise from the +x axis; this one runs
        # clockwise from twelve, which is how a clock and a person read it.
        top = math.pi / 2
        pygame.draw.arc(screen, colour, box, top - 2 * math.pi * fraction, top, stroke)


# Every cover that has been scaled to fit a tile, by game and size.
#
# The picker was rescaling all of them on every frame: 7.4 ms of a 16.7 ms
# frame on a Steam Deck for the grid's ten, 9.0 ms for the shelf's twenty-four.
# Art does not change between frames and neither does a tile, so the second
# scale of the same pair is work nobody asked for.
#
# Module level rather than passed around: two views and a hero all want the
# same surfaces, and threading a cache through every drawing function is how
# one of them ends up with its own.
SCALED = Recent(96)


def fitted(picture, key, width: int, height: int):
    """`picture` scaled to fit a `width` x `height` box, kept for next frame.

    Fitted, never stretched: a tile is 2:3 and a cartridge box is landscape, so
    most of this library's art arrives the wrong shape for its slot and scaling
    to fill would squash every one.
    """
    pw, ph = picture.get_size()
    scale = min(width / pw, height / ph)
    size = (max(1, int(pw * scale)), max(1, int(ph * scale)))
    remembered = SCALED.get((key, size))
    if remembered is not None:
        return remembered
    return SCALED.put((key, size), pygame.transform.smoothscale(picture, size))


def filling(picture, key, width: int, height: int):
    """`picture` scaled so it *covers* a box, for cropping to a square icon.

    The other half of `fitted`: that one leaves bars, which is right for a
    cover in a tile and wrong for a 40-pixel icon in a list row.
    """
    pw, ph = picture.get_size()
    scale = max(width / pw, height / ph)
    size = (max(1, int(pw * scale)), max(1, int(ph * scale)))
    remembered = SCALED.get((key, "fill", size))
    if remembered is not None:
        return remembered
    return SCALED.put((key, "fill", size), pygame.transform.smoothscale(picture, size))


def view_rects(browser, size) -> list:
    """The rectangles the view on screen draws its games in.

    Asked of the view rather than assumed to be the grid's. The menu anchors
    to one of these, and the shelf's twelve rows are not the grid's ten tiles:
    opening a menu on the eleventh row indexed past the end of a list that was
    not the one on screen, and the picker died with an IndexError.
    """
    if browser.view == SHELF:
        _hero, rows = shelf(*size)
        return rows
    return grid(*size)


def hovering(browser, pos, size) -> int | None:
    """Which cover the pointer is on, in whichever view is drawn.

    Asked of the view rather than assumed, because the two have different
    rectangles and a pointer answered by the wrong one selects a game three
    along from the one it is over.
    """
    finder = shelf_at if browser.view == SHELF else tile_at
    return finder(*pos, *size)


def draw_row(
    screen,
    font_at,
    row,
    game,
    picture,
    selected: bool,
    under: str,
    ring: tuple[float | None, bool] | None = None,
) -> None:
    """One line of the list: an icon, a title, and `under` it (the platform and
    what is going on, from `gridview.row_under_text`).

    The icon is the game's own art cropped to a square rather than fitted into
    one. A row is wide and short, and art letterboxed into that leaves a
    postage stamp with bars either side; a square crop of a cover is what
    Steam's list shows and what reads at this size.
    """
    if selected:
        pygame.draw.rect(screen, TILE_SELECTED, row.rect, border_radius=8)

    side = max(8, row.height - 8)
    icon = pygame.Rect(row.x + 6, row.y + 4, side, side)
    if picture is not None:
        art_surface = filling(picture, game.key, side, side)
        screen.blit(art_surface, icon.topleft, pygame.Rect(0, 0, side, side))
    else:
        pygame.draw.rect(screen, TILE, icon, border_radius=4)

    left = icon.right + 12
    room = max(24, row.x + row.width - left - 12)
    text = game.title[:120]
    size = max(14, min(24, row.height // 2))
    title = font_at(size).render(text, True, TEXT)
    if title.get_width() > room:
        title = _fit(font_at, text, room, size).render(text, True, TEXT)
    screen.blit(title, (left, row.y + 4))

    if ring is not None:
        fraction, failed = ring
        r = max(6, side // 3)
        draw_ring(screen, (row.x + row.width - r - 12, row.y + row.height // 2), r, fraction, failed)
    small = font_at(max(11, size - 8)).render(under, True, TEXT_DIM)
    screen.blit(small, (left, row.y + 6 + title.get_height()))


def draw_shelf(screen, font_at, view: GridView):
    """The list on the left, and the art of the one under the cursor on the
    right — the other way to look at the same library."""
    state, art, installed, rings, outdated = view.state, view.art, view.installed, view.rings, view.outdated
    width, height = screen.get_size()
    screen.fill(BACKGROUND)
    page = state.page
    hero, rows = shelf(width, height)

    chosen = page[state.selected] if 0 <= state.selected < len(page) else None
    if chosen is not None:
        picture = art.get(chosen.key) if art else None
        if picture is not None:
            art_surface = fitted(picture, chosen.key, hero.width, hero.height)
            size = art_surface.get_size()
            screen.blit(
                art_surface,
                (hero.x + (hero.width - size[0]) // 2, hero.y + (hero.height - size[1]) // 2),
            )
        else:
            # No art anywhere: the title, large, in the space the art would
            # have had. Better than an empty half of the screen.
            pygame.draw.rect(screen, TILE, hero.rect, border_radius=12)
            text = chosen.title[:120]
            shown = _fit(font_at, text, hero.width - 32, 44).render(text, True, TEXT)
            screen.blit(
                shown,
                (hero.x + (hero.width - shown.get_width()) // 2,
                 hero.y + hero.height // 2 - shown.get_height() // 2),
            )

    for index, row in enumerate(rows):
        if index >= len(page):
            break
        game = page[index]
        ring = rings.get(game.key) if rings else None
        under = row_under_text(
            game, ring, bool(installed and game.key in installed), bool(outdated and game.key in outdated)
        )
        draw_row(screen, font_at, row, game, art.get(game.key) if art else None, index == state.selected, under, ring)

    if view.status:
        shown = font_at(22).render(view.status, True, TEXT_DIM)
        screen.blit(shown, (rows[0].x, height - shown.get_height() - 10))
    return draw_chip(screen, font_at, view.chip)


def draw(screen, font_at, view: GridView, typing: str | None = None, menu=None):
    state, art, installed, rings, outdated = view.state, view.art, view.installed, view.rings, view.outdated
    width, height = screen.get_size()
    screen.fill(BACKGROUND)
    page = state.page
    tiles = grid(width, height)
    # One translucent wash per tile size, cached: while the menu is open every
    # other tile drops to ~90% so the chosen one reads as chosen.
    dim = None
    if menu is not None:
        # Cached, which the comment here used to claim and the code did not do:
        # it built one of these every frame the menu was open.
        dim = SCALED.get(("dim", tiles[0].width, tiles[0].height))
        if dim is None:
            dim = pygame.Surface((tiles[0].width, tiles[0].height), pygame.SRCALPHA)
            dim.fill((*BACKGROUND, 26))
            SCALED.put(("dim", tiles[0].width, tiles[0].height), dim)

    for index, tile in enumerate(tiles):
        if index >= len(page):
            break
        game = page[index]
        selected = index == state.selected
        picture = art.get(game.key) if art else None

        if picture is not None:
            art_surface = fitted(picture, game.key, tile.width, tile.height)
            size = art_surface.get_size()
            pygame.draw.rect(screen, TILE, tile.rect, border_radius=8)
            screen.blit(
                art_surface,
                (tile.x + (tile.width - size[0]) // 2, tile.y + (tile.height - size[1]) // 2),
            )
        else:
            pygame.draw.rect(screen, TILE_SELECTED if selected else TILE, tile.rect, border_radius=8)
            # No picture — the title *is* the tile, which is also what a game
            # with no art anywhere falls back to for good.
            inner = tile.width - 16
            # Bounded: titles have no server-side length cap, and font.render
            # on a pathological one allocates a surface megapixels wide.
            text = game.title[:120]
            title = font_at(20).render(text, True, TEXT)
            if title.get_width() > inner:
                title = _fit(font_at, text, inner, 20).render(text, True, TEXT)
            screen.blit(title, (tile.x + 8, tile.y + tile.height // 2 - title.get_height() // 2))
            platform = font_at(14).render(game.platform, True, TEXT_DIM)
            screen.blit(platform, (tile.x + 8, tile.y + tile.height - platform.get_height() - 8))

        if installed and game.key in installed:
            if outdated and game.key in outdated:
                draw_alert_badge(screen, tile)
            else:
                draw_badge(screen, tile)
        if rings and game.key in rings:
            fraction, failed = rings[game.key]
            radius = max(14, tile.width // 5)
            draw_ring(screen, (tile.x + tile.width // 2, tile.y + tile.height // 2), radius, fraction, failed)
            if fraction is not None and not failed:
                pct = font_at(max(14, radius // 2)).render(f"{int(fraction * 100)}%", True, TEXT)
                screen.blit(
                    pct,
                    (tile.x + (tile.width - pct.get_width()) // 2, tile.y + (tile.height - pct.get_height()) // 2),
                )
        if selected:
            pygame.draw.rect(screen, TEXT, tile.rect, width=3, border_radius=8)
        if dim is not None and index != menu.tile_index:
            screen.blit(dim, (tile.x, tile.y))

    # The hovered game's full title, in the top strip the grid never uses.
    # Tiles shrink long titles toward unreadable and art hides them entirely;
    # the selection is the one game whose whole name is worth a line, and
    # hover moves the selection, so pointer and stick share it.
    # The chip shares that strip; the title fits in what the chip leaves.
    chip_rect = draw_chip(screen, font_at, view.chip)
    if state.game is not None:
        text = state.game.title[:200]
        room = width - 48 - (2 * (chip_rect.width + 24) if chip_rect is not None else 0)
        label = _fit(font_at, text, max(120, room), 26).render(text, True, TEXT)
        screen.blit(label, ((width - label.get_width()) // 2, (tiles[0].y - label.get_height()) // 2))

    # While typing, the search box replaces the status: see status_line.
    status, bright = status_line(state, view.status, typing)
    label = font_at(18).render(status, True, TEXT if bright else TEXT_DIM)
    screen.blit(label, (label.get_height(), height - label.get_height() * 2))
    return chip_rect

def menu_rects(menu: Menu, tiles, font_at, bounds=None) -> list[tuple[int, int, int, int]]:
    """One rect per action row, beside the tile on the side with room.

    Computed here and only here, so the drawing and the pointer hit-testing
    cannot disagree about where a row is.

    `bounds` is the window, and the panel is kept inside it. In a grid of two
    rows a panel centred on a tile always fitted; against the twelfth row of a
    list it hangs off the bottom of the screen, and the verbs at the end of it
    -- uninstall among them -- cannot be reached or read.
    """
    # Clamped rather than indexed blind. The caller passes the view's own
    # rectangles, so this should always be in range -- and if a view ever grows
    # a shape nobody updated, a menu in the wrong place beats a traceback on a
    # television.
    tile = tiles[min(menu.tile_index, len(tiles) - 1)]
    row_h = font_at(22).get_height() + 14
    width = max(font_at(22).size(label)[0] for label, _ in menu.actions) + 32
    height = row_h * len(menu.actions) + 8
    gap = 10
    x = tile.x + tile.width + gap if menu.side == "right" else tile.x - gap - width
    y = tile.y + (tile.height - height) // 2
    if bounds is not None:
        screen_width, screen_height = bounds
        y = max(4, min(y, screen_height - height - 4))
        x = max(4, min(x, screen_width - width - 4))
    return [(x, y + 4 + i * row_h, width, row_h) for i in range(len(menu.actions))]


def draw_menu(screen, menu: Menu, tiles, font_at) -> None:
    rows = menu_rects(menu, tiles, font_at, screen.get_size())
    x, y = rows[0][0], rows[0][1] - 4
    height = rows[-1][1] + rows[-1][3] - y + 8
    pygame.draw.rect(screen, TILE, (x, y, rows[0][2], height), border_radius=8)
    pygame.draw.rect(screen, TEXT_DIM, (x, y, rows[0][2], height), width=1, border_radius=8)
    for index, (label, _) in enumerate(menu.actions):
        rx, ry, rw, rh = rows[index]
        if index == menu.selected:
            pygame.draw.rect(screen, TILE_SELECTED, (rx + 4, ry, rw - 8, rh - 4), border_radius=6)
        text = font_at(22).render(label, True, TEXT if index == menu.selected else TEXT_DIM)
        screen.blit(text, (rx + 16, ry + (rh - text.get_height()) // 2 - 2))


