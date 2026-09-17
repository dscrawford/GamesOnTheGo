"""Getting a tile picture, off the frame loop.

**The service is the only place this asks.** Every picture the fleet has is
in the GOTG service's art cache, put there by `gotg admin art warm`, and so
is every *absence*: a game nobody has art for is recorded as a miss, once,
for everybody. That is the design rather than a shortcut — one answer per
game, held in one place, instead of nine thousand games re-resolved on every
laptop that opens the picker. When the answer is wrong, it is fixed in the
one place too: `gotg admin art search` and `gotg admin art set` curate the
cache, and every grid picks the correction up.

Which means no upstream is reached from here at all: no SteamGridDB, no
libretro, no per-machine rate limit to respect, and a tile whose game has
never been warmed simply draws its title until the next warm reaches it.

Tiles fill in lazily, one at a time as they scroll into view — cheap now not
because the grid asks sooner but because of who it asks: a machine we run,
answering in milliseconds whether it has the picture or knows nobody does.
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import config
from .art import ArtStore
from .catalog import Game


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
SERVICE_TIMEOUT = int(config.get("theme.timeouts.service", 10))

# Identify ourselves, which is not cosmetic: gotg.dcraw.net is Cloudflare-
# proxied, and Cloudflare answers the default Python-urllib/3.x with a 403
# (error 1010) whatever the token says. The same bug cost a working
# SteamGridDB source once already — and here it is worse, because a 403 is
# not an answer, so every tile silently stayed blank.
USER_AGENT = "gotg-ui/0.1.0"


def _ask(url: str, token: str) -> urllib.request.Request:
    request = urllib.request.Request(url)
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("User-Agent", USER_AGENT)
    return request


def service_art(game: Game, url: str, token: str) -> tuple[bytes | None, bool]:
    """(the picture, whether that was an answer).

    (bytes, True)  the service had it
    (None,  True)  the service knows nobody has art for this game
    (None,  False) the service has not been asked to look yet, or could not
                   be reached — nobody's answer, so nothing is written down
    """
    if not (url and token):
        return None, False
    where = f"{urllib.parse.quote(game.platform)}/{urllib.parse.quote(game.id)}"
    request = _ask(f"{url}/art/{where}", token)
    try:
        with urllib.request.urlopen(request, timeout=SERVICE_TIMEOUT) as response:  # noqa: S310
            return response.read(), True
    except urllib.error.HTTPError as error:
        miss = error.headers.get("X-Gotg-Art") == "miss" if error.headers else False
        error.close()
        return None, miss
    except (urllib.error.URLError, OSError):
        return None, False


def fetch_one(game: Game, url: str, token: str) -> tuple[bytes | None, bool]:
    """One picture, and whether "none" was a real answer.

    Only the service is asked. "No" from it is the fleet's answer and is
    written down locally as well, so this machine does not ask again next
    launch; "not yet looked at" is nobody's answer and is left alone for a
    warm run to settle.
    """
    return service_art(game, url, token)


class Loader:
    """The workers that fill the cache behind the grid.

    Two threads, a queue and a set of what is already in flight. The frame
    loop only ever calls want() and done(), neither of which blocks.

    Lazily, per tile, as it always was: what makes that cheap is that the
    answer is already sitting in the service — a picture, or the fact that
    nobody has one — so a tile scrolling into view costs one request to a
    machine we run and nothing upstream at all.
    """

    def __init__(self, store: ArtStore, workers: int = 2):
        self.store = store
        self.url, self.token = service()
        self.queue: queue.Queue = queue.Queue()
        self.ready: queue.Queue = queue.Queue()
        self._seen: set[tuple[str, str]] = set()
        self._lock = threading.Lock()
        # Misses older than this launch are asked about once more. A second
        # back, so a miss written in the same tick as the start still counts.
        self.started = time.time() - 1
        self.threads = [threading.Thread(target=self._work, daemon=True) for _ in range(workers)]
        for thread in self.threads:
            thread.start()

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
            if game.key in self._seen or self.store.is_miss(game, since=self.started):
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
            body, answered = fetch_one(game, self.url, self.token)
            try:
                if body is not None:
                    self.store.put(game, body)
                    self.ready.put(game)
                elif answered:
                    self.store.put_miss(game)
                # And when nobody said no — a throttled or unreachable source,
                # rather than one that looked — nothing is written down. The
                # game stays in _seen, so it is not asked about again this
                # session: want() runs per tile per frame, and forgetting it
                # here would be sixty requests a second for as long as the
                # tile is on screen.
            except (OSError, ValueError):
                # A full disk or a source that answered with an error page.
                # Neither is worth stopping the grid for.
                pass
