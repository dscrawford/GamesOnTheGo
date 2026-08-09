#!/usr/bin/env python3
"""A stand-in for SteamGridDB, just enough of it to exercise artwork fetching.

The real API needs a key — an unauthenticated request is a 401, which is the
one thing about it that cannot be automated away — so this checks for the
bearer token too. Everything a test wants to arrange is a flag: no match, an
empty asset list, a refused key.

Endpoints, matching the v2 shapes:

    GET /api/v2/search/autocomplete/<term>   -> {"data": [{"id": .., "name": ..}]}
    GET /api/v2/<kind>/game/<id>             -> {"data": [{"url": .., "score": ..}]}
    GET /img/<name>                          -> the bytes of a fake image

Assets come back highest score first, because that ordering is what "the top
pick" means and a test should not have to know we sort.

Usage: mock_steamgriddb.py <port> [--no-match] [--no-assets] [--reject-key]
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

KINDS = ("grids", "heroes", "logos", "icons")


class Handler(BaseHTTPRequestHandler):
    no_match = False
    no_assets = False
    reject_key = False

    def log_message(self, format, *args):  # noqa: A002 — quiet under bats
        pass

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]

        # The real service sits behind Cloudflare, which rejects the default
        # python agent outright — a 403 no key can fix, and one a mock that
        # answered anything would never have caught.
        if self.headers.get("User-Agent", "").startswith("Python-urllib"):
            self._json(403, {"success": False, "errors": ["Forbidden"]})
            return

        # A fake image, served without auth the way a CDN would.
        if path.startswith("/img/"):
            body = b"\x89PNG\r\n\x1a\n" + path.encode()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        auth = self.headers.get("Authorization", "")
        if self.reject_key or not auth.startswith("Bearer "):
            self._json(401, {"success": False, "errors": ["Unauthorized"]})
            return

        if path.startswith("/api/v2/search/autocomplete/"):
            if self.no_match:
                self._json(200, {"success": True, "data": []})
            else:
                self._json(
                    200,
                    {"success": True, "data": [{"id": 42, "name": "The Top Match"}]},
                )
            return

        for kind in KINDS:
            if path.startswith(f"/api/v2/{kind}/game/"):
                if self.no_assets:
                    self._json(200, {"success": True, "data": []})
                else:
                    self._json(
                        200,
                        {
                            "success": True,
                            # Deliberately not in score order: the client is the
                            # one that has to pick the best, not the server.
                            "data": [
                                {"score": 1, "url": f"http://{self.headers['Host']}/img/{kind}-worse.png"},
                                {"score": 99, "url": f"http://{self.headers['Host']}/img/{kind}-best.png"},
                            ],
                        },
                    )
                return

        self._json(404, {"success": False})


def main() -> int:
    port = int(sys.argv[1])
    Handler.no_match = "--no-match" in sys.argv
    Handler.no_assets = "--no-assets" in sys.argv
    Handler.reject_key = "--reject-key" in sys.argv
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
