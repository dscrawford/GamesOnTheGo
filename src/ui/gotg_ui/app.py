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
from .fetch import Loader
from .grid import Grid
from .layout import grid, tile_at

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


def draw(screen, state: Grid, font_at, art=None, status: str = "", typing: str | None = None) -> None:
    width, height = screen.get_size()
    screen.fill(BACKGROUND)
    page = state.page
    tiles = grid(width, height)

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
            title = font_at(20).render(game.title, True, TEXT)
            if title.get_width() > inner:
                title = _fit(font_at, game.title, inner, 20).render(game.title, True, TEXT)
            screen.blit(title, (tile.x + 8, tile.y + tile.height // 2 - title.get_height() // 2))
            platform = font_at(14).render(game.platform, True, TEXT_DIM)
            screen.blit(platform, (tile.x + 8, tile.y + tile.height - platform.get_height() - 8))

        if selected:
            pygame.draw.rect(screen, TEXT, tile.rect, width=3, border_radius=8)

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


def run(library: Library) -> Game | None:
    """Draw until somebody picks a game or quits, and say which happened.

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

    browser = Browser(library)
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

    chosen: Game | None = None
    typing: str | None = None
    running = True
    while running:
        state = browser.grid
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
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
                elif event.key == pygame.K_TAB:
                    browser.cycle_platform(-1 if event.mod & pygame.KMOD_SHIFT else 1)
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
                    chosen = state.game
                    running = chosen is None
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
                        chosen = state.game
                        running = chosen is None
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
                    chosen = state.game
                    running = chosen is None
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

        # Whatever the workers finished since the last frame stops being a
        # placeholder now. Only the page on screen is ever asked for.
        for game in loader.done():
            art.pop(game.key, None)
        for game in state.page:
            surface_for(game)

        draw(screen, state, font_at, art, browser.status, typing)
        pygame.display.flip()
        clock.tick(60)

    # Before the caller execs: the emulator must not inherit a window and a
    # grabbed GPU from a process that is about to stop existing.
    pygame.quit()
    return chosen
