"""Cover art, decoded on a worker thread rather than in the middle of a frame.

A page of the grid scrolling into view is ten covers nobody has decoded yet,
and they were decoded right there, in the frame that first drew them: 1.5 ms
each, 13.5 ms for ten, measured on this machine's own art -- the whole of one
frame at 60 Hz and two at 165, in the one moment somebody is watching the
screen move. The picker's worst frame on a real evening was 30 ms.

On a worker the same sixty covers took 66 ms and the frame loop never waited
more than 1.9 ms for the lock: pygame lets go of it while it decodes. So a
tile's picture turns up a frame or two later instead, drawn as its title in
between, which is what a tile whose picture has not downloaded yet already
looks like.

The loader is handed in, so a test can hold the queue without pygame.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable, Hashable
from typing import Any

# Not decoded yet: asked for, and on its way.
PENDING = object()


class Decoder:
    """Decodes pictures by key, one worker, never blocking the caller."""

    def __init__(self, load: Callable[[Any], Any], workers: int = 1):
        self._load = load
        self._todo: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._asked: set[Hashable] = set()
        self._ready: dict[Hashable, Any] = {}
        self._arrived: list[Hashable] = []
        for _ in range(workers):
            threading.Thread(target=self._work, daemon=True).start()

    def want(self, key: Hashable, path: Any) -> Any:
        """The picture, or None if it would not decode, or PENDING.

        Returns at once. A decoded picture is handed over once and forgotten
        here: the caller keeps it, and a second copy would be the library's
        art held twice.
        """
        with self._lock:
            if key in self._ready:
                self._asked.discard(key)
                return self._ready.pop(key)
            if key in self._asked:
                return PENDING
            self._asked.add(key)
        self._todo.put((key, path))
        return PENDING

    def arrived(self) -> list[Hashable]:
        """Which keys finished since last asked: the frame loop's cue that a
        tile's picture changed without anybody pressing anything."""
        with self._lock:
            done, self._arrived = self._arrived, []
        return done

    def _work(self) -> None:
        while True:
            key, path = self._todo.get()
            try:
                picture = self._load(path)
            except Exception:  # noqa: BLE001 - a bad file is one tile's problem, not the worker's
                picture = None
            with self._lock:
                self._ready[key] = picture
                self._arrived.append(key)
