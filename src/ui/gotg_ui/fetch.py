"""Getting a tile picture, off the frame loop.

The sources are the client's own — src/client/steam/artwork.py, on PYTHONPATH
from the wrapper — so the grid asks SteamGridDB and libretro-thumbnails the
same way `gotg steam art` does, through the GOTG service that holds the real
key. A second implementation would be a second thing to keep in step with an
API neither of us controls.

Imported lazily and behind a guard: the model half of this program has to run
in a venv that has neither the client nor pygame, which is how the tests run
at all.
"""

from __future__ import annotations

import json
import os
import queue
import threading
from pathlib import Path

from .art import ArtStore
from .catalog import Game

# Portrait first, then the wide capsule — and the fallback is not politeness.
# libretro files box art by its shape, and a cartridge box is landscape: "a
# 512x357 N64 box filed as a 600x900 tile would pillarbox it down the middle".
# This library is ~5670 cartridge games, so asking only for grids_portrait
# would find nothing for almost all of it whenever SteamGridDB has no match.
KINDS = ("grids_portrait", "grids")


def service() -> tuple[str, str]:
    """The url and token `gotg login` wrote. Empty when there is no config,
    which is a machine that has never logged in rather than an error: the grid
    still draws, with titles instead of pictures."""
    config = os.environ.get("GOTG_API_FILE")
    if not config:
        base = os.environ.get("GOTG_CONFIG_DIR")
        if not base:
            xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
            base = os.path.join(xdg, "gotg")
        config = os.path.join(base, "api.json")
    try:
        raw = json.loads(Path(config).read_text())
    except (OSError, ValueError):
        return "", ""
    return str(raw.get("url", "")).rstrip("/"), str(raw.get("token", ""))


def sources_for(game: Game, url: str, token: str, names_cache: Path | None = None):
    """The same two the Steam path uses, in the same order.

    SteamGridDB first, through the service that holds the real key, then
    libretro-thumbnails which needs none — so a machine with no key still gets
    pictures for everything No-Intro names.
    """
    import artwork  # noqa: PLC0415 — the client's, from PYTHONPATH
    import libretro  # noqa: PLC0415

    found = []
    if url and token:
        # /steamgriddb, not the bare service url: the proxy *is* the API under
        # that prefix, and the source cannot tell the difference — which is
        # the whole point of it being a reverse proxy. cmd-steam.sh passes the
        # same thing.
        # prefer_thumb: a tile is a couple of hundred pixels and there are ten
        # of them, so the full 600x900 is a download and a decode spent on
        # nothing.
        found.append(artwork.SteamGridDBSource(f"{url}/steamgriddb", token, [game.title], prefer_thumb=True))
    found.append(
        artwork.LibretroSource(
            base_url=os.environ.get("GOTG_LIBRETRO_URL") or libretro.DEFAULT_BASE_URL,
            playlists_file=artwork.DEFAULT_PLAYLISTS,
            platform=game.platform,
            name=game.title,
            game_id=game.id,
            # libretro answers with one name index per platform; without
            # somewhere to keep it that index is refetched for every game on
            # the page.
            cache_dir=names_cache,
        )
    )
    return found


def fetch_one(game: Game, url: str, token: str, names_cache: Path | None = None) -> bytes | None:
    """One picture, or None. Never raises: a grid that fell over because a
    picture would not download is worse than a grid of titles."""
    try:
        import artwork  # noqa: PLC0415

        facade = artwork.Artwork(sources=sources_for(game, url, token, names_cache))
        for kind in KINDS:
            found = facade.fetch(kind)
            if found:
                return found[0]
    except Exception:  # noqa: BLE001 — an unreachable source is not a crash
        return None
    return None


class Loader:
    """A worker that fills the cache behind the grid.

    One thread, a bounded queue and a set of what is already in flight. The
    frame loop only ever calls want() and done(), neither of which blocks.
    """

    def __init__(self, store: ArtStore, workers: int = 2):
        self.store = store
        self.url, self.token = service()
        # Beside the pictures, so one state directory holds the whole cache.
        self.names_cache = store.root.parent / "libretro"
        self.names_cache.mkdir(parents=True, exist_ok=True)
        self.queue: queue.Queue = queue.Queue()
        self.ready: queue.Queue = queue.Queue()
        self._seen: set[tuple[str, str]] = set()
        self._lock = threading.Lock()
        self.threads = [threading.Thread(target=self._work, daemon=True) for _ in range(workers)]
        for thread in self.threads:
            thread.start()

    def want(self, game: Game) -> Path | None:
        """The cached picture if there is one, and otherwise a request.

        Returns immediately either way. A game already asked about — cached,
        missed, or in flight — is never asked about twice.
        """
        cached = self.store.get(game)
        if cached is not None:
            return cached
        with self._lock:
            if game.key in self._seen or self.store.is_miss(game):
                return None
            self._seen.add(game.key)
        self.queue.put(game)
        return None

    def done(self) -> list[Game]:
        """Which games got a picture since last asked. Never blocks."""
        found = []
        while True:
            try:
                found.append(self.ready.get_nowait())
            except queue.Empty:
                return found

    def _work(self) -> None:
        while True:
            game = self.queue.get()
            if game is None:
                return
            body = fetch_one(game, self.url, self.token, self.names_cache)
            try:
                if body is None:
                    self.store.put_miss(game)
                else:
                    self.store.put(game, body)
                    self.ready.put(game)
            except (OSError, ValueError):
                # A full disk or a source that answered with an error page.
                # Neither is worth stopping the grid for.
                pass
