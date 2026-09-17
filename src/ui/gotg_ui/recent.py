"""A small cache that forgets the least recently used thing.

The picker scales every visible cover to fit its tile, and was doing it again
on every frame for art that had not changed. Measured on a Steam Deck: 7.4 ms
of a 16.7 ms frame to rescale ten covers, and 9.0 ms for a shelf of
twenty-four. Nearly half the frame spent producing the same pixels as last
time.

Bounded, because the alternative is a dictionary that grows by one surface per
game per size for a library of nine thousand. The bound is small on purpose --
a screen shows tens of covers, not hundreds -- so this is not really a cache of
the library, it is a cache of what is on screen and what was on screen a moment
ago.

No pygame here: what is stored is the caller's business and the eviction is
what is worth a test.
"""

from __future__ import annotations

from typing import Any


class Recent:
    """The last `capacity` values, by key, oldest evicted first."""

    def __init__(self, capacity: int = 64):
        self.capacity = max(1, capacity)
        self._items: dict[Any, Any] = {}

    def get(self, key: Any) -> Any | None:
        """The value, and a note that it was wanted just now.

        Re-inserting is what makes it least-*recently*-used rather than
        least-recently-*added*: the tile under the cursor is asked for every
        frame, and would otherwise age out while it was being looked at.
        """
        if key not in self._items:
            return None
        value = self._items.pop(key)
        self._items[key] = value
        return value

    def put(self, key: Any, value: Any) -> Any:
        """Keep it, evicting the oldest if there is no room. Returns it, so a
        caller can store and use in one line."""
        self._items.pop(key, None)
        self._items[key] = value
        while len(self._items) > self.capacity:
            # Dicts keep insertion order, so the first key is the oldest.
            del self._items[next(iter(self._items))]
        return value

    def clear(self) -> None:
        self._items.clear()

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, key: Any) -> bool:
        return key in self._items
