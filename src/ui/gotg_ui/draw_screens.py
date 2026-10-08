"""Drawing the filter panel, storage, the saves choice and the loader: SDL and
pygame only.

Pulled out of app.py with no change of behaviour (see draw_grid.py). The Saves
list is saves_draw.py's.
"""

from __future__ import annotations

import pygame

from . import filters, prepare
from .catalog import Game
from .draw_grid import SCALED
from .saves_choice import HERE, REMOTE, Choice
from .saves_choice import when as saves_when
from .storage import Storage, human
from .theme import BACKGROUND, PANEL, TEXT, TEXT_DIM, TILE, TILE_SELECTED


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
    overlay = SCALED.get(("panel", width, height))
    if overlay is None:
        overlay = pygame.Surface((width, height), pygame.SRCALPHA)
        overlay.fill((*BACKGROUND, 232))
        SCALED.put(("panel", width, height), overlay)
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
    # Upwards when there is no room screen, which there is not for the last row.
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


def draw_saves_choice(screen, font_at, choice: Choice | None, title: str) -> None:
    """Two saves side by side: which machine each is on, and when it changed.

    None is the moment before there is anything to choose -- the client is
    still bundling and asking -- and says so rather than showing the grid.
    """
    width, height = screen.get_size()
    screen.fill(BACKGROUND)
    margin = height // 16
    if choice is None:
        said = font_at(30).render(f"Checking the saves for {title[:60]}…", True, TEXT)
        screen.blit(said, ((width - said.get_width()) // 2, height // 2 - said.get_height()))
        hint = font_at(18).render("B / Escape — back", True, TEXT_DIM)
        screen.blit(hint, (margin, height - margin - hint.get_height()))
        return
    heading = font_at(34).render(f"Two different saves for {choice.game.title[:50]}", True, TEXT)
    screen.blit(heading, ((width - heading.get_width()) // 2, margin))
    lede = font_at(20).render(
        "Both were played since they last matched. Keep one — the other is kept aside, not deleted.", True, TEXT_DIM
    )
    screen.blit(lede, ((width - lede.get_width()) // 2, margin + heading.get_height() + 12))

    remote = choice.check.remote
    cards = (
        (HERE, "This machine", choice.check.here.device, choice.check.here.updated),
        (REMOTE, "Saved on the service", remote.device if remote else "", remote.updated if remote else ""),
    )
    gap = width // 24
    card_w = (width - 2 * margin - gap) // 2
    card_h = height // 3
    top = height // 2 - card_h // 2
    for index, (side, label, device, updated) in enumerate(cards):
        lit = choice.selected == side
        left = margin + index * (card_w + gap)
        box = pygame.Rect(left, top, card_w, card_h)
        pygame.draw.rect(screen, TILE_SELECTED if lit else TILE, box, border_radius=12)
        y = top + card_h // 8
        for text, size, colour in (
            (label, 22, TEXT_DIM if not lit else TEXT),
            (device or "an unnamed machine", 36, TEXT),
            (f"updated {saves_when(updated)}", 24, TEXT if lit else TEXT_DIM),
        ):
            line = font_at(size).render(text[:48], True, colour)
            screen.blit(line, (left + (card_w - line.get_width()) // 2, y))
            y += line.get_height() + card_h // 10
    keys = "Left / Right — choose   ·   A / Enter — keep this one   ·   B / Escape — back"
    hint = font_at(18).render(keys, True, TEXT_DIM)
    screen.blit(hint, (margin, height - margin - hint.get_height()))


def _draw_progress_bar(screen, font_at, y: int, margin: int, fraction: float | None, figures: str, what: str) -> int:
    """One bar with its figures under it; returns where the next thing goes."""
    width, height = screen.get_size()
    bar_h = max(10, height // 45)
    bar = pygame.Rect(margin, y, width - 2 * margin, bar_h)
    pygame.draw.rect(screen, TILE, bar, border_radius=bar_h // 2)
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
    text = font_at(20).render(figures, True, TEXT)
    screen.blit(text, (margin, y))
    detail = font_at(16).render(what[:120], True, TEXT_DIM)
    screen.blit(detail, (width - margin - detail.get_width(), y + 2))
    return y + text.get_height() + margin // 2


def draw_prepare(
    screen,
    font_at,
    game: Game | None,
    lines: list[str],
    failed: bool,
    progress: prepare.Progress | None = None,
    elapsed: float = 0.0,
    stage: prepare.Stage | None = None,
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

    if game is None:
        # `gotg update self`: GOTG itself, the roots Steam starts, and then a
        # restart into the new picker; the client's lines say what became
        # of the library when it fails.
        if failed:
            heading = "could not update GOTG"
            hint = "B / Escape — back to the grid   ·   full log: gotg update self"
            colour = (224, 96, 96)
        else:
            heading = "updating GOTG — the picker restarts when it is done"
            hint = "B / Escape — cancel; the library stays as it was"
            colour = TEXT
    elif failed:
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
    # The download and the emulator's build run at once, so each has a bar:
    # what nix is doing first, since it is the one that used to say nothing.
    for shown in (stage, progress):
        if shown is not None and not failed:
            y = _draw_progress_bar(screen, font_at, y, margin, shown.fraction, shown.describe(), shown.what)
    line_font = font_at(16)
    for line in lines:
        if y > height - margin * 2:
            break
        screen.blit(line_font.render(line[:180], True, TEXT_DIM), (margin, y))
        y += line_font.get_height() + 2

    label = font_at(18).render(hint, True, TEXT_DIM)
    screen.blit(label, (margin, height - margin - label.get_height()))
