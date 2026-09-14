"""The loader's fence behaviour: a name the cache refuses must cost one tile
its picture, never the grid its life.

Only the non-network half is tested here; the sources themselves are the
client's, covered from its side.
"""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from gotg_ui.art import ArtStore
from gotg_ui.catalog import Game
from gotg_ui.fetch import LATER, Loader, fetch_one, service_art

PICTURE = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


@pytest.fixture
def loader(tmp_path, monkeypatch):
    # No api.json anywhere: url/token come up empty, which is the machine
    # that never logged in — sources still construct, nothing is fetched here.
    monkeypatch.setenv("GOTG_API_FILE", str(tmp_path / "absent.json"))
    return Loader(ArtStore(tmp_path / "art"), workers=0)


def test_a_hostile_name_is_no_art_not_a_crash(loader):
    # This exact shape took the whole grid down from the frame loop once: the
    # cache's ValueError fence rode up through want() on a catalog row the
    # fence disliked. The fence must hold — no path built — quietly.
    bad = Game(id="../escape", platform="gba", title="t", handler="single_file")
    assert loader.want(bad) is None
    assert loader.want(bad) is None  # and again every frame, still quietly


def test_a_real_long_id_is_wanted_normally(loader):
    g = Game(id="jpn." + "x" * 100, platform="gba", title="t", handler="single_file")
    assert loader.want(g) is None  # queued for fetch, no picture yet
    assert g.key in loader._seen, "requested, not rejected"


# --- the service's art cache, which is now the first place asked -------------


class _Art(BaseHTTPRequestHandler):
    """Enough of /art to answer the three things that matter: here it is,
    nobody has it, and nobody has looked."""

    index: dict = {}
    pictures: dict = {}
    seen: list = []

    def log_message(self, format, *args):  # noqa: A002
        pass

    def _send(self, code, body, kind, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        _Art.seen.append(self.path)
        if self.path == "/art":
            self._send(200, json.dumps(_Art.index).encode(), "application/json")
            return
        key = self.path[len("/art/") :]
        if key in _Art.pictures:
            self._send(200, _Art.pictures[key], "image/png")
            return
        miss = key in _Art.index.get("misses", [])
        self._send(404, b"{}", "application/json", {"X-Gotg-Art": "miss" if miss else "absent"})


@pytest.fixture
def art_service(tmp_path, monkeypatch):
    _Art.index = {"version": 1, "art": {}, "misses": []}
    _Art.pictures = {}
    _Art.seen = []
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = HTTPServer(("127.0.0.1", port), _Art)
    threading.Thread(target=lambda: server.serve_forever(poll_interval=0.02), daemon=True).start()
    config = tmp_path / "api.json"
    config.write_text(json.dumps({"url": f"http://127.0.0.1:{port}", "token": "client-token"}))
    monkeypatch.setenv("GOTG_API_FILE", str(config))
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


def test_the_service_answer_is_the_picture(art_service):
    _Art.pictures["n64/usa.zelda"] = PICTURE
    game = Game(id="usa.zelda", platform="n64", title="Zelda", handler="single_file")
    assert service_art(game, art_service, "client-token") == (PICTURE, True)


def test_a_recorded_miss_is_an_answer(art_service):
    _Art.index["misses"] = ["switch/usa.some_game"]
    game = Game(id="usa.some_game", platform="switch", title="Some Game", handler="single_file")
    assert service_art(game, art_service, "client-token") == (None, True)


def test_never_looked_at_is_not_an_answer(art_service):
    game = Game(id="usa.other_game", platform="switch", title="Other", handler="single_file")
    assert service_art(game, art_service, "client-token") == (None, False)


def test_a_sweep_never_reaches_an_upstream(art_service, monkeypatch):
    # The whole point of the cache: a background pass over five thousand games
    # must not become five thousand SteamGridDB searches.
    def explode(*args, **kwargs):
        raise AssertionError("the upstreams were asked during a prefetch")

    monkeypatch.setattr("gotg_ui.fetch.fetch_upstream", explode)
    game = Game(id="usa.other_game", platform="switch", title="Other", handler="single_file")
    assert fetch_one(game, art_service, "client-token", upstream=False) == (None, False)


def test_a_tile_on_screen_may_still_ask_an_upstream(art_service, monkeypatch):
    monkeypatch.setattr("gotg_ui.fetch.fetch_upstream", lambda *a, **k: (PICTURE, True))
    game = Game(id="usa.other_game", platform="switch", title="Other", handler="single_file")
    assert fetch_one(game, art_service, "client-token", upstream=True) == (PICTURE, True)


def test_prefetch_queues_what_the_service_holds(art_service, tmp_path):
    _Art.index["art"] = {"n64/usa.zelda": {"ext": ".png", "bytes": 4}}
    _Art.pictures["n64/usa.zelda"] = PICTURE
    games = [
        Game(id="usa.zelda", platform="n64", title="Zelda", handler="single_file"),
        Game(id="usa.some_game", platform="switch", title="Some", handler="single_file"),
    ]
    loader = Loader(ArtStore(tmp_path / "art"), workers=0)
    loader._prefetch(games)
    queued = [loader.queue.get_nowait() for _ in range(loader.queue.qsize())]
    assert [item[2].key for item in queued] == [("n64", "usa.zelda")]
    assert queued[0][0] == LATER, "a sweep waits behind anything on screen"
    assert queued[0][3] is False, "and asks nobody but us"


def test_a_tile_on_screen_jumps_the_sweep(art_service, tmp_path):
    _Art.index["art"] = {f"n64/usa.game_{n}": {"ext": ".png", "bytes": 4} for n in range(3)}
    games = [Game(id=f"usa.game_{n}", platform="n64", title="G", handler="single_file") for n in range(3)]
    loader = Loader(ArtStore(tmp_path / "art"), workers=0)
    loader._prefetch(games)
    urgent = Game(id="usa.on_screen", platform="n64", title="Now", handler="single_file")
    loader.want(urgent)
    assert loader.queue.get_nowait()[2].key == urgent.key


def test_a_service_with_no_art_cache_prefetches_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("GOTG_API_FILE", str(tmp_path / "absent.json"))
    loader = Loader(ArtStore(tmp_path / "art"), workers=0)
    loader._prefetch([Game(id="usa.zelda", platform="n64", title="Z", handler="single_file")])
    assert loader.queue.qsize() == 0


def test_a_throttled_upstream_is_not_a_local_miss(art_service, monkeypatch, tmp_path):
    # A 429 has not said "nobody has art for this game", and writing that down
    # would blank the tile on this machine for good.
    class Throttled:
        def fetch(self, kind):
            return None

        def note(self):
            return {"skipped": "http 429"}

    monkeypatch.setattr("gotg_ui.fetch.sources_for", lambda *a, **k: [Throttled()])
    game = Game(id="usa.other_game", platform="switch", title="Other", handler="single_file")
    assert fetch_one(game, art_service, "client-token") == (None, False)
