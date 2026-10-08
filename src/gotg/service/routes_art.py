"""`/art`: the fleet's tile pictures, over the handler.

A mixin of `Handler` in its own file: the cache itself is artcache.py (bytes
on disk, no HTTP); this is the other half, the part that decides who may read
or write, what a miss says, and when a 304 saves the body.
"""

from __future__ import annotations

import hashlib
import sys
import urllib.parse

from ..contract import ENTRY_ID_RE, PLATFORM_RE
from .artcache import CONTENT_TYPES, extension_for
from .routes_files import open_contained


class ArtRoutes:
    def _art(self, rest: str, body: bytes | None) -> None:
        """`/art` — every tile picture the fleet has resolved, held once.

            GET    /art                        the whole index, in one answer
            GET    /art/<platform>/<id>        the picture
            PUT    /art/<platform>/<id>        the warmer, with the index token
            PUT    /art/<platform>/<id>?miss=1 nothing has this one
            DELETE /art/<platform>/<id>        forget it, so a warm looks again

        Reading is open to any client token — the pictures are not secret and
        the whole point is that a laptop gets them from here instead of from
        an upstream. Writing is the index token's, for the same reason catalog
        writes are: a client that could put bytes here could put anything
        every other client then draws.
        """
        if self.art is None:
            self._problem(503, "this service holds no art cache")
            return
        parts = self._split(rest)
        if parts is None:
            return
        segments = [urllib.parse.unquote(segment) for segment in parts.path.strip("/").split("/") if segment]

        if not segments:
            if self.command not in ("GET", "HEAD"):
                self._problem(405, "the art index is a GET")
                return
            self._json(200, self.art.index())
            return
        if len(segments) != 2:
            self._problem(404, "a picture lives at /art/<platform>/<id>")
            return
        platform, game_id = segments
        # Checked here as well as in the cache: the cache raises, and a
        # traceback per malformed path is a worse answer than a 400.
        if not PLATFORM_RE.match(platform) or not ENTRY_ID_RE.match(game_id):
            self._problem(400, "that is not a platform and an entry id")
            return

        try:
            if self.command in ("GET", "HEAD"):
                self._art_get(platform, game_id)
            elif self.command == "PUT":
                miss = urllib.parse.parse_qs(parts.query).get("miss", ["0"])[0] not in ("0", "")
                self._art_put(platform, game_id, body, miss=miss)
            elif self.command == "DELETE":
                self._art_delete(platform, game_id)
            else:
                self._problem(405, f"{self.command} is not something /art answers")
        except ValueError as error:
            self._problem(400, str(error))
        except OSError as error:
            print(f"art cache error: {error}", file=sys.stderr)
            self._problem(500, "art cache error")

    def _art_get(self, platform: str, game_id: str) -> None:
        found = self.art.get(platform, game_id)
        if found is None:
            # The two kinds of nothing are worth telling apart: "miss" is an
            # answer — somebody looked and there is no art anywhere — and a
            # client that hears it stops asking. "absent" only means nobody
            # has warmed this one yet.
            miss = self.art.is_miss(platform, game_id)
            self._json(
                404,
                {"error": f"no art for {platform}/{game_id}", "miss": miss},
                {"X-Gotg-Art": "miss" if miss else "absent"},
            )
            return
        path, extension = found
        fd = open_contained(path, self.art.root)
        if fd is None:
            self._problem(404, f"no art for {platform}/{game_id}")
            return
        with open(fd, "rb", closefd=True) as handle:
            payload = handle.read()
        # A strong ETag over bytes this small costs microseconds and saves the
        # whole body on every prefetch after the first. The art itself never
        # changes in place — a new picture is a new warm — so it is also
        # immutable for as long as the client cares to keep it.
        etag = f'"{hashlib.sha256(payload).hexdigest()}"'
        headers = {"ETag": etag, "Cache-Control": "public, max-age=604800", "X-Gotg-Art": "hit"}
        if self.headers.get("If-None-Match") == etag:
            self._send(304, b"", CONTENT_TYPES[extension], headers)
            return
        self._send(200, payload, CONTENT_TYPES[extension], headers)

    def _art_put(self, platform: str, game_id: str, body: bytes | None, *, miss: bool) -> None:
        if not self._is_index():
            self._problem(403, "writing art needs the index token")
            return
        if miss:
            self.art.put_miss(platform, game_id)
            self._json(200, {"stored": f"{platform}/{game_id}", "miss": True})
            return
        if not body:
            self._problem(400, "a picture, or ?miss=1 to record that there is none")
            return
        if extension_for(body) is None:
            # From the bytes, never the Content-Type: the cache is served back
            # to every client's grid, so what goes in is a picture or nothing.
            self._problem(415, "that is not a png, jpeg or webp")
            return
        path = self.art.put(platform, game_id, body)
        self._json(200, {"stored": f"{platform}/{game_id}", "ext": path.suffix, "bytes": len(body)})

    def _art_delete(self, platform: str, game_id: str) -> None:
        if not self._is_index():
            self._problem(403, "forgetting art needs the index token")
            return
        dropped = self.art.forget(platform, game_id)
        self._json(200, {"forgot": f"{platform}/{game_id}", "had": dropped})
