"""`/logs`: players' session logs, written by them and read only by the admin.

A mixin of `Handler` like the saves routes. The asymmetry is the point: any
token may PUT its own sessions (the user comes from authentication, never the
path), while listing, reading and deleting need the admin token, since a log
can hold whatever a game printed.
"""

from __future__ import annotations

from ..logs import TooLarge, valid_session_id
from .routes_saves import clean_device


class LogsRoutes:
    def _logs(self, rest: str, body: bytes | None, principal: str) -> None:
        if self.logs is None:
            self._problem(503, "this service holds no logs store")
            return
        parts = self._split(rest)
        if parts is None:
            return
        segments = [s for s in parts.path.strip("/").split("/") if s]
        try:
            if self.command == "PUT" and len(segments) == 1:
                self._logs_put(segments[0], body, self._saves_user(principal))
            elif self.command == "PUT":
                self._problem(404, "a session is put at /logs/<session-id>")
            elif self.command not in ("GET", "DELETE"):
                self._problem(405, f"{self.command} is not something the logs store answers")
            elif self._needs_admin(principal):
                return
            elif self.command == "GET" and not segments:
                self._logs_list()
            elif len(segments) == 2 and self.command == "GET":
                self._logs_get(*segments)
            elif len(segments) == 2:
                self._logs_delete(*segments)
            else:
                self._problem(404, f"nothing lives at /logs/{parts.path.strip('/')}")
        except ValueError as error:
            self._problem(400, str(error))
        except OSError as error:
            self._problem(500, str(error))

    def _logs_put(self, session_id: str, body: bytes | None, user: str) -> None:
        if not valid_session_id(session_id):
            self._problem(400, f"not a session id: {session_id!r}")
            return
        try:
            kept = self.logs.put(
                user,
                session_id,
                body or b"",
                attr=self.headers.get("X-Gotg-Attr", "")[:128],
                device=clean_device(self.headers.get("X-Gotg-Device", "")),
            )
        except TooLarge as error:
            self._problem(413, str(error))
            return
        self._json(200, {"kept": kept.bytes, "sessions": kept.sessions, "dropped": kept.dropped})

    def _logs_list(self) -> None:
        self._json(200, {"users": self.logs.list_all()})

    def _logs_get(self, user: str, session_id: str) -> None:
        found = self.logs.path(user, session_id)
        if found is None:
            self._problem(404, f"no session {session_id} for {user}")
            return
        self._send(200, found.read_bytes(), "application/zstd")

    def _logs_delete(self, user: str, session_id: str) -> None:
        if not self.logs.delete(user, session_id):
            self._problem(404, f"no session {session_id} for {user}")
            return
        self._json(200, {"deleted": session_id})
