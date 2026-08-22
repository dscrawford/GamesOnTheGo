"""The grid itself: the only part that needs a screen.

Kept thin on purpose. Which games exist and where the tiles go are both
answerable without a display, and both live next door; what is left here is
drawing them and reading a controller.

Milestone one draws placeholders and launches nothing. Art and `gotg play`
come after this has been sat in front of on a Deck.
"""

from __future__ import annotations

import pygame

from .catalog import Game, Library
from .grid import Grid
from .layout import grid

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


def draw(screen, state: Grid, font_at) -> None:
    width, height = screen.get_size()
    screen.fill(BACKGROUND)
    page = state.page
    tiles = grid(width, height)

    for index, tile in enumerate(tiles):
        if index >= len(page):
            break
        game = page[index]
        selected = index == state.selected
        pygame.draw.rect(screen, TILE_SELECTED if selected else TILE, tile.rect, border_radius=8)
        if selected:
            pygame.draw.rect(screen, TEXT, tile.rect, width=3, border_radius=8)

        # Until there is art, the tile is the title — which is also what a
        # game with no art anywhere will always fall back to.
        inner = tile.width - 16
        title = font_at(20).render(game.title, True, TEXT)
        if title.get_width() > inner:
            title = _fit(font_at, game.title, inner, 20).render(game.title, True, TEXT)
        screen.blit(title, (tile.x + 8, tile.y + tile.height // 2 - title.get_height() // 2))

        platform = font_at(14).render(game.platform, True, TEXT_DIM)
        screen.blit(platform, (tile.x + 8, tile.y + tile.height - platform.get_height() - 8))

    if state.library.pages:
        status = f"page {state.page_index + 1} of {state.library.pages}  ·  {len(state.library)} games"
    else:
        status = "no games in the catalog"
    label = font_at(18).render(status, True, TEXT_DIM)
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

    state = Grid(library)
    chosen: Game | None = None
    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_ESCAPE, pygame.K_q):
                    running = False
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

        draw(screen, state, font_at)
        pygame.display.flip()
        clock.tick(60)

    # Before the caller execs: the emulator must not inherit a window and a
    # grabbed GPU from a process that is about to stop existing.
    pygame.quit()
    return chosen
