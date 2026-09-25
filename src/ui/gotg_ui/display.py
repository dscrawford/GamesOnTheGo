"""The window, and how a finished frame gets onto the panel.

Every screen here is drawn on the CPU into one 1280x800 surface, and that
part is cheap: 1.4 ms a frame, measured on a real evening. What was wrong was
the last step. pygame's plain window copies the surface to the screen and
returns -- 1.1 ms, and no waiting for the panel whatever `vsync=1` asked for
-- so frames were timed by a sleep and landed on a 165 Hz panel two and three
refreshes apart. See pace.py for what that looked like and how it is timed
now.

So the surface goes to the panel through an SDL renderer instead: uploaded to
a streaming texture, drawn scaled into the window, presented on the panel's
beat. 2.9 ms upload and present measured here, which is what pygame's own
`SCALED` mode costs too -- that already uses a renderer internally, so the
full-screen picker gains an honest vsync and nothing else, and the windowed
one gains vsync it never had. Drawing with textures instead of surfaces would
be a rewrite of every screen for a millisecond and a half; not worth it.

Scaled with linear filtering, not nearest. At 1280x800 on a 4K panel the
factor is 2.7, and nearest-neighbour makes a dot moving one pixel a frame
move two physical pixels, then three, then two -- a stutter of its own that
has nothing to do with timing.

When a renderer cannot be had -- no GPU, a driver that refuses -- this falls
back to exactly what it did before, and says so in the trace.
`GOTG_UI_RENDERER=window` forces the old way, for comparing the two.
"""

from __future__ import annotations

import os
import time

import pygame

from . import config, trace
from .letterbox import fit, to_frame
from .pace import Pace, wait

TITLE = "GamesOnTheGo"

_alpha: pygame.Surface | None = None


def alpha_format() -> pygame.Surface:
    """A surface whose pixel format images are converted to.

    `convert_alpha()` asks the display for a format, and a renderer's window
    has no display surface to ask -- it raises "No convert format has been
    set". Converting *to a surface* needs no display at all, and a 32-bit
    surface with alpha is what the display format would have been anyway.
    """
    global _alpha
    if _alpha is None:
        _alpha = pygame.Surface((1, 1), pygame.SRCALPHA, 32)
    return _alpha


def image(path) -> pygame.Surface:
    """An image from disk, ready to blit. Safe off the main thread: nothing
    here touches the window."""
    return pygame.image.load(str(path)).convert(alpha_format())


class Display:
    """One window: a surface to draw on, and a way to show it on time."""

    def __init__(self, surface, show, *, refresh: float, vsync: bool, gpu: bool, window=None, info=None):
        self.surface = surface
        self._show = show
        self.window = window
        self.gpu = gpu
        self.pace = Pace(refresh=refresh or 60.0, vsync=vsync)
        self.info = info or {}
        self._asked_monitor = 0.0

    def present(self) -> None:
        """Put the frame on the panel. With vsync that holds, this is where
        the loop waits for the panel, and pace.py plans around it."""
        self._show()
        undecided = self.pace.holds is None
        self.pace.presented(time.monotonic())
        if undecided and self.pace.holds is not None:
            # Said once: whether the panel waits is the difference between
            # the two ways this paces, and a trace should not need the fps
            # lines to be read backwards to know which one ran.
            trace.say("paced", vsync_holds=self.pace.holds, refresh=round(self.pace.refresh, 1))

    def rest(self, cap: float | None = None, woken=None) -> None:
        """Sleep until the next frame should start. An idle screen's long rest
        ends the moment SDL has an event or `woken` says danstick spoke."""
        now = time.monotonic()
        if self.window is not None and now >= self._asked_monitor:
            # Once a second: a window dragged to another panel is paced for
            # that one from then on.
            self._asked_monitor = now + 1.0
            index = _window_display(self.window)
            if index is not None:
                self.pace.moved(_desktop_refresh(index))
        wait(self.pace.rest(now, cap), woken, peek=pygame.event.peek)

    def mouse(self, event) -> None:
        """Mouse positions in frame coordinates, in place.

        The renderer draws the frame letterboxed into whatever size the
        window is, and SDL reports the pointer in the window's pixels. The
        grid hit-tests in the frame's, so without this a click on a 4K panel
        landed on the tile at a third of the distance. `SCALED` did this
        conversion itself; the renderer path has to.
        """
        if not self.gpu or self.window is None:
            return
        pos = getattr(event, "pos", None)
        if pos is None:
            return
        size = self.surface.get_size()
        box = fit(size, tuple(self.window.size))
        event.pos = to_frame(pos, box, size)

    def refreshing(self) -> float:
        """The panel's rate, as the probe measured it once it could."""
        return self.pace.refresh


def open(size: tuple[int, int], *, fullscreen: bool = False) -> Display:
    """The window this program draws in, the best way this machine allows."""
    vsync = bool(config.get("theme.vsync", True))
    want_gpu = os.environ.get("GOTG_UI_RENDERER", "") != "window" and bool(config.get("theme.renderer", True))
    shown = _renderer(size, fullscreen, vsync) if want_gpu else None
    if shown is None:
        shown = _window(size, fullscreen, vsync)
    shown.info.update(
        {
            "driver": pygame.display.get_driver(),
            "drawing": list(size),
            "desktop": [list(s) for s in pygame.display.get_desktop_sizes()],
            "fullscreen": fullscreen,
            "vsync": vsync,
            "gpu": shown.gpu,
            "refresh": shown.pace.refresh,
        }
    )
    trace.say("display", **shown.info)
    return shown


def _desktop_refresh(index: int = 0) -> float:
    try:
        rates = pygame.display.get_desktop_refresh_rates()
    except (pygame.error, AttributeError):
        return 60.0
    if not rates:
        return 60.0
    rate = rates[index] if 0 <= index < len(rates) else rates[0]
    return float(rate) if rate else 60.0


_sdl = None


def _window_display(window) -> int | None:
    """Which monitor this window is on, by SDL's own count.

    pygame-ce's Window does not say, and a desk with a 60 Hz panel first and
    two 165 Hz ones after was paced for 60 wherever the window sat. SDL knows:
    the library pygame already loaded answers `SDL_GetWindowDisplayIndex`
    through the window's id. None when that cannot be asked, and the first
    monitor's rate stands.
    """
    global _sdl
    try:
        if _sdl is None:
            import ctypes

            lib = ctypes.CDLL("libSDL2-2.0.so.0")
            lib.SDL_GetWindowFromID.restype = ctypes.c_void_p
            lib.SDL_GetWindowFromID.argtypes = [ctypes.c_uint32]
            lib.SDL_GetWindowDisplayIndex.argtypes = [ctypes.c_void_p]
            lib.SDL_GetWindowDisplayIndex.restype = ctypes.c_int
            _sdl = lib
        pointer = _sdl.SDL_GetWindowFromID(window.id)
        if not pointer:
            return None
        index = _sdl.SDL_GetWindowDisplayIndex(pointer)
    except (OSError, AttributeError):
        _sdl = False
        return None
    return index if index >= 0 else None


def _renderer(size, fullscreen: bool, vsync: bool) -> Display | None:
    """A window with an SDL renderer behind it, or None if there is none to be had."""
    from pygame._sdl2.video import Renderer, Texture

    window = None
    try:
        window = pygame.Window(TITLE, size, fullscreen_desktop=fullscreen)
        try:
            renderer = Renderer(window, vsync=vsync)
        except pygame.error as error:
            # A driver that will render but not wait for the panel. Still
            # better than the plain window; pace.py will find out it does not
            # hold and time the frames itself.
            trace.say("no-vsync", why=str(error))
            renderer = Renderer(window)
        texture = Texture(renderer, size, streaming=True, scale_quality=1)
    except (pygame.error, OSError, ValueError) as error:
        trace.say("no-renderer", why=str(error))
        if window is not None:
            window.destroy()
        return None

    surface = pygame.Surface(size, 0, 32)

    def show() -> None:
        texture.update(surface)
        renderer.draw_color = (0, 0, 0, 255)
        renderer.clear()
        texture.draw(dstrect=fit(size, tuple(window.size)))
        renderer.present()

    index = _window_display(window)
    return Display(
        surface,
        show,
        refresh=_desktop_refresh(index or 0),
        vsync=vsync,
        gpu=True,
        window=window,
        info={"renderer": "sdl", "monitor": index},
    )


def _window(size, fullscreen: bool, vsync: bool) -> Display:
    """The way it was: pygame's own window, `SCALED` when full screen."""
    pygame.display.set_caption(TITLE)
    flags = (pygame.FULLSCREEN | pygame.SCALED) if fullscreen else 0
    surface = None
    if vsync:
        try:
            surface = pygame.display.set_mode(size, flags, vsync=1)
        except pygame.error as error:
            trace.say("no-vsync", why=str(error))
    if surface is None:
        surface = pygame.display.set_mode(size, flags)
    try:
        refresh = float(pygame.display.get_current_refresh_rate()) or _desktop_refresh()
    except pygame.error:
        refresh = _desktop_refresh()
    return Display(
        surface,
        pygame.display.flip,
        refresh=refresh,
        vsync=vsync,
        gpu=False,
        info={"renderer": "window"},
    )
