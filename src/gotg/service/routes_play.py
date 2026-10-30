"""`/play`: the browser player's page, its EmulatorJS assets and its plan route.

The page and `/play/ejs/...` are answered before authentication, like the admin
page: they are static code that holds no secret (the token is typed into the page
and used for `/catalog`, `/games` and `/saves`, which authenticate as ever). Only
`/play/plan/<platform>/<id>` is credentialed.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import play_page
from .routes_files import open_contained


class PlayRoutes:
    def _serve_play_page(self, clean: str) -> bool:
        """The static half, before authentication. Whether it answered. Not on the admin listener."""
        if self.listener == "admin" or self.command not in ("GET", "HEAD"):
            return False
        top = clean.split("/")[0]
        if top != "play":
            return False
        if clean.startswith("play/ejs/"):
            self.close_connection = True
            self._play_asset(clean[len("play/ejs/") :])
            return True
        page = play_page.routes().get(clean)
        if page is None:
            return False  # /play/plan/... and anything else wants a token
        self.close_connection = True
        body, content_type = page
        self._send_play(body, content_type)
        return True

    def _send_play(self, body: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", play_page.POLICY)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _play_asset(self, name: str) -> None:
        """One file of the vendored EmulatorJS, from the directory the operator
        fetched it into (scripts/fetch-emulatorjs.sh). Served whole: the cores are
        ~1 MB compressed, and the browser caches them."""
        if self.ejs_dir is None:
            self._problem(404, "this service holds no EmulatorJS; see docs/browser-play.md")
            return
        if not play_page.EJS_PATH_RE.match(name) or ".." in name.split("/"):
            self._problem(404, "no such asset")
            return
        fd = open_contained(self.ejs_dir / name, self.ejs_dir)
        if fd is None:
            self._problem(404, "no such asset")
            return
        try:
            with os.fdopen(fd, "rb", closefd=False) as src:
                body = src.read()
        finally:
            os.close(fd)
        kind = play_page.EJS_TYPES.get(Path(name).suffix, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _play(self, rest: str) -> None:
        """`GET /play/plan/<platform>/<id>`: how this game is played."""
        parts = self._split(rest)
        if parts is None:
            return
        segments = [s for s in parts.path.strip("/").split("/") if s]
        if self.command != "GET" or len(segments) != 3 or segments[0] != "plan":
            self._problem(404, "ask /play/plan/<platform>/<id>")
            return
        plan = play_page.play_plan(segments[1], segments[2])
        if plan is None:
            self._problem(404, "not a platform and game id")
            return
        self._json(200, plan)
