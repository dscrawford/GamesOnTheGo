"""The grid itself: the only part that needs a screen.

Kept thin on purpose. Which games exist and where the tiles go are both
answerable without a display, and both live next door; what is left here is
drawing them and reading a controller.

Milestone one draws placeholders and launches nothing. Art and `gotg play`
come after this has been sat in front of on a Deck.
"""

from __future__ import annotations

import pygame

from .art import ArtStore
from .browser import Browser
from .catalog import Game, Library
from .controllers import assets_dir
from .controllers import draw as draw_controllers
from .fetch import Loader
from .grid import Grid
from .installed import installed_games
from .layout import grid, tile_at
from .menu import Menu
from .prepare import Preparer, is_ready
from .storage import Storage, human

BACKGROUND = (18, 18, 20)
TILE = (38, 38, 44)
TILE_SELECTED = (58, 104, 148)
TEXT = (232, 232, 236)
TEXT_DIM = (150, 150, 158)

# The Deck's own panel, so a window on a desktop is the shape it will be there.
WINDOW = (1280, 800)


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
    pygame.draw.circle(screen, BACKGROUND, (cx, cy), r + 2)
    pygame.draw.circle(screen, TILE_SELECTED, (cx, cy), r)
    shaft = r // 2
    head = r // 2
    stroke = max(2, r // 5)
    pygame.draw.line(screen, TEXT, (cx, cy - shaft), (cx, cy + shaft // 2), stroke)
    pygame.draw.polygon(screen, TEXT, [(cx - head, cy), (cx + head, cy), (cx, cy + head)])
    pygame.draw.line(screen, TEXT, (cx - head, cy + head + 2), (cx + head, cy + head + 2), stroke)


def draw(
    screen,
    state: Grid,
    font_at,
    art=None,
    status: str = "",
    typing: str | None = None,
    menu=None,
    installed: set[tuple[str, str]] | None = None,
) -> None:
    width, height = screen.get_size()
    screen.fill(BACKGROUND)
    page = state.page
    tiles = grid(width, height)
    # One translucent wash per tile size, cached: while the menu is open every
    # other tile drops to ~90% so the chosen one reads as chosen.
    dim = None
    if menu is not None:
        dim = pygame.Surface((tiles[0].width, tiles[0].height), pygame.SRCALPHA)
        dim.fill((*BACKGROUND, 26))

    for index, tile in enumerate(tiles):
        if index >= len(page):
            break
        game = page[index]
        selected = index == state.selected
        picture = art.get(game.key) if art else None

        if picture is not None:
            # Fitted, never stretched. A tile is 2:3 and a cartridge box is
            # landscape, so most of this library's art arrives the wrong shape
            # for the slot it goes in; scaling to fill would squash every one.
            pw, ph = picture.get_size()
            scale = min(tile.width / pw, tile.height / ph)
            size = (max(1, int(pw * scale)), max(1, int(ph * scale)))
            pygame.draw.rect(screen, TILE, tile.rect, border_radius=8)
            screen.blit(
                pygame.transform.smoothscale(picture, size),
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


def menu_rects(menu: Menu, tiles, font_at) -> list[tuple[int, int, int, int]]:
    """One rect per action row, beside the tile on the side with room.

    Computed here and only here, so the drawing and the pointer hit-testing
    cannot disagree about where a row is.
    """
    tile = tiles[menu.tile_index]
    row_h = font_at(22).get_height() + 14
    width = max(font_at(22).size(label)[0] for label, _ in menu.actions) + 32
    height = row_h * len(menu.actions) + 8
    gap = 10
    x = tile.x + tile.width + gap if menu.side == "right" else tile.x - gap - width
    y = tile.y + (tile.height - height) // 2
    return [(x, y + 4 + i * row_h, width, row_h) for i in range(len(menu.actions))]


def draw_menu(screen, menu: Menu, tiles, font_at) -> None:
    rows = menu_rects(menu, tiles, font_at)
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


def draw_prepare(screen, font_at, game: Game, lines: list[str], failed: bool) -> None:
    """The loader screen: heading, `gotg install`'s output verbatim, the way out.

    Verbatim so a build failure reads on the TV, not only in a log file.
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

    y = margin + font_at(30).get_height() + margin // 2
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
    screen = pygame.display.set_mode(WINDOW)
    clock = pygame.time.Clock()
    pygame.joystick.init()
    pads = [pygame.joystick.Joystick(i) for i in range(pygame.joystick.get_count())]
    for pad in pads:
        pad.init()

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

    chosen: tuple[Game, str] | None = None
    typing: str | None = None
    menu: Menu | None = None
    # Which platform's bindings are being looked at, or None for the grid.
    # A screen rather than an overlay: it is a page of reference, not an
    # action, and nothing underneath it should keep moving.
    controllers: str | None = None
    controller_art: dict = {}
    # The storage screen, and the path being typed to add to it.
    storage: Storage | None = None
    storage_typing: str | None = None
    # The loader phase: a verb that needs work spawns the client and the grid
    # gives way to its output until it finishes, fails, or is cancelled.
    preparer: Preparer | None = None
    prepare_failed = False
    # What happens when the loader succeeds: exec the verb, or come back here.
    after_prepare: str | None = None
    running = True

    def pick(game: Game | None, verb: str = "play") -> None:
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
            # Not an exec: the shortcut is written, the grid comes back.
            preparer = Preparer(game, ["steam", "add"])
            after_prepare = None
            return
        if verb == "uninstall":
            # Through the loader like steam-add, so what was removed is read
            # rather than guessed; then the badges are asked for again.
            preparer = Preparer(game, ["uninstall"])
            after_prepare = "uninstall"
            return
        after_prepare = verb
        if is_ready(game):
            chosen = (game, verb)
            running = False
        else:
            preparer = Preparer(game)

    try:
        while running:
            state = browser.grid
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    if preparer is not None:
                        preparer.cancel()
                    running = False
                    continue

                # On the loader, the only input is the way out. Everything else
                # would be the grid moving invisibly behind the build.
                if preparer is not None:
                    back = (event.type == pygame.KEYDOWN and event.key in (pygame.K_ESCAPE, pygame.K_b)) or (
                        event.type == pygame.JOYBUTTONDOWN and event.button == 1
                    )
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
                    elif event.type == pygame.JOYHATMOTION:
                        dx, dy = event.value
                        if dy:
                            storage.move(-dy)
                    elif event.type == pygame.JOYBUTTONDOWN:
                        if event.button == 0:
                            storage.make_default()
                        elif event.button == 1:
                            storage = None
                        elif event.button == 2:
                            storage.remove()
                        elif event.button == 3:
                            # Y: add. A Deck raises the Steam keyboard over this.
                            storage_typing = ""
                    continue

                # Same for the controller diagram: it is a whole screen, so
                # the only input it takes is the way back.
                if controllers is not None:
                    if (event.type == pygame.KEYDOWN and event.key in (pygame.K_ESCAPE, pygame.K_b, pygame.K_c)) or (
                        event.type == pygame.JOYBUTTONDOWN and event.button == 1
                    ):
                        controllers = None
                    continue

                # While the menu is open it owns the input: the grid must
                # not move invisibly underneath the panel.
                if menu is not None:
                    if event.type == pygame.KEYDOWN:
                        if event.key in (pygame.K_ESCAPE, pygame.K_b):
                            menu = None
                        elif event.key == pygame.K_UP:
                            menu.move(-1)
                        elif event.key == pygame.K_DOWN:
                            menu.move(1)
                        elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                            picked, menu = menu, None
                            pick(picked.game, picked.action)
                    elif event.type == pygame.JOYHATMOTION:
                        dx, dy = event.value
                        if dy:
                            menu.move(-dy)  # the hat is y-up
                    elif event.type == pygame.JOYBUTTONDOWN:
                        if event.button == 0:
                            picked, menu = menu, None
                            pick(picked.game, picked.action)
                        elif event.button == 1:
                            menu = None
                    elif event.type == pygame.MOUSEMOTION:
                        rows = menu_rects(menu, grid(*screen.get_size()), font_at)
                        for i, (rx, ry, rw, rh) in enumerate(rows):
                            if rx <= event.pos[0] < rx + rw and ry <= event.pos[1] < ry + rh:
                                menu.select(i)
                    elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                        rows = menu_rects(menu, grid(*screen.get_size()), font_at)
                        hit = None
                        for i, (rx, ry, rw, rh) in enumerate(rows):
                            if rx <= event.pos[0] < rx + rw and ry <= event.pos[1] < ry + rh:
                                hit = i
                        if hit is None:
                            menu = None  # a click that misses the panel closes it
                        elif menu.select(hit):
                            picked, menu = menu, None
                            pick(picked.game, picked.action)
                    continue

                # While typing, every key is text. Nothing below runs, or the
                # letters of a search would also be moving the cursor.
                if typing is not None:
                    if event.type != pygame.KEYDOWN:
                        continue
                    if event.key == pygame.K_ESCAPE:
                        typing = None
                    elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                        browser.set_search(typing)
                        typing = None
                    elif event.key == pygame.K_BACKSPACE:
                        typing = typing[:-1]
                    elif event.unicode and event.unicode.isprintable():
                        typing += event.unicode
                    continue

                if event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_ESCAPE, pygame.K_q):
                        running = False
                    elif event.key in (pygame.K_SLASH, pygame.K_f):
                        typing = browser.search
                    elif event.key == pygame.K_c:
                        # The selected game names the platform; the diagram is
                        # per-platform, because that is the grain the bindings
                        # are written at.
                        if state.game is not None:
                            controllers = state.game.platform
                    elif event.key == pygame.K_TAB:
                        # Tab walks platforms, shift-Tab walks regions — one
                        # key for both switches. Backwards lives on R/shift-R.
                        if event.mod & pygame.KMOD_SHIFT:
                            browser.cycle_region(1)
                        else:
                            browser.cycle_platform(1)
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
                    elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                        if state.game is not None:
                            menu = Menu(state.game, state.selected, browser.is_installed(state.game))
                    elif event.key == pygame.K_i:
                        browser.toggle_installed()
                    elif event.key == pygame.K_s:
                        storage = Storage()
                        storage.refresh()
                elif event.type == pygame.MOUSEMOTION:
                    # Hover moves the cursor, so the pointer and the stick drive
                    # one selection rather than two competing highlights. A gap
                    # leaves it where it was.
                    over = tile_at(*event.pos, *screen.get_size())
                    if over is not None:
                        state.select(over)
                elif event.type == pygame.MOUSEBUTTONDOWN:
                    if event.button == 1:
                        over = tile_at(*event.pos, *screen.get_size())
                        # Only ever the tile actually under the pointer: hover has
                        # already put the cursor there, so this cannot launch
                        # something the click was not on.
                        if over is not None and state.select(over):
                            menu = Menu(state.game, state.selected, browser.is_installed(state.game))
                    # No button 4/5 here: SDL2 reports a wheel as MOUSEWHEEL *and*
                    # as those two for compatibility, so handling both turns the
                    # page twice for one scroll.
                elif event.type == pygame.MOUSEWHEEL:
                    browser.grid.turn(-1 if event.y > 0 else 1)
                elif event.type == pygame.JOYHATMOTION:
                    dx, dy = event.value
                    state.move(dx, -dy)  # SDL's hat is y-up, the grid is y-down
                elif event.type == pygame.JOYBUTTONDOWN:
                    # SDL's own mapping, which is why gotg-pads exists: A is 0,
                    # B is 1, and 4 and 5 are the shoulders.
                    if event.button == 0:
                        if state.game is not None:
                            menu = Menu(state.game, state.selected, browser.is_installed(state.game))
                    elif event.button == 1:
                        running = False
                    elif event.button == 4:
                        state.turn(-1)
                    elif event.button == 5:
                        state.turn(1)
                    elif event.button == 3:
                        # Y: the next platform. Six of them, so cycling beats a
                        # menu nobody can reach without a pointer.
                        browser.cycle_platform(1)
                    elif event.button == 2:
                        # X: search. A Deck raises the Steam keyboard over this.
                        typing = browser.search
                    elif event.button == 7:
                        # Start: toggles installed-only. Every face button is
                        # taken; Start reads as "my library" on a handheld.
                        browser.toggle_installed()

            # Completion first, drawing second: a finished steam-add clears
            # the preparer, and this same frame must already be the grid's.
            if preparer is not None and not prepare_failed and not preparer.running:
                if preparer.ok:
                    if after_prepare is None:
                        preparer = None  # steam add done — back to the grid
                    elif after_prepare == "uninstall":
                        browser.set_installed(installed_games())
                        preparer = None
                    else:
                        chosen = (preparer.game, after_prepare)
                        running = False
                else:
                    prepare_failed = True
            if preparer is not None:
                draw_prepare(screen, font_at, preparer.game, preparer.tail(28), prepare_failed)
            elif storage is not None:
                draw_storage(screen, font_at, storage, storage_typing)
            elif controllers is not None:
                draw_controllers(screen, assets_dir(), controllers, font_at, controller_art)
            else:
                # Whatever the workers finished since the last frame stops being a
                # placeholder now. Only the page on screen is ever asked for.
                for game in loader.done():
                    art.pop(game.key, None)
                for game in state.page:
                    surface_for(game)
                draw(screen, state, font_at, art, browser.status, typing, menu, browser.installed)
                if menu is not None:
                    draw_menu(screen, menu, grid(*screen.get_size()), font_at)
            pygame.display.flip()
            # The loader only mirrors streamed text; 30fps halves the redundant
            # re-render of a mostly-unchanged tail across a minutes-long build.
            clock.tick(30 if preparer is not None else 60)

    finally:
        # However the loop ends — quit, exec handoff, Ctrl-C, a crash — the
        # install must not be orphaned: its process group is detached from the
        # terminal, so nothing else will ever stop it.
        if preparer is not None and (chosen is None or preparer.game != chosen[0]):
            preparer.cancel()

    # Before the caller execs: the emulator must not inherit a window and a
    # grabbed GPU from a process that is about to stop existing.
    pygame.quit()
    return chosen
