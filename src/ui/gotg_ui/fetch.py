"""Getting a tile picture, off the frame loop.

**The service first, and usually only the service.** Every picture the fleet
has ever resolved lives in the GOTG service's own art cache, warmed by `gotg
admin art warm`, so the ordinary path for a tile is one request to a machine
we run — no SteamGridDB, no libretro, no per-laptop rate limit to respect.
The service also answers "nobody has art for this one", which is the answer
that matters most: most of a 5674-game library has no art anywhere, and
hearing that once is the difference between a grid that draws and a grid that
spends its first minute asking upstreams about games nothing has.

Because the index is one request, the grid does not have to be lazy about it
either: what the service holds is pulled in the background at startup, so
scrolling finds the pictures already on disk.

The upstream sources stay as a fallback, for a game imported since the last
warm and for a deployment with no art cache at all. They are the client's own
— src/client/steam/artwork.py, on PYTHONPATH from the wrapper — so the grid
asks SteamGridDB and libretro-thumbnails the same way `gotg steam art` does,
through the GOTG service that holds the real key. A second implementation
would be a second thing to keep in step with an API neither of us controls.

Imported lazily and behind a guard: the model half of this program has to run
in a venv that has neither the client nor pygame, which is how the tests run
at all.
"""

from __future__ import annotations

import itertools
import json
import os
import queue
import threading
import urllib.error
import urllib.parse
import urllib.request
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


# How long a request to our own service may take before the grid gives up on
# it. Short: this is a machine we run, and a tile is not worth a stall.
SERVICE_TIMEOUT = 10


def service_index(url: str, token: str) -> dict:
    """Everything the service has art for, in one request.

    {"art": {"n64/usa.zelda": {...}}, "misses": [...]}, or empty for a
    deployment that holds no art cache — which is not an error, just a fleet
    that has never been warmed.
    """
    if not (url and token):
        return {}
    request = urllib.request.Request(f"{url}/art")
    request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=SERVICE_TIMEOUT) as response:  # noqa: S310
            return json.loads(response.read())
    except (urllib.error.URLError, OSError, ValueError):
        return {}


def service_art(game: Game, url: str, token: str) -> tuple[bytes | None, bool]:
    """(the picture, whether that was an answer).

    (bytes, True)  the service had it
    (None,  True)  the service knows nobody has art for this game
    (None,  False) the service has not been asked to look yet, or is not
                   reachable — the caller may try the upstreams itself
    """
    if not (url and token):
        return None, False
    where = f"{urllib.parse.quote(game.platform)}/{urllib.parse.quote(game.id)}"
    request = urllib.request.Request(f"{url}/art/{where}")
    request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=SERVICE_TIMEOUT) as response:  # noqa: S310
            return response.read(), True
    except urllib.error.HTTPError as error:
        miss = error.headers.get("X-Gotg-Art") == "miss" if error.headers else False
        error.close()
        return None, miss
    except (urllib.error.URLError, OSError):
        return None, False


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


# What a source says when it did not get an answer, as opposed to getting the
# answer "no" — substrings of artwork.py's and libretro.py's own notes. The
# warmer keeps the same list for the same reason (steam/warm.py); neither can
# import the other, since one runs in the client's python and one in the UI's.
TRANSIENT = ("unreachable", "http 429", "http 500", "http 502", "http 504", "refused")


def fetch_upstream(game: Game, url: str, token: str, names_cache: Path | None = None) -> tuple[bytes | None, bool]:
    """(the picture, whether "none" was an answer). Never raises: a grid that
    fell over because a picture would not download is worse than a grid of
    titles.

    The second half keeps a bad minute from becoming a permanent blank. A
    throttled or unreachable source has not said "nobody has art for this",
    and writing that down locally would cost the tile its picture for good.
    """
    try:
        import artwork  # noqa: PLC0415

        sources = sources_for(game, url, token, names_cache)
        facade = artwork.Artwork(sources=sources)
        for kind in KINDS:
            found = facade.fetch(kind)
            if found:
                return found[0], True
        skipped = " ".join(str(source.note().get("skipped", "")) for source in sources)
        return None, not any(mark in skipped for mark in TRANSIENT)
    except Exception:  # noqa: BLE001 — an unreachable source is not a crash
        return None, False


def fetch_one(
    game: Game,
    url: str,
    token: str,
    names_cache: Path | None = None,
    *,
    upstream: bool = True,
) -> tuple[bytes | None, bool]:
    """One picture, and whether "none" was a real answer.

    The service is asked first and is usually the end of it. Only a game it
    has never been asked to look at reaches the upstreams, and only when the
    caller allows it — a background sweep of five thousand games must never
    turn into five thousand SteamGridDB searches, which is the entire thing
    the art cache exists to prevent.
    """
    body, answered = service_art(game, url, token)
    if body is not None:
        return body, True
    if answered:
        # The service looked and there is nothing. Written down locally too,
        # so this machine does not ask again next launch.
        return None, True
    if not upstream:
        return None, False
    return fetch_upstream(game, url, token, names_cache)


# What a queued request is for. Interactive work — a tile that is on screen
# now — jumps the whole prefetch sweep, which is thousands of entries long.
NOW, LATER = 0, 1


class Loader:
    """The workers that fill the cache behind the grid.

    Two threads, a priority queue and a set of what is already in flight. The
    frame loop only ever calls want() and done(), neither of which blocks.

    Startup asks the service for its whole index and queues everything in it
    that this machine does not have yet, at the low priority — so the grid
    stops being lazy without a scroll ever waiting on a download.
    """

    def __init__(self, store: ArtStore, workers: int = 2, *, prefetch: bool = True):
        self.store = store
        self.url, self.token = service()
        # Beside the pictures, so one state directory holds the whole cache.
        self.names_cache = store.root.parent / "libretro"
        self.names_cache.mkdir(parents=True, exist_ok=True)
        # (priority, sequence, game, may-ask-upstream). The sequence keeps the
        # queue from ever comparing two Games, which are not orderable, and
        # keeps each priority first-come-first-served.
        self.queue: queue.PriorityQueue = queue.PriorityQueue()
        self.ready: queue.Queue = queue.Queue()
        self._sequence = itertools.count()
        self._seen: set[tuple[str, str]] = set()
        self._lock = threading.Lock()
        # A lock of its own, because priming makes a request: the frame loop
        # takes _lock in want(), and a ten-second timeout held under that one
        # would be ten seconds of a frozen grid.
        self._prime_lock = threading.Lock()
        # What the service says it has. Empty until the priming thread
        # answers, and empty forever against a service with no art cache.
        self.index: dict = {}
        self.primed = threading.Event()
        self.prefetch_enabled = prefetch and os.environ.get("GOTG_ART_PREFETCH", "1") != "0"
        self.threads = [threading.Thread(target=self._work, daemon=True) for _ in range(workers)]
        for thread in self.threads:
            thread.start()

    # --- what the frame loop calls -----------------------------------------

    def want(self, game: Game) -> Path | None:
        """The cached picture if there is one, and otherwise a request.

        Returns immediately either way. A game already asked about — cached,
        missed, or in flight — is never asked about twice.
        """
        try:
            cached = self.store.get(game)
        except ValueError:
            # The cache's name fence refused it: no path was built, which is
            # the fence doing its job — but one row the fence dislikes must
            # cost one tile its picture, never the grid its life. It rode up
            # from here through the frame loop exactly once.
            return None
        if cached is not None:
            return cached
        with self._lock:
            if game.key in self._seen or self.store.is_miss(game):
                return None
            self._seen.add(game.key)
        self._put(NOW, game, upstream=True)
        return None

    def done(self) -> list[Game]:
        """Which games got a picture since last asked. Never blocks."""
        found = []
        while True:
            try:
                found.append(self.ready.get_nowait())
            except queue.Empty:
                return found

    def prefetch(self, games: list[Game]) -> None:
        """Pull everything the service already holds for this library.

        Off the frame loop and behind the index, so the grid is drawing long
        before this finishes — and it never touches an upstream, so a sweep of
        the whole catalog costs nothing but our own bandwidth.
        """
        if not self.prefetch_enabled:
            return
        threading.Thread(target=self._prefetch, args=(list(games),), daemon=True).start()

    # --- the workers -------------------------------------------------------

    def _put(self, priority: int, game: Game, *, upstream: bool) -> None:
        self.queue.put((priority, next(self._sequence), game, upstream))

    def _prime(self) -> dict:
        """The service's index, fetched once. Every caller waits on the same
        request rather than each making its own."""
        if not self.primed.is_set():
            with self._prime_lock:
                if not self.primed.is_set():
                    self.index = service_index(self.url, self.token)
                    self.primed.set()
        return self.index

    def _prefetch(self, games: list[Game]) -> None:
        index = self._prime()
        have = index.get("art", {})
        if not have:
            return
        for game in games:
            if f"{game.platform}/{game.id}" not in have:
                # Either the service has nothing, or it has a recorded miss.
                # Both are answers, and neither is worth a request.
                continue
            try:
                if self.store.get(game) is not None:
                    continue
            except ValueError:
                continue
            with self._lock:
                if game.key in self._seen:
                    continue
                self._seen.add(game.key)
            self._put(LATER, game, upstream=False)

    def _work(self) -> None:
        while True:
            _, _, game, upstream = self.queue.get()
            if game is None:
                return
            body, answered = fetch_one(game, self.url, self.token, self.names_cache, upstream=upstream)
            try:
                if body is not None:
                    self.store.put(game, body)
                    self.ready.put(game)
                elif answered:
                    self.store.put_miss(game)
                # And when nobody said no — the service has not looked at this
                # one yet — nothing is written down. The game stays in _seen,
                # so it is not asked about again this session: want() runs per
                # tile per frame, and forgetting it here would be sixty
                # requests a second for as long as the tile is on screen.
            except (OSError, ValueError):
                # A full disk or a source that answered with an error page.
                # Neither is worth stopping the grid for.
                pass
