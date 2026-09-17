"""Installs that run behind the grid.

Install from the menu used to take the loader screen, the way Play does; but
Play needs the game *now*, and Install is "have it for later" -- there is no
reason to stare at a bar for twenty minutes when the grid could stay usable.
So an install started here is a Preparer nobody is watching, drawn as a ring
on its tile from the same progress lines the loader reads, and landing as a
badge when it ends.

One per game. Starting a second for the same game while one runs is a
no-op: the client holds a lock per game anyway, and the second would only
queue behind the first and look stuck. Play on a game that is installing
adopts the running install into the loader (`take`) for the same reason.

pygame-free: what is held is which games are on their way and how far, and
that is what is worth a test.
"""

from __future__ import annotations

from .catalog import Game
from .prepare import Preparer, Progress


class Installs:
    """The installs running right now, by (platform, id)."""

    def __init__(self):
        self._running: dict[tuple[str, str], Preparer] = {}
        # What a failed one said last, kept until the next attempt: the ring
        # turns red, and the menu offers Install again.
        self.failed: dict[tuple[str, str], str] = {}

    @property
    def keys(self) -> set[tuple[str, str]]:
        return set(self._running)

    def running(self, key: tuple[str, str]) -> bool:
        return key in self._running

    def start(self, game: Game, variant: str | None = None, version: str | None = None) -> None:
        if game.key in self._running:
            return
        self.failed.pop(game.key, None)
        self._running[game.key] = Preparer(game, ["install"], variant, version)

    def progress(self, key: tuple[str, str]) -> Progress | None:
        preparer = self._running.get(key)
        return preparer.progress if preparer is not None else None

    def take(self, key: tuple[str, str]) -> Preparer | None:
        """Hand a running install to whoever wants to watch it; it is no
        longer this object's to poll."""
        return self._running.pop(key, None)

    def cancel(self, key: tuple[str, str]) -> None:
        preparer = self._running.pop(key, None)
        if preparer is not None:
            preparer.cancel()

    def poll(self) -> list[tuple[str, str]]:
        """The installs that finished well since last asked, and no longer
        running. A failure moves to `failed` with its last line."""
        done = []
        for key, preparer in list(self._running.items()):
            if preparer.running:
                continue
            del self._running[key]
            if preparer.ok:
                done.append(key)
            else:
                last = [line for line in preparer.tail(3) if line.strip()]
                self.failed[key] = last[-1] if last else "install failed"
        return done

    def rings(self) -> dict[tuple[str, str], tuple[float | None, bool]]:
        """What to draw on each tile: (fraction, failed). None for a fraction
        is an install with no figures yet -- a build, or the first tick."""
        out: dict[tuple[str, str], tuple[float | None, bool]] = {}
        for key, preparer in self._running.items():
            progress = preparer.progress
            out[key] = (progress.fraction if progress is not None else None, False)
        for key in self.failed:
            out.setdefault(key, (1.0, True))
        return out
