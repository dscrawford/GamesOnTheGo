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

from gotg_ui import fetch
from gotg_ui.art import ArtStore
from gotg_ui.catalog import Game
from gotg_ui.fetch import Loader, fetch_one, service_art

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
        # gotg.dcraw.net is Cloudflare-proxied, and Cloudflare answers the
        # default Python-urllib/3.x with a 403 whatever the token says. A mock
        # that answered anything would never have caught it — and it did not:
        # every tile was silently blank until this was put here.
        if self.headers.get("User-Agent", "").startswith("Python-urllib"):
            self._send(403, b"error code: 1010\n", "text/plain")
            return
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


def test_the_grid_asks_nobody_but_the_service(art_service):
    # No SteamGridDB, no libretro, not even through the proxy: the service is
    # the source of truth, and a game it has not been warmed for simply draws
    # its title until a warm run settles it.
    assert not hasattr(fetch, "sources_for"), "the upstream sources belong to the warmer now"
    assert not hasattr(fetch, "fetch_upstream")
    game = Game(id="usa.other_game", platform="switch", title="Other", handler="single_file")
    assert fetch_one(game, art_service, "client-token") == (None, False)
    assert _Art.seen == ["/art/switch/usa.other_game"]


def test_the_grid_identifies_itself(art_service):
    # Not cosmetic: without a User-Agent the edge answers 403, which is not an
    # answer, so the tile stays blank and nothing says why.
    game = Game(id="usa.zelda", platform="n64", title="Zelda", handler="single_file")
    _Art.pictures["n64/usa.zelda"] = PICTURE
    assert service_art(game, art_service, "client-token") == (PICTURE, True)
