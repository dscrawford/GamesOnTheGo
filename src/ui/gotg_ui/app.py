"""The grid itself: the only part that needs a screen.

Kept thin on purpose. Which games exist and where the tiles go are both
answerable without a display, and both live next door; what is left here is
drawing them and reading a controller.

Milestone one draws placeholders and launches nothing. Art and `gotg play`
come after this has been sat in front of on a Deck.
"""

from __future__ import annotations

import math
import os
import sys
import time

import pygame

from . import around, config, devices, filters, keys, meter, pads, prepare, seat, trace
from .art import ArtStore
from .assign import KeyHold, Session, Watch, attend
from .browser import SHELF, Browser
from .catalog import Game, Library
from .controllers import assets_dir, control_places, draw_assign, draw_strip
from .controllers import draw as draw_controllers
from .fetch import Loader
from .filters import Filters
from .grid import Grid
from .hush import Hush
from .installed import installed_games
from .installs import Installs
from .layout import grid, shelf, shelf_at, tile_at
from .menu import Menu
from .padmap import DaemonWatch, Padmap, ensure_daemon
from .padstrip import HEIGHT as STRIP_HEIGHT
from .padstrip import PANEL, status_text, strip_status
from .prepare import Preparer, is_ready
from .recent import Recent
from .storage import Storage, human
from .variants import variants_for
from .versions import forget as forget_versions
from .versions import names as version_names
from .versions import versions_for

BACKGROUND = config.colour("theme.colours.background", (18, 18, 20))
TILE = config.colour("theme.colours.tile", (38, 38, 44))
TILE_SELECTED = config.colour("theme.colours.tile_selected", (58, 104, 148))
TEXT = config.colour("theme.colours.text", (232, 232, 236))
TEXT_DIM = config.colour("theme.colours.text_dim", (150, 150, 158))

# The Deck's own panel, so a window on a desktop is the shape it will be there.
WINDOW = tuple(config.get("theme.window", [1280, 800]))

# The keyboard's arrows as the same steps a d-pad gives, so the diagram is
# walked the same way from either.
ARROWS = {
    pygame.K_LEFT: (-1, 0),
    pygame.K_RIGHT: (1, 0),
    pygame.K_UP: (0, -1),
    pygame.K_DOWN: (0, 1),
}


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
_scaled = Recent(96)


def fitted(picture, key, width: int, height: int):
    """`picture` scaled to fit a `width` x `height` box, kept for next frame.

    Fitted, never stretched: a tile is 2:3 and a cartridge box is landscape, so
    most of this library's art arrives the wrong shape for its slot and scaling
    to fill would squash every one.
    """
    pw, ph = picture.get_size()
    scale = min(width / pw, height / ph)
    size = (max(1, int(pw * scale)), max(1, int(ph * scale)))
    remembered = _scaled.get((key, size))
    if remembered is not None:
        return remembered
    return _scaled.put((key, size), pygame.transform.smoothscale(picture, size))


def filling(picture, key, width: int, height: int):
    """`picture` scaled so it *covers* a box, for cropping to a square icon.

    The other half of `fitted`: that one leaves bars, which is right for a
    cover in a tile and wrong for a 40-pixel icon in a list row.
    """
    pw, ph = picture.get_size()
    scale = max(width / pw, height / ph)
    size = (max(1, int(pw * scale)), max(1, int(ph * scale)))
    remembered = _scaled.get((key, "fill", size))
    if remembered is not None:
        return remembered
    return _scaled.put((key, "fill", size), pygame.transform.smoothscale(picture, size))


def view_rects(browser, size) -> list:
    """The rectangles the view on screen draws its games in.

    Asked of the view rather than assumed to be the grid's. The menu anchors
    to one of these, and the shelf's twelve rows are not the grid's ten tiles:
    opening a menu on the eleventh row indexed past the end of a list that was
    not the one on screen, and the picker died with an IndexError.
    """
    if browser.view == SHELF:
        _hero, rows = shelf(*size, STRIP_HEIGHT)
        return rows
    return grid(*size, STRIP_HEIGHT)


def hovering(browser, pos, size) -> int | None:
    """Which cover the pointer is on, in whichever view is drawn.

    Asked of the view rather than assumed, because the two have different
    rectangles and a pointer answered by the wrong one selects a game three
    along from the one it is over.
    """
    finder = shelf_at if browser.view == SHELF else tile_at
    return finder(*pos, *size, STRIP_HEIGHT)


def draw_cover(screen, tile, game, picture, font_at, selected: bool) -> None:
    """One cover, fitted into its rectangle and never stretched.

    The grid and the shelf draw the same thing at two sizes, and drawing it
    twice is two places for the art to start being squashed in one of them.
    """
    if picture is not None:
        art = fitted(picture, game.key, tile.width, tile.height)
        size = art.get_size()
        pygame.draw.rect(screen, TILE, tile.rect, border_radius=8)
        screen.blit(
            art,
            (tile.x + (tile.width - size[0]) // 2, tile.y + (tile.height - size[1]) // 2),
        )
        return
    pygame.draw.rect(screen, TILE_SELECTED if selected else TILE, tile.rect, border_radius=8)
    inner = max(8, tile.width - 16)
    text = game.title[:120]
    base = max(10, min(20, tile.width // 6))
    title = font_at(base).render(text, True, TEXT)
    if title.get_width() > inner:
        title = _fit(font_at, text, inner, base).render(text, True, TEXT)
    # Clipped to its own cover. _fit gives up at its smallest size, and on a
    # shelf cover that is still too wide for a long title -- which then runs
    # across the game beside it.
    before = screen.get_clip()
    screen.set_clip(pygame.Rect(*tile.rect))
    screen.blit(title, (tile.x + 8, tile.y + tile.height // 2 - title.get_height() // 2))
    screen.set_clip(before)


def draw_row(
    screen, row, game, picture, font_at, selected: bool, installed: bool, ring: tuple[float | None, bool] | None = None
) -> None:
    """One line of the list: an icon, a title, and the platform under it.

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
        under = game.platform + ("   ·   install failed" if failed else "   ·   installing")
        r = max(6, side // 3)
        draw_ring(screen, (row.x + row.width - r - 12, row.y + row.height // 2), r, fraction, failed)
    else:
        under = game.platform + ("   ·   installed" if installed else "")
    small = font_at(max(11, size - 8)).render(under, True, TEXT_DIM)
    screen.blit(small, (left, row.y + 6 + title.get_height()))


def draw_shelf(
    screen,
    state: Grid,
    font_at,
    art=None,
    status: str = "",
    installed: set[tuple[str, str]] | None = None,
    rings: dict[tuple[str, str], tuple[float | None, bool]] | None = None,
) -> None:
    """The list on the left, and the art of the one under the cursor on the
    right — the other way to look at the same library."""
    width, height = screen.get_size()
    screen.fill(BACKGROUND)
    page = state.page
    hero, rows = shelf(width, height, STRIP_HEIGHT)

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
        draw_row(
            screen,
            row,
            game,
            art.get(game.key) if art else None,
            font_at,
            index == state.selected,
            bool(installed and game.key in installed),
            rings.get(game.key) if rings else None,
        )

    if status:
        shown = font_at(22).render(status, True, TEXT_DIM)
        screen.blit(shown, (rows[0].x, height - shown.get_height() - 10))


def draw(
    screen,
    state: Grid,
    font_at,
    art=None,
    status: str = "",
    typing: str | None = None,
    menu=None,
    installed: set[tuple[str, str]] | None = None,
    rings: dict[tuple[str, str], tuple[float | None, bool]] | None = None,
) -> None:
    width, height = screen.get_size()
    screen.fill(BACKGROUND)
    page = state.page
    tiles = grid(width, height, STRIP_HEIGHT)
    # One translucent wash per tile size, cached: while the menu is open every
    # other tile drops to ~90% so the chosen one reads as chosen.
    dim = None
    if menu is not None:
        # Cached, which the comment here used to claim and the code did not do:
        # it built one of these every frame the menu was open.
        dim = _scaled.get(("dim", tiles[0].width, tiles[0].height))
        if dim is None:
            dim = pygame.Surface((tiles[0].width, tiles[0].height), pygame.SRCALPHA)
            dim.fill((*BACKGROUND, 26))
            _scaled.put(("dim", tiles[0].width, tiles[0].height), dim)

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
    if state.game is not None:
        text = state.game.title[:200]
        label = _fit(font_at, text, width - 48, 26).render(text, True, TEXT)
        screen.blit(label, ((width - label.get_width()) // 2, (tiles[0].y - label.get_height()) // 2))

    if not status:
        status = (
            f"page {state.page_index + 1} of {state.library.pages}  ·  {len(state.library)} games"
            if state.library.pages
            else "no games in the catalog"
        )
    # While typing, the search box replaces the status: it is the thing being
    # edited, and two lines competing for the same corner reads as neither.
    if typing is not None:
        status = f"search: {typing}_"
    label = font_at(18).render(status, True, TEXT if typing is not None else TEXT_DIM)
    screen.blit(label, (label.get_height(), height - label.get_height() * 2))


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
        y = max(STRIP_HEIGHT + 4, min(y, screen_height - height - 4))
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


def filter_rects(panel, font_at, size) -> list[tuple[int, int, int, int]]:
    """One rect per filter row, centred. Computed here and only here, so the
    drawing and the pointer cannot disagree about where a row is."""
    width, height = size
    row_h = font_at(26).get_height() + 16
    panel_w = min(int(width * 0.6), 560)
    total = row_h * len(panel.rows)
    left = (width - panel_w) // 2
    top = (height - total) // 2
    return [(left, top + i * row_h, panel_w, row_h) for i in range(len(panel.rows))]


def draw_filters(screen, font_at, browser, panel, typing: str | None = None) -> None:
    """The filter panel: a row per thing to narrow by, and its value.

    A list rather than more buttons. Everything on it was already possible and
    half of it only from a keyboard -- regions had no binding a pad could
    reach at all.
    """
    width, height = screen.get_size()
    overlay = _scaled.get(("panel", width, height))
    if overlay is None:
        overlay = pygame.Surface((width, height), pygame.SRCALPHA)
        overlay.fill((*BACKGROUND, 232))
        _scaled.put(("panel", width, height), overlay)
    screen.blit(overlay, (0, 0))

    title = font_at(40).render("Menu", True, TEXT)
    rows = filter_rects(panel, font_at, (width, height))
    screen.blit(title, ((width - title.get_width()) // 2, rows[0][1] - title.get_height() - 24))

    for (label, value, selected), (rx, ry, rw, rh) in zip(filters.rows_for(browser, panel, typing), rows, strict=True):
        if selected:
            pygame.draw.rect(screen, TILE_SELECTED, (rx, ry, rw, rh - 6), border_radius=8)
        name = font_at(26).render(label, True, TEXT if selected else TEXT_DIM)
        screen.blit(name, (rx + 20, ry + (rh - 6 - name.get_height()) // 2))
        if value:
            shown = font_at(26).render(value, True, TEXT if selected else TEXT_DIM)
            screen.blit(shown, (rx + rw - shown.get_width() - 20, ry + (rh - 6 - shown.get_height()) // 2))

    bottom = rows[-1][1] + rows[-1][3]
    if panel.choice is not None:
        # The list can reach below the rows, and the keys have to clear it --
        # a footer drawn under an open list reads as part of the options.
        bottom = max(bottom, draw_choice(screen, font_at, panel, rows))

    keys = (
        "up/down choose   A pick   B back"
        if panel.choice is not None
        else "A open the list   left/right nudge   B or Esc back"
    )
    footer = font_at(22).render(keys, True, TEXT_DIM)
    screen.blit(footer, ((width - footer.get_width()) // 2, bottom + 20))


# How many options are on screen at once. A dozen platforms would run off the
# bottom of a Deck, so the list scrolls around the cursor instead.
CHOICE_WINDOW = 7


def draw_choice(screen, font_at, panel, rows) -> int:
    """The open dropdown, over the row it belongs to. Returns its bottom."""
    choice = panel.choice
    rx, ry, rw, rh = rows[panel.index]
    row_h = font_at(24).get_height() + 10

    # Scrolled so the cursor is in the middle, except at the ends, where
    # sliding past the last entry would show empty space instead of options.
    total = len(choice.options)
    shown = min(CHOICE_WINDOW, total)
    first = max(0, min(choice.index - shown // 2, total - shown))

    counter = font_at(18)
    # Room for "7 of 12" under the last option rather than across it.
    footer_h = counter.get_height() + 6 if total > shown else 0
    list_w = max(220, rw // 2)
    list_x = rx + rw - list_w
    list_y = ry + rh - 6
    list_h = row_h * shown + 8 + footer_h
    # Upwards when there is no room below, which there is not for the last row.
    if list_y + list_h > screen.get_height():
        list_y = ry - list_h + 6

    pygame.draw.rect(screen, PANEL, (list_x, list_y, list_w, list_h), border_radius=8)
    pygame.draw.rect(screen, TEXT_DIM, (list_x, list_y, list_w, list_h), width=1, border_radius=8)

    for offset in range(shown):
        index = first + offset
        y = list_y + 4 + offset * row_h
        if index == choice.index:
            pygame.draw.rect(screen, TILE_SELECTED, (list_x + 4, y, list_w - 8, row_h - 2), border_radius=6)
        colour = TEXT if index == choice.index else TEXT_DIM
        text = font_at(24).render(choice.options[index], True, colour)
        screen.blit(text, (list_x + 16, y + (row_h - 2 - text.get_height()) // 2))

    # Said, rather than left to be guessed at from a list that stops.
    if footer_h:
        more = counter.render(f"{choice.index + 1} of {total}", True, TEXT_DIM)
        screen.blit(more, (list_x + list_w - more.get_width() - 12, list_y + list_h - more.get_height() - 4))
    return list_y + list_h


def draw_storage(screen, font_at, storage: Storage, typing: str | None) -> None:
    """The storage screen: every games directory, the device's room, the way out."""
    width, height = screen.get_size()
    screen.fill(BACKGROUND)
    margin = height // 16
    screen.blit(font_at(30).render("Storage — where games are kept", True, TEXT), (margin, margin))

    y = margin + font_at(30).get_height() + margin // 2
    row_h = font_at(22).get_height() + 16
    if not storage.rows:
        empty = font_at(22).render("no games directory known — could not ask the client", True, TEXT_DIM)
        screen.blit(empty, (margin, y))
    for index, row in enumerate(storage.rows):
        selected = index == storage.selected
        if selected:
            band = (margin - 8, y - 4, width - 2 * margin + 16, row_h)
            pygame.draw.rect(screen, TILE_SELECTED, band, border_radius=6)
        mark = "downloads land here" if row.get("default") else ""
        if not row.get("exists", True):
            room = "missing"
        else:
            room = f"{human(int(row.get('free_bytes', 0)))} free of {human(int(row.get('total_bytes', 0)))}"
        path = font_at(22).render(str(row["path"])[:90], True, TEXT if selected else TEXT_DIM)
        screen.blit(path, (margin, y + 4))
        note = font_at(18).render(f"{room}    {mark}", True, TEXT if selected else TEXT_DIM)
        screen.blit(note, (width - margin - note.get_width(), y + 8))
        y += row_h

    if typing is not None:
        prompt = font_at(22).render(f"add a directory: {typing}_", True, TEXT)
        screen.blit(prompt, (margin, y + row_h))
    if storage.message:
        note = font_at(18).render(storage.message[:160], True, TEXT)
        screen.blit(note, (margin, height - margin * 2 - note.get_height()))
    hint = (
        "A / Enter — downloads go here   ·   Y / + — add a directory   ·   X / Delete — forget   ·   B / Escape — back"
    )
    label = font_at(18).render(hint, True, TEXT_DIM)
    screen.blit(label, (margin, height - margin - label.get_height()))


def draw_prepare(
    screen,
    font_at,
    game: Game,
    lines: list[str],
    failed: bool,
    progress: prepare.Progress | None = None,
    elapsed: float = 0.0,
) -> None:
    """The loader screen: heading, a bar while a download runs, `gotg install`'s
    output verbatim, the way out.

    Verbatim so a build failure reads on the TV, not only in a log file. The
    bar and the running clock are there because a 30 GB download and a
    first emulator build are minutes of a screen that otherwise looks frozen.
    """
    width, height = screen.get_size()
    screen.fill(BACKGROUND)
    margin = height // 16

    if failed:
        heading = f"could not prepare {game.title[:80]}"
        hint = "B / Escape — back to the grid   ·   full log: gotg install " + f"{game.platform}/{game.id}"
        colour = (224, 96, 96)
    else:
        heading = f"preparing {game.title[:80]} — a first launch builds its emulator"
        hint = "B / Escape — cancel and go back"
        colour = TEXT

    screen.blit(font_at(30).render(heading, True, colour), (margin, margin))
    if not failed:
        # The clock, at the right of the heading: the one thing that moves
        # through a build that prints nothing for a minute.
        clock = font_at(20).render(prepare.elapsed_text(elapsed), True, TEXT_DIM)
        screen.blit(clock, (width - margin - clock.get_width(), margin + 6))

    y = margin + font_at(30).get_height() + margin // 2
    if progress is not None and not failed:
        bar_h = max(10, height // 45)
        bar = pygame.Rect(margin, y, width - 2 * margin, bar_h)
        pygame.draw.rect(screen, TILE, bar, border_radius=bar_h // 2)
        fraction = progress.fraction
        if fraction is None:
            # Size unknown: a short segment sweeping back and forth, so the
            # bar still says "moving" rather than "stuck at zero".
            sweep = (pygame.time.get_ticks() // 8) % (2 * (bar.width - bar.width // 5))
            if sweep > bar.width - bar.width // 5:
                sweep = 2 * (bar.width - bar.width // 5) - sweep
            fill = pygame.Rect(bar.x + sweep, bar.y, bar.width // 5, bar_h)
        else:
            fill = pygame.Rect(bar.x, bar.y, max(bar_h, int(bar.width * fraction)), bar_h)
        pygame.draw.rect(screen, TILE_SELECTED, fill, border_radius=bar_h // 2)
        y += bar_h + 8
        figures = font_at(20).render(progress.describe(), True, TEXT)
        screen.blit(figures, (margin, y))
        what = font_at(16).render(progress.what[:120], True, TEXT_DIM)
        screen.blit(what, (width - margin - what.get_width(), y + 2))
        y += figures.get_height() + margin // 2
    line_font = font_at(16)
    for line in lines:
        if y > height - margin * 2:
            break
        screen.blit(line_font.render(line[:180], True, TEXT_DIM), (margin, y))
        y += line_font.get_height() + 2

    label = font_at(18).render(hint, True, TEXT_DIM)
    screen.blit(label, (margin, height - margin - label.get_height()))


def run(library: Library, installed_only: bool = False) -> tuple[Game, str] | None:
    """Draw until somebody chooses an action or quits, and say which.

    Returns (game, verb) — play or configure — both of which the caller execs.

    The game is *returned* rather than launched here: exec has to happen after
    pygame has given the display back, or the emulator inherits a window and a
    grabbed GPU from a process that is about to stop existing.
    """
    pygame.init()
    pygame.display.set_caption("GamesOnTheGo")
    # SCALED with FULLSCREEN: the layout is worked out at one size and the
    # display scales it, so a 1280x800 panel and a television at 1080p get
    # the same grid rather than one with different margins.
    flags = (pygame.FULLSCREEN | pygame.SCALED) if config.fullscreen() else 0
    # vsync, when the config asks for it. Off by default because it is a trade
    # rather than a win: SDL blocks in `flip` until the panel is ready, which
    # is smoother and cheaper than drawing frames nobody sees, and on a driver
    # that cannot do it `set_mode` refuses outright -- hence the fallback.
    screen = None
    if config.get("theme.vsync", True):
        try:
            screen = pygame.display.set_mode(WINDOW, flags, vsync=1)
        except pygame.error as error:
            trace.say("no-vsync", why=str(error))
    if screen is None:
        screen = pygame.display.set_mode(WINDOW, flags)
    clock = pygame.time.Clock()

    # What SDL actually got, said once. A 1280x800 surface being resampled to
    # a 4K panel every frame is tens of milliseconds that no amount of
    # drawing less will recover, and it is invisible from in here without
    # asking: driver, the size drawn, the size shown, and the refresh rate.
    shown_on = {
        "driver": pygame.display.get_driver(),
        "drawing": list(screen.get_size()),
        "desktop": [list(size) for size in pygame.display.get_desktop_sizes()],
        "scaled": bool(flags & pygame.SCALED),
        "refresh": pygame.display.get_current_refresh_rate(),
        "vsync": bool(config.get("theme.vsync", True)),
    }
    trace.say("display", **shown_on)
    if meter.wanted():
        print(f"gotg-ui: display {shown_on}", file=sys.stderr)
    # Held open for as long as the picker runs: a pad that goes out of scope is
    # closed by SDL, and a closed one stops producing events. Opened through
    # the controller API, which is what makes "the right bumper" mean the same
    # button on every pad rather than index 5 on an Xbox one.
    sticks = pads.init()
    # And every keyboard and mouse that is really a controller -- a Steam
    # Controller's lizard mode, a Bluetooth Xbox pad's extra collections --
    # held so the compositor never sees them. The joystick rule cannot reach
    # those: to SDL a lizard-mode d-pad *is* the arrow keys. See hush.py.
    # Where a frame's time goes. Silent unless GOTG_UI_FPS=1 or a trace is
    # running: "it feels slow" is two different problems -- this program
    # drawing, and SDL presenting what it drew -- and they are fixed in
    # different places. See meter.py.
    fps = meter.Meter()

    hush = Hush()
    hush.refresh()

    fonts: dict[int, pygame.font.Font] = {}

    def font_at(size: int) -> pygame.font.Font:
        if size not in fonts:
            fonts[size] = pygame.font.Font(None, size)
        return fonts[size]

    # Asked once, up front, and again only after an uninstall: the answer is
    # a walk of the whole catalog against the disk, not a per-frame question.
    browser = Browser(library, installed=installed_games())
    if installed_only:
        browser.toggle_installed()
    store = ArtStore()
    loader = Loader(store)
    # Decoded surfaces, keyed by (platform, id). Decoding is not free and the
    # same ten tiles are redrawn sixty times a second.
    art: dict[tuple[str, str], object] = {}

    def surface_for(game):
        """A decoded picture for one game, asking the loader if need be."""
        if game.key in art:
            return art[game.key]
        path = loader.want(game)
        if path is None:
            return None
        try:
            art[game.key] = pygame.image.load(str(path)).convert_alpha()
        except pygame.error:
            # A truncated or unreadable file: drop it so a refresh can
            # replace it, and draw the title this time round.
            store.forget(game)
            art[game.key] = None
        return art[game.key]

    chosen: tuple[Game, str, str | None, str | None] | None = None
    typing: str | None = None
    typing_from = ""  # the search before typing began, for Escape
    menu: Menu | None = None
    # Which platform's bindings are being looked at, or None for the grid.
    # A screen rather than an overlay: it is a page of reference, not an
    # action, and nothing underneath it should keep moving.
    controllers: str | None = None
    # Which control the cursor is on while the diagram is up.
    focus: str = ""
    # Filled in when the diagram is drawn. Empty until then, which the cursor
    # treats as "nowhere to go" rather than as an error.
    control_anchors: dict[str, tuple[float, float]] = {}
    # The filter panel, while it is open. None is the grid.
    panel: Filters | None = None
    # The controller layer. Absent is a state rather than a failure: a machine
    # with no daemon running is what every machine looks like before anybody
    # has set a controller up, and the strip says so instead of disappearing.
    padmap = Padmap()
    # Started rather than waited for: the picker is usually the first thing
    # open on this machine, so if it does not start the daemon nothing will.
    # A failure is a sentence in the strip, not a reason to refuse to draw.
    # No session, ever, from the grid. padmap opens one by itself the first
    # time it meets a pad it has no mapping for -- which grabs every
    # controller, takes the screen, and is exactly the pairing detour this
    # picker is supposed to have stopped needing. A seat comes from a hold,
    # and what a button means is asked at launch by gotg-seat, where there is
    # a game to ask about. Set before the daemon is started, since it is the
    # daemon that reads it, and left alone when somebody set it themselves.
    os.environ.setdefault("PADMAP_NO_AUTOSETUP", "1")
    # And no seat but by a hold. padmap seats a pad it has a stored mapping
    # for the moment it sees it, which --fresh does not stop: the Xbox pad
    # was player one two seconds after the grid opened, nobody had held
    # anything, and the strip said "no controllers" because no state ever
    # followed. Seen in a trace, not reasoned about.
    os.environ.setdefault("PADMAP_NO_AUTOATTACH", "1")
    # Unseated, and for as long as this process lives -- which, after a pick
    # execvp's into a game, is the game. Nobody is seated when the picker
    # opens; a hold seats them; the daemon goes when the session does.
    padmap_trouble = ensure_daemon(fresh=True, follow=os.getpid())
    trace.say(
        "start",
        pid=os.getpid(),
        padmap_on_path=bool(__import__("shutil").which("padmap")),
        daemon_trouble=padmap_trouble,
        any_pad=os.environ.get("GOTG_ANY_PAD"),
        pads_open=len(sticks),
        held=hush.refresh(),
    )
    padmap.connect()
    # And asked after again whenever the connection is gone -- see DaemonWatch
    # for why reconnecting alone was not enough.
    padmap_watch = DaemonWatch()
    # The assignment screen. `open` is what decides whether it is on screen,
    # and padmap closes it by accepting rather than this program deciding.
    seating = Session()
    # And the standing invitation underneath it: padmap listening for a hold
    # for as long as the grid is up, so picking a controller up and holding a
    # button is all it takes to become player one. Nothing on screen until
    # somebody does -- see assign.Watch.
    watch = Watch()
    # The space bar, held, seats the keyboard. Tapped it opens the menu as it
    # always did -- decided on release, so one key can mean either.
    space = KeyHold()
    controller_art: dict = {}
    # The storage screen, and the path being typed to add to it.
    storage: Storage | None = None
    storage_typing: str | None = None
    # The loader phase: a verb that needs work spawns the client and the grid
    # gives way to its output until it finishes, fails, or is cancelled.
    preparer: Preparer | None = None
    installs = Installs()
    prepare_failed = False
    # What happens when the loader succeeds: exec the verb, or come back here.
    after_prepare: str | None = None
    running = True

    def pick(game: Game | None, verb: str = "play", variant: str | None = None, version: str | None = None) -> None:
        nonlocal chosen, running, preparer, after_prepare, controllers, storage
        if game is None:
            return
        if verb == "controllers":
            # A screen in this program, not a verb for the client.
            controllers = game.platform
            return
        if verb == "storage":
            storage = Storage()
            storage.refresh()
            return
        if verb == "steam-add":
            # Not an exec: the shortcut is written, the grid comes back. A mod
            # gets its own Steam entry, which is what the client does with a
            # variant anyway — its own launcher, its own artwork.
            preparer = Preparer(game, ["steam", "add"], variant, version)
            after_prepare = None
            return
        if verb == "install":
            # Behind the grid, as a ring on the tile: the badge fills in when
            # it lands, and the grid stays usable meanwhile. Per variant,
            # like play -- a mod is its own environment, and installing it
            # here is what makes its Play instant later.
            installs.start(game, variant, version)
            return
        if verb == "cancel-install":
            installs.cancel(game.key)
            return
        if verb == "uninstall":
            # Through the loader like steam-add, so what was removed is read
            # rather than guessed; then the badges are asked for again.
            # No variant: `gotg uninstall` takes the game's bytes and every
            # launcher with them, mods included. Passing one would read as
            # "remove just this mod", which is not what would happen.
            preparer = Preparer(game, ["uninstall"])
            after_prepare = "uninstall"
            return
        after_prepare = verb
        # A game already on its way: the loader adopts that install rather
        # than starting a second one to queue behind the client's lock.
        if verb == "play" and installs.running(game.key):
            preparer = installs.take(game.key)
            return
        # Readiness is per variant: a mod is its own environment, and the one
        # built for the plain game says nothing about whether this one is.
        if is_ready(game, variant):
            chosen = (game, verb, variant, version)
            running = False
        else:
            preparer = Preparer(game, None, variant, version)

    try:
        while running:
            state = browser.grid
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    if preparer is not None:
                        preparer.cancel()
                    running = False
                    continue

                # Before any screen, because every screen needs it. Assigning
                # controllers *replaces* them: padmap grabs the physical pad,
                # which then reports nothing, and publishes `padmap Player N`
                # in its place. A picker holding only the handles it opened at
                # startup goes dead at exactly the moment somebody finishes
                # setting their controller up -- which is the worst possible
                # moment, because it looks like padmap broke the machine.
                if event.type == pygame.JOYDEVICEADDED:
                    sticks.add(event.device_index)
                    hush.refresh()
                    # Event node numbers are reused, so what /proc said about
                    # eventN a moment ago can be another device now.
                    devices.forget()
                    continue
                if event.type == pygame.JOYDEVICEREMOVED:
                    sticks.remove(event.instance_id)
                    hush.refresh()
                    devices.forget()
                    continue

                # The keyboard takes a seat like everything else.
                #
                # It used to be the fallback underneath the pad rule -- no
                # daemon, no seat, no problem -- and that was the hole the
                # rule exists to close: a controller is a keyboard in
                # hardware, so "anything that types" included pads nobody had
                # assigned. Until padmap has seated it, the only key heard is
                # the space bar that asks for the seat. See keys.py.
                if event.type in (pygame.KEYDOWN, pygame.KEYUP, pygame.TEXTINPUT):
                    # The space bar is always heard: a keyboard that cannot
                    # ask for a seat cannot be given one.
                    asking = getattr(event, "key", None) == pygame.K_SPACE
                    if not asking and not keys.drives(padmap.players, padmap.connected):
                        trace.say("key-refused", key=getattr(event, "key", None))
                        continue

                # On the loader, the only input is the way out. Everything else
                # would be the grid moving invisibly behind the build.
                if preparer is not None:
                    back = (
                        event.type == pygame.KEYDOWN and event.key in (pygame.K_ESCAPE, pygame.K_b)
                    ) or pads.button(event) == pads.B
                    if back:
                        if not prepare_failed:
                            preparer.cancel()
                        preparer = None
                        prepare_failed = False
                    continue

                # The storage screen owns its input: a list to walk, three
                # verbs, and a path typed to add — every key is text then.
                if storage is not None:
                    if storage_typing is not None:
                        if event.type != pygame.KEYDOWN:
                            continue
                        if event.key == pygame.K_ESCAPE:
                            storage_typing = None
                        elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                            storage.add(storage_typing)
                            storage_typing = None
                        elif event.key == pygame.K_BACKSPACE:
                            storage_typing = storage_typing[:-1]
                        elif event.unicode and event.unicode.isprintable():
                            storage_typing += event.unicode
                        continue
                    if event.type == pygame.KEYDOWN:
                        if event.key in (pygame.K_ESCAPE, pygame.K_b):
                            storage = None
                        elif event.key == pygame.K_UP:
                            storage.move(-1)
                        elif event.key == pygame.K_DOWN:
                            storage.move(1)
                        elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                            storage.make_default()
                        elif event.key in (pygame.K_DELETE, pygame.K_x):
                            storage.remove()
                        elif event.key in (pygame.K_PLUS, pygame.K_KP_PLUS, pygame.K_EQUALS, pygame.K_y):
                            storage_typing = ""
                    else:
                        step = pads.direction(event)
                        pressed = pads.button(event)
                        if step and step[1]:
                            storage.move(step[1])
                        elif pressed == pads.A:
                            storage.make_default()
                        elif pressed == pads.B:
                            storage = None
                        elif pressed == pads.X:
                            storage.remove()
                        elif pressed == pads.Y:
                            # A Deck raises the Steam keyboard over this.
                            storage_typing = ""
                    continue

                # Same for the controller diagram, which is also where a
                # controller is assigned -- so it takes A, B and Y from both
                # the keyboard and a pad, and nothing else. `seating.open` as
                # well as the screen, because padmap opens sessions of its own
                # and the way out of one has to be reachable from wherever the
                # picker happened to be.
                if controllers is not None or seating.open:
                    if event.type == pygame.KEYDOWN:
                        if event.key in (pygame.K_ESCAPE, pygame.K_b, pygame.K_c):
                            # One press, and it keeps what was claimed. Two
                            # presses was the bug: the first threw the claim
                            # away, which took the clone with it, which left
                            # nothing able to press the second.
                            if seating.open:
                                padmap.send(seating.leave())
                            controllers = None
                        elif event.key in ARROWS and not seating.open:
                            focus = around.nearest(control_anchors, focus, ARROWS[event.key])
                        elif event.key in (pygame.K_RETURN, pygame.K_a):
                            # One key, two meanings, and the state says which:
                            # nothing started yet means start, and a session in
                            # flight means keep what has been claimed.
                            padmap.send(seating.accept() if seating.open else seating.begin())
                        elif event.key == pygame.K_r and seating.open:
                            padmap.send(seating.reset())
                    elif pads.direction(event) is not None and not seating.open:
                        # Around the drawing itself. Geometric, so "right" from
                        # the d-pad reaches the face buttons rather than
                        # whichever control the config happens to list next.
                        focus = around.nearest(control_anchors, focus, pads.direction(event))
                    else:
                        # The same three things the keyboard does. Leaving them
                        # off was the whole of "I hold A and nothing happens":
                        # no session was ever begun, so there was nothing to
                        # hold a button *at*.
                        pressed = pads.button(event)
                        if pressed == pads.A:
                            padmap.send(seating.accept() if seating.open else seating.begin())
                        elif pressed == pads.B:
                            if seating.open:
                                padmap.send(seating.leave())
                            controllers = None
                        elif pressed == pads.Y and seating.open:
                            padmap.send(seating.reset())
                    continue

                # While the menu is open it owns the input: the grid must
                # not move invisibly underneath the panel.
                if menu is not None:
                    if event.type == pygame.KEYDOWN:
                        if event.key in (pygame.K_ESCAPE, pygame.K_b):
                            # Out of the variants first, then out of the menu.
                            if menu.expanded:
                                menu.close_list()
                            else:
                                menu = None
                        elif event.key == pygame.K_UP:
                            menu.move(-1)
                        elif event.key == pygame.K_DOWN:
                            menu.move(1)
                        elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                            verb = menu.confirm()
                            if verb is not None:
                                picked, menu = menu, None
                                pick(picked.game, verb, picked.variant, picked.version)
                    elif pads.direction(event) is not None:
                        _, dy = pads.direction(event)
                        if dy:
                            menu.move(dy)
                    elif pads.button(event) is not None:
                        pressed = pads.button(event)
                        if pressed == pads.A:
                            verb = menu.confirm()
                            if verb is not None:
                                picked, menu = menu, None
                                pick(picked.game, verb, picked.variant, picked.version)
                        elif pressed == pads.B:
                            # B backs out of the variants first, and only then
                            # out of the menu: one button, one step at a time.
                            if menu.expanded:
                                menu.close_list()
                            else:
                                menu = None
                    elif event.type == pygame.MOUSEMOTION:
                        rows = menu_rects(
                            menu, view_rects(browser, screen.get_size()), font_at, screen.get_size()
                        )
                        for i, (rx, ry, rw, rh) in enumerate(rows):
                            if rx <= event.pos[0] < rx + rw and ry <= event.pos[1] < ry + rh:
                                menu.select(i)
                    elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                        rows = menu_rects(
                            menu, view_rects(browser, screen.get_size()), font_at, screen.get_size()
                        )
                        hit = None
                        for i, (rx, ry, rw, rh) in enumerate(rows):
                            if rx <= event.pos[0] < rx + rw and ry <= event.pos[1] < ry + rh:
                                hit = i
                        if hit is None:
                            menu = None  # a click that misses the panel closes it
                        elif menu.select(hit):
                            verb = menu.confirm()
                            if verb is not None:
                                picked, menu = menu, None
                                pick(picked.game, verb, picked.variant, picked.version)
                    continue

                # The filter panel, while it is up. Before the typing branch
                # so that opening the keyboard from it still works, and before
                # the grid so the cursor does not move behind it.
                if panel is not None and typing is None:
                    # An open list owns up, down, A and B; the panel underneath
                    # owns them when there is none. One step back per press of
                    # B: the list first, then the panel.
                    if event.type == pygame.KEYDOWN:
                        if event.key in (pygame.K_ESCAPE, pygame.K_b):
                            if panel.open:
                                panel.close()
                            else:
                                panel = None
                        elif event.key == pygame.K_UP:
                            panel.choice.move(-1) if panel.open else panel.move(-1)
                        elif event.key == pygame.K_DOWN:
                            panel.choice.move(1) if panel.open else panel.move(1)
                        elif event.key == pygame.K_LEFT and not panel.open:
                            panel.adjust(browser, -1)
                        elif event.key == pygame.K_RIGHT and not panel.open:
                            panel.adjust(browser, 1)
                        elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                            if panel.open:
                                # A platform back means the controller row: it
                                # is a door out of the panel, not a filter.
                                chosen = panel.choose(browser)
                                if chosen:
                                    controllers, panel = chosen, None
                            elif panel.press(browser) == filters.TYPING:
                                typing = typing_from = browser.search
                    else:
                        step = pads.direction(event)
                        pressed = pads.button(event)
                        if step:
                            dx, dy = step
                            if dy:
                                panel.choice.move(dy) if panel.open else panel.move(dy)
                            if dx and not panel.open:
                                panel.adjust(browser, dx)
                        elif pressed == pads.A:
                            if panel.open:
                                chosen = panel.choose(browser)
                                if chosen:
                                    controllers, panel = chosen, None
                            elif panel.press(browser) == filters.TYPING:
                                typing = typing_from = browser.search
                        elif pressed == pads.B:
                            if panel.open:
                                panel.close()
                            else:
                                panel = None
                        elif pressed == pads.START:
                            # Start closes it the way Start opened it.
                            panel = None
                    continue

                # While typing, every key is text. Nothing below runs, or the
                # letters of a search would also be moving the cursor.
                if typing is not None:
                    if event.type != pygame.KEYDOWN:
                        continue
                    if event.key == pygame.K_ESCAPE:
                        # Cancelled: the grid goes back to the search it had.
                        browser.set_search(typing_from)
                        typing = None
                    elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                        browser.set_search(typing)
                        typing = None
                    elif event.key == pygame.K_BACKSPACE:
                        typing = typing[:-1]
                        browser.set_search(typing)
                    elif event.unicode and event.unicode.isprintable():
                        typing += event.unicode
                        # Applied as it is typed: the grid narrowing under the
                        # letters is the feedback that the letters went in.
                        browser.set_search(typing)
                    continue

                if event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_ESCAPE, pygame.K_q):
                        running = False
                    elif event.key in (pygame.K_SLASH, pygame.K_f):
                        typing = typing_from = browser.search
                    elif event.key == pygame.K_c:
                        # The selected game names the platform; the diagram is
                        # per-platform, because that is the grain the bindings
                        # are written at.
                        if state.game is not None:
                            controllers = state.game.platform
                    elif event.key == pygame.K_TAB:
                        # The panel, which is every filter in one place and
                        # both directions on each. It used to cycle platforms
                        # forwards and, with shift, regions — a controller
                        # could reach the first and not the second.
                        panel = Filters()
                    elif event.key == pygame.K_LEFT:
                        state.move(-1, 0)
                    elif event.key == pygame.K_RIGHT:
                        state.move(1, 0)
                    elif event.key == pygame.K_UP:
                        state.move(0, -1)
                    elif event.key == pygame.K_DOWN:
                        state.move(0, 1)
                    elif event.key == pygame.K_PAGEDOWN:
                        state.turn(1)
                    elif event.key == pygame.K_PAGEUP:
                        state.turn(-1)
                    elif event.key == pygame.K_SPACE:
                        space.down(time.monotonic())
                    elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                        if state.game is not None:
                            menu = Menu(
                                state.game,
                                state.selected,
                                browser.is_installed(state.game),
                                variants_for(state.game),
                                version_names(versions_for(state.game)),
                                columns=browser.columns,
                                installing=installs.running(state.game.key),
                            )
                    elif event.key == pygame.K_i:
                        browser.toggle_installed()
                    elif event.key == pygame.K_s:
                        storage = Storage()
                        storage.refresh()
                elif event.type == pygame.MOUSEMOTION:
                    # Hover moves the cursor, so the pointer and the stick drive
                    # one selection rather than two competing highlights. A gap
                    # leaves it where it was.
                    over = hovering(browser, event.pos, screen.get_size())
                    if over is not None:
                        state.select(over)
                elif event.type == pygame.MOUSEBUTTONDOWN:
                    if event.button == 1:
                        over = hovering(browser, event.pos, screen.get_size())
                        # Only ever the tile actually under the pointer: hover has
                        # already put the cursor there, so this cannot launch
                        # something the click was not on.
                        if over is not None and state.select(over):
                            menu = Menu(
                                state.game,
                                state.selected,
                                browser.is_installed(state.game),
                                variants_for(state.game),
                                version_names(versions_for(state.game)),
                                columns=browser.columns,
                                installing=installs.running(state.game.key),
                            )
                    # No button 4/5 here: SDL2 reports a wheel as MOUSEWHEEL *and*
                    # as those two for compatibility, so handling both turns the
                    # page twice for one scroll.
                elif event.type == pygame.KEYUP and event.key == pygame.K_SPACE:
                    # A tap is the menu, as space always was; a hold that
                    # finished has already seated the keyboard and this is
                    # just the key coming back up.
                    if space.up() and state.game is not None:
                        menu = Menu(
                            state.game,
                            state.selected,
                            browser.is_installed(state.game),
                            variants_for(state.game),
                            version_names(versions_for(state.game)),
                            columns=browser.columns,
                            installing=installs.running(state.game.key),
                        )
                elif event.type == pygame.MOUSEWHEEL:
                    browser.grid.turn(-1 if event.y > 0 else 1)
                elif pads.direction(event) is not None:
                    state.move(*pads.direction(event))
                elif pads.button(event) is not None:
                    # By name, not by index: the shoulders are 4 and 5 on an
                    # Xbox pad and 9 and 10 on a Steam Controller, and this used
                    # to compare against the first pair on both.
                    pressed = pads.button(event)
                    if pressed == pads.A:
                        if state.game is not None:
                            menu = Menu(
                                state.game,
                                state.selected,
                                browser.is_installed(state.game),
                                variants_for(state.game),
                                version_names(versions_for(state.game)),
                                columns=browser.columns,
                                installing=installs.running(state.game.key),
                            )
                    elif pressed == pads.B:
                        running = False
                    elif pressed == pads.LB:
                        state.turn(-1)
                    elif pressed == pads.RB:
                        state.turn(1)
                    elif pressed == pads.Y:
                        # The next platform: quicker than the panel when it is
                        # the next one that is wanted.
                        browser.cycle_platform(1)
                    elif pressed == pads.X:
                        # Search. A Deck raises the Steam keyboard over this.
                        typing = typing_from = browser.search
                    elif pressed == pads.START:
                        # Start: the filter panel, which is where installed-only
                        # now lives along with everything else that narrows the
                        # library. Start is the button somebody presses looking
                        # for options, so that is what it opens.
                        panel = Filters()

            # Installs running behind the grid: one landing is a badge, and
            # the versions the client listed were about a game not yet there.
            if installs.poll():
                browser.set_installed(installed_games())
                forget_versions()
            # Completion first, drawing second: a finished steam-add clears
            # the preparer, and this same frame must already be the grid's.
            if preparer is not None and not prepare_failed and not preparer.running:
                if preparer.ok:
                    if after_prepare is None:
                        preparer = None  # steam add done — back to the grid
                    elif after_prepare in ("install", "uninstall"):
                        browser.set_installed(installed_games())
                        # What the client said about versions was about a game
                        # that is no longer there -- or not there yet.
                        forget_versions()
                        preparer = None
                    else:
                        chosen = (preparer.game, after_prepare, preparer.variant, preparer.version)
                        running = False
                else:
                    prepare_failed = True
            # Reconnected here rather than on a timer: connect() on an absent
            # socket fails at once with ENOENT, and a daemon started while the
            # picker is open should be picked up without restarting it.
            if not padmap.connected:
                if padmap_watch.due(time.monotonic()):
                    padmap_watch.mark(time.monotonic())
                    # Not fresh: a daemon that died mid-session restores the
                    # seats it had, which is what somebody halfway through an
                    # evening wants back.
                    padmap_trouble = ensure_daemon(force=True, follow=os.getpid())
                padmap.connect()
            # Keeping up with padmap, once a frame: fold in what it said and
            # keep it listening for a hold. Who may move the cursor is not a
            # question here -- pads.py refuses anything padmap did not
            # publish, on every event, with no switch to turn that off.
            listen = attend(padmap, seating, watch)
            if listen is not None:
                padmap.send(listen)
            keyboard = space.due(time.monotonic())
            if keyboard is not None:
                trace.say("sent", **keyboard)
                padmap.send(keyboard)

            painting = time.perf_counter()
            # The full-screen views draw into the band below the strip rather
            # than under it: each starts its heading a sixteenth of the way
            # down, which on a 800-pixel screen is where the strip ends. None
            # of them hit-tests, so a subsurface costs nothing and no screen
            # carries a copy of the strip's height.
            below = screen.subsurface(
                (0, STRIP_HEIGHT, screen.get_width(), screen.get_height() - STRIP_HEIGHT)
            )
            if preparer is not None:
                draw_prepare(
                    below, font_at, preparer.game, preparer.tail(28), prepare_failed,
                    progress=preparer.progress if preparer.running else None,
                    elapsed=preparer.elapsed,
                )
            elif storage is not None:
                draw_storage(below, font_at, storage, storage_typing)
            elif panel is not None:
                # The grid behind it, so changing a filter is visibly changing
                # the thing underneath rather than a number on a form.
                if browser.view == SHELF:
                    draw_shelf(below, state, font_at, art, browser.status, browser.installed, installs.rings())
                else:
                    draw(below, state, font_at, art, browser.status, None, None, browser.installed, installs.rings())
                draw_filters(below, font_at, browser, panel, typing)
            elif controllers is not None or seating.open:
                if seating.open or seating.view.finished:
                    draw_assign(below, font_at, seating.view)
                else:
                    control_anchors = control_places(assets_dir(), controllers, controller_art)
                    if focus not in control_anchors:
                        focus = around.first(control_anchors)
                    draw_controllers(
                        below, assets_dir(), controllers, font_at, controller_art,
                        highlight=focus,
                    )
            else:
                # Whatever the workers finished since the last frame stops being a
                # placeholder now. Only the page on screen is ever asked for.
                for game in loader.done():
                    art.pop(game.key, None)
                for game in state.page:
                    surface_for(game)
                if browser.view == SHELF and typing is None:
                    # The menu is drawn over the list rather than swapping the
                    # screen back to the grid underneath it, which is what
                    # happened before and moved every game on screen.
                    draw_shelf(screen, state, font_at, art, browser.status, browser.installed, installs.rings())
                else:
                    # Typing still belongs to the grid: the search box is drawn
                    # there, and a shelf with its own copy would be two to keep
                    # in step.
                    draw(screen, state, font_at, art, browser.status, typing, menu, browser.installed, installs.rings())
                if menu is not None:
                    draw_menu(screen, menu, view_rects(browser, screen.get_size()), font_at)

            # Last, and over everything: which seat a person is in is the one
            # thing worth knowing on every screen, and drawing it after the
            # others means no screen has to leave room for it.
            draw_strip(
                screen,
                font_at,
                padmap.players,
                padmap.slots,
                strip_status(padmap.status_word, len(padmap.players))
                if padmap.connected
                else (padmap_trouble or status_text(padmap.status_word)),
                # `filling`, not the last reading: a hold let go is padmap
                # going quiet, and the strip has to empty on its own.
                progress=seating.filling(time.monotonic()) or space.progress(time.monotonic()),
                joining="keyboard" if space.since is not None else None,
            )
            drawn = time.perf_counter()
            pygame.display.flip()
            shown = time.perf_counter()
            # The loader only mirrors streamed text; 30fps halves the redundant
            # re-render of a mostly-unchanged tail across a minutes-long build.
            clock.tick(30 if preparer is not None else 60)
            ticked = time.perf_counter()
            said = fps.frame(drawn - painting, shown - drawn, ticked - shown, ticked)
            if said is not None:
                trace.say("frame", **said)
                if meter.wanted():
                    print(
                        "gotg-ui: {fps} fps   draw {draw_ms} ms   present {present_ms} ms   "
                        "idle {idle_ms} ms   worst {worst_ms} ms".format(**said),
                        file=sys.stderr,
                    )

    finally:
        # However the loop ends — quit, exec handoff, Ctrl-C, a crash — the
        # install must not be orphaned: its process group is detached from the
        # terminal, so nothing else will ever stop it.
        if preparer is not None and (chosen is None or preparer.game != chosen[0]):
            preparer.cancel()
        # padmap goes on listening for a hold. It used to be told to stop
        # here, on the theory that a game has its own idea of what a button
        # does -- but the daemon seats only pads that hold no seat, so a
        # button in a game reseats nobody, and a second player arriving
        # mid-level is exactly who this is for. Seating outlives this client
        # in the daemon, which is what lets it.

    # The gate, in this window rather than in a second one.
    #
    # The picker used to close its display and exec the client, and the client
    # started `gotg-seat`, which opened another window -- one program, two
    # windows, a black flicker between them, and the seat somebody had just
    # taken thrown away in the middle of it. It runs here now, while the
    # window is still up and the seats still stand, and the client is told the
    # gate has been met. `theme.gate_in_window: false` puts it back in its own
    # process, which is still how Steam and a bare terminal meet it.
    if chosen is not None and chosen[1] == "play" and config.get("theme.gate_in_window", True):
        try:
            seat.before_launch(screen, clock, font_at, padmap, chosen[0].platform, chosen[0].title, hush)
            os.environ["GOTG_SEAT_MET"] = "1"
        except Exception as error:  # noqa: BLE001 - a screen must never stop a launch
            trace.say("gate-in-window-failed", why=str(error))

    # Before the caller execs: the emulator must not inherit a window and a
    # grabbed GPU from a process that is about to stop existing -- nor the
    # controllers' keyboards, which are padmap's to hold from here on.
    hush.release()
    pygame.quit()
    return chosen
