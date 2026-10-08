"""`/catalog`: the handler's catalog routes, one function per route.

A mixin of `Handler`, in its own file because `_catalog` was a hundred and
thirty-seven lines: path parsing, the index-token gate, five routes and the
seven exceptions they can raise, in one `try`. The routes are now methods
(`_catalog_list`, `_catalog_put`, `_catalog_seen`, `_catalog_sweep`,
`_catalog_delete`), the two decisions that are not I/O are pure functions
(`touch_keys`, `advertised_hosts`), and `_catalog` is left holding what really
is shared: the path, the gate and the exception-to-status table.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from ..catalog import Conflict, SweepRefused
from ..contract import ENTRY_ID_RE, PLATFORM_RE


def touch_keys(payload: object) -> list[tuple[str, str]]:
    """The `(platform, id)` pairs of a `{"games": ["<platform>/<id>", ...]}`
    touch, or a ValueError saying which part is wrong (the handler answers 400
    with the message)."""
    games = payload.get("games") if isinstance(payload, dict) else None
    if not isinstance(games, list) or not all(isinstance(g, str) for g in games):
        raise ValueError('a touch is {"games": ["<platform>/<id>", ...]}')
    keys = []
    for game in games:
        platform, _, game_id = game.partition("/")
        if not PLATFORM_RE.match(platform) or not ENTRY_ID_RE.match(game_id):
            raise ValueError(f"not a platform and an entry id: {game}")
        keys.append((platform, game_id))
    return keys


def advertised_hosts(preferred: str, files_url: str) -> list[str]:
    """Byte hosts in order of preference, without repeats. `files_url` stays
    the one every client can reach, so an older client keeps working."""
    return list(dict.fromkeys(h for h in (preferred, files_url) if h))


class CatalogRoutes:
    def _files_url(self) -> str:
        """The byte host as it stands right now, or "" for none.

        With a port file configured, an unreadable or nonsense port means the
        tunnel is down — advertising a byte host that cannot answer would turn
        every download into a hang, so none is advertised and the client says
        which host it could not reach.
        """
        base = self.config.files_url
        if not base or not self.config.files_port_file:
            return base
        try:
            port = int(Path(self.config.files_port_file).read_text().strip())
        except (OSError, ValueError):
            print(f"files_url withheld: no forwarded port at {self.config.files_port_file}", file=sys.stderr)
            return ""
        if not 0 < port < 65536:
            print(f"files_url withheld: nonsense forwarded port {port}", file=sys.stderr)
            return ""
        return f"{base}:{port}"

    def _catalog(self, rest: str, body: bytes | None) -> None:
        """`/catalog` reads for everyone; writes for the index principal only.

        PUT upserts one entry, and answers 409 when the stored entry points at
        different bytes — two torrents producing the same id is a real error
        the old hardlink collision used to surface, and an upsert must not
        swallow it. The sweep reports what a completed scan did not confirm;
        it deletes nothing.
        """
        if self.catalog is None:
            self._problem(503, "this service holds no catalog")
            return

        parts = self._split(rest)
        if parts is None:
            return
        segments = [s for s in parts.path.strip("/").split("/") if s]
        query = parts.query.split("&")

        try:
            if self.command == "GET" and not segments:
                self._catalog_list(query)
            elif self.command == "PUT" and len(segments) == 2:
                self._catalog_put(segments[0], segments[1], body, query)
            elif self.command == "POST" and segments == ["seen"]:
                self._catalog_seen(body)
            elif self.command == "POST" and segments == ["sweep"]:
                self._catalog_sweep(body, query)
            elif self.command == "DELETE" and len(segments) == 2:
                self._catalog_delete(segments[0], segments[1])
            else:
                self._problem(404, f"nothing lives at /catalog/{parts.path.strip('/')}")
        except Conflict as conflict:
            self._json(409, {"error": str(conflict), "stored": conflict.stored})
        except SweepRefused as refused:
            self._problem(409, str(refused))
        # JSONDecodeError is a ValueError; RecursionError is what a deeply
        # nested body raises inside json.loads, and it must earn a 400 rather
        # than a thread traceback.
        except (ValueError, RecursionError) as error:
            message = "malformed body" if isinstance(error, RecursionError) else str(error)
            self._problem(400, message)
        # Neither message reaches the client: a storage error string typically
        # embeds server paths.
        except sqlite3.Error as error:
            print(f"catalog storage error: {error}", file=sys.stderr)
            self._problem(500, "catalog storage error")
        except OSError as error:
            print(f"catalog error: {error}", file=sys.stderr)
            self._problem(500, "catalog storage error")

    def _catalog_needs_index(self) -> bool:
        """Whether a write may proceed; if not, the refusal has been sent."""
        if not self.config.index_token:
            self._problem(503, "no index token is configured; the catalog is read-only")
            return False
        if not self._is_index():
            self._problem(403, "catalog writes need the index token")
            return False
        return True

    def _catalog_list(self, query: list[str]) -> None:
        full = "full=1" in query
        if full and not self._is_index():
            self._problem(403, "the full catalog view needs the index token")
            return
        # Server paths for the pod that has the library mounted; a
        # client's view never carries them.
        paths = "paths=1" in query
        if paths and not (self._is_index() or self._principal() == "library"):
            self._problem(403, "server paths need the library token")
            return
        view = self.catalog.view(full=full, paths=paths)
        # The catalog names its own byte host, so clients need no
        # files configuration — and a moving host (a VPN-fronted one
        # changes address on reconnect) costs a re-read, not a rewrite.
        files_url = self._files_url()
        if files_url:
            view["files_url"] = files_url
        hosts = advertised_hosts(self.config.files_preferred_url, files_url)
        if hosts:
            view["files_urls"] = hosts
        self._json(200, view)

    def _catalog_put(self, platform: str, game_id: str, body: bytes | None, query: list[str]) -> None:
        if not self._catalog_needs_index():
            return
        if not body:
            self._problem(400, "an entry must arrive as the request body")
            return
        entry = self.catalog.upsert(platform, game_id, json.loads(body), force="force=1" in query)
        self._json(200, entry)

    def _catalog_seen(self, body: bytes | None) -> None:
        if not self._catalog_needs_index():
            return
        # An import that changed nothing still has to say it looked:
        # seen_at is what the sweep reads to decide a game has
        # vanished. Saying it for thousands of entries in one request
        # is the difference between an import that takes a minute and
        # one that takes many.
        keys = touch_keys(json.loads(body) if body else {})
        seen = self.catalog.touch(keys)
        self._json(200, {"seen": seen, "asked": len(keys)})

    def _catalog_sweep(self, body: bytes | None, query: list[str]) -> None:
        if not self._catalog_needs_index():
            return
        payload = json.loads(body) if body else {}
        if not isinstance(payload, dict):
            self._problem(400, "the sweep body must be a JSON object")
            return
        report = self.catalog.sweep(str(payload.get("since", "")), confirm="confirm=1" in query)
        self._json(200, report)

    def _catalog_delete(self, platform: str, game_id: str) -> None:
        if not self._catalog_needs_index():
            return
        if self.catalog.delete(platform, game_id):
            self._send(200, b'{"deleted":true}', "application/json")
        else:
            self._problem(404, f"no entry {platform}/{game_id}")
