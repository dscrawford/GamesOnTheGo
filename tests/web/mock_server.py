"""A stdlib mock of the API contract at the top of static/js/api.js, plus the SPA's files.

    python tests/web/mock_server.py [port]      # then open http://127.0.0.1:<port>/ui/

Sign-in is a cookie: GET /auth/login sets it (`?as=admin` for an admin) and
redirects to `next`. In-memory state, reset per process.
"""

from __future__ import annotations

import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from gotg.service.routes_static import static_file

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c63f8cfc0f01f0005000201"
    "a5f645400000000049454e44ae426082"
)


def games():
    out = []
    for i in range(60):
        out.append(
            {
                "id": f"usa.game_{i:02d}",
                "platform": "n64",
                "handler": "mupen",
                "title": f"USA Game {i:02d}",
                "files": [{"name": f"game_{i}.z64", "size_bytes": 8388608, "sha256": "ab" * 32}],
            }
        )
    out += [
        {"id": "eur.zelda", "platform": "n64", "handler": "mupen", "title": "Zelda (EUR)", "files": []},
        {"id": "jpn.mario", "platform": "snes", "handler": "snes9x", "title": "Mario (JPN)", "files": []},
        {"id": "world.tetris", "platform": "gb", "handler": "sameboy", "title": "Tetris", "files": []},
    ]
    return out


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.gens = {
            "env-n64-usa_game_00": [
                {"generation": 1, "hash": "h1", "size": 1024, "device": "deck", "written_at": "2026-01-01T10:00:00Z"},
                {
                    "generation": 2,
                    "hash": "h2",
                    "size": 2048,
                    "device": "desktop",
                    "written_at": "2026-01-02T10:00:00Z",
                },
            ]
        }
        self.tokens = [
            {
                "name": "daniel-deck",
                "user": "daniel",
                "display": "",
                "created_at": 1700000000,
                "expires_at": None,
                "revoked_at": None,
                "last_used_at": 1700001000,
            },
            {
                "name": "old",
                "user": "guest",
                "display": "",
                "created_at": 1600000000,
                "expires_at": None,
                "revoked_at": 1650000000,
                "last_used_at": None,
            },
        ]
        self.invites = [{"name": "friend", "user": "guest", "created_at": 1700000000, "expires_at": 1900000000}]


class Handler(BaseHTTPRequestHandler):
    state = State()

    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", ctype="application/json", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj, extra=None):
        self._send(code, json.dumps(obj).encode(), extra=extra)

    def _session(self):
        m = re.search(r"gotg_session=(\w+)", self.headers.get("Cookie", ""))
        return m.group(1) if m else None

    def _route(self):
        parts = urlsplit(self.path)
        path, query = parts.path, parse_qs(parts.query)
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if path.startswith("/ui/"):
            found = static_file(path.lstrip("/"))
            if not found:
                return self._send(404, b"", "text/plain")
            return self._send(200, found[0], found[1])
        if path == "/auth/login":
            who = query.get("as", ["user"])[0]
            return self._send(
                302, extra={"Location": query.get("next", ["/ui/"])[0], "Set-Cookie": f"gotg_session={who}; Path=/"}
            )
        who = self._session()
        if who is None:
            return self._json(401, {"error": "sign in"})
        admin = who == "admin"
        if path == "/auth/whoami":
            return self._json(200, {"name": who, "user": who, "admin": admin})
        if path == "/saves":
            return self._json(200, {"version": 1, "saves": self._save_list()})
        if path == "/catalog":
            return self._json(200, {"version": 2, "games": games()})
        if path.startswith("/art/"):
            return self._send(200, PNG, "image/png") if "usa.game_0" in path else self._json(404, {"error": "none"})
        m = re.fullmatch(r"/saves/(env-[a-z0-9_-]+)(?:/(meta|history|gen/(\d+)))?", path)
        if m:
            return self._saves(m, query, body)
        if path.startswith("/admin/"):
            if not admin:
                return self._json(403, {"error": "admin only"})
            return self._admin(path, body)
        self._json(404, {"error": "nothing here"})

    def _save_list(self):
        out = []
        for attr, gens in sorted(self.state.gens.items()):
            head = gens[-1]
            out.append(
                {
                    "attr": attr,
                    "head": head,
                    "generations": len(gens),
                    "devices": sorted({g["device"] for g in gens}),
                    "overwritten_generations": [],
                    "has_conflict": False,
                    "sync": None,
                }
            )
        return out

    def _saves(self, m, query, body):
        attr, what, gen = m.groups()
        gens = self.state.gens.get(attr, [])
        head = gens[-1] if gens else None
        if self.command == "PUT":
            parent = self.headers.get("X-Gotg-Parent", "")
            if head and parent != head["hash"] and "force" not in query:
                return self._json(409, head)
            new = {
                "generation": len(gens) + 1,
                "hash": f"h{len(gens) + 1}",
                "size": len(body),
                "device": self.headers.get("X-Gotg-Device", ""),
                "written_at": "2026-02-01T00:00:00Z",
            }
            self.state.gens.setdefault(attr, []).append(new)
            return self._json(200, new)
        if not head:
            return self._json(404, {"error": "nothing has been pushed"})
        if what == "meta":
            return self._json(200, head)
        if what == "history":
            return self._json(200, {"generations": [{**g, "current": g is head} for g in reversed(gens)]})
        return self._send(200, b"bundle", "application/zstd")

    def _admin(self, path, body):
        s = self.state
        if path == "/admin/info":
            return self._json(200, {"public_url": "http://mock"})
        if path == "/admin/tokens" and self.command == "GET":
            return self._json(200, {"tokens": s.tokens})
        if path == "/admin/invites" and self.command == "GET":
            return self._json(200, {"invites": s.invites})
        if path == "/admin/invites":
            asked = json.loads(body)
            return self._json(200, {"name": asked["name"], "code": "gotgi_MOCKCODE"})
        if self.command == "DELETE" and path.startswith("/admin/tokens/"):
            name = path.rsplit("/", 1)[1]
            for t in s.tokens:
                if t["name"] == name and not t["revoked_at"]:
                    t["revoked_at"] = 1700002000
                    return self._json(200, {"revoked": name})
            return self._json(404, {"error": "nothing live"})
        self._json(404, {"error": "no"})

    do_GET = do_PUT = do_POST = do_DELETE = _route


def serve(port: int = 0) -> ThreadingHTTPServer:
    Handler.state = State()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


if __name__ == "__main__":
    server = serve(int(sys.argv[1]) if len(sys.argv) > 1 else 8099)
    print(f"http://127.0.0.1:{server.server_port}/ui/  (sign in: /auth/login?as=admin)")
    threading.Event().wait()
