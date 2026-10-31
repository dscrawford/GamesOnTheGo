"""`/ports`: portable builds a client pushed, and what the flake can build.

    GET    /ports                       the user's pushed builds
    GET    /ports/<platform>/<id>       the game's ports and variants, each with its build or null
    GET    /ports/<output>              one build's record
    GET    /ports/<output>/file         its bytes (resumable)
    PUT    /ports/<output>              push one (streamed: X-Gotg-Sha256, X-Gotg-Device)
    DELETE /ports/<output>              drop it

A build is the one body this service never holds in memory: it runs to
gigabytes, so the handler leaves it unread (app.py) and `put` streams it to disk.
"""

from __future__ import annotations

import os

from ..ports import OUTPUT_RE, Incomplete, TooLarge
from .routes_saves import clean_device


def ports_target(path: str) -> tuple[str, ...] | None:
    """`("list",)`, `("game", platform, id)`, `("build", output)` or `("file", output)`."""
    segments = [s for s in path.strip("/").split("/") if s]
    if not segments:
        return ("list",)
    if len(segments) == 1 and OUTPUT_RE.match(segments[0]):
        return ("build", segments[0])
    if len(segments) == 2 and segments[1] == "file" and OUTPUT_RE.match(segments[0]):
        return ("file", segments[0])
    if len(segments) == 2 and "." in segments[1] and "." not in segments[0]:
        return ("game", segments[0], segments[1])
    return None


class PortsRoutes:
    def _ports(self, rest: str, principal: str) -> None:
        upload = self.command == "PUT"
        if self.ports is None:
            self._problem(503, "this service holds no ports store", close=upload)
            return
        parts = self._split(rest)
        if parts is None:
            self.close_connection = True
            return
        target = ports_target(parts.path)
        if target is None:
            self._problem(404, f"nothing lives at /ports/{parts.path.strip('/')}", close=upload)
            return
        user = self._saves_user(principal)
        kind = target[0]
        allowed = {"list": ("GET", "HEAD"), "game": ("GET", "HEAD"), "file": ("GET", "HEAD")}.get(
            kind, ("GET", "HEAD", "PUT", "DELETE")
        )
        if self.command not in allowed:
            self._problem(405, f"{self.command} is not something /ports/{parts.path.strip('/')} answers", close=upload)
            return
        try:
            if kind == "list":
                self._json(200, {"version": 1, "ports": self.ports.listing(user)})
            elif kind == "game":
                self._json(200, {"version": 1, "available": self.ports.for_game(user, target[1], target[2])})
            elif kind == "file":
                self._ports_file(user, target[1])
            elif self.command == "PUT":
                self._ports_put(user, target[1])
            elif self.command == "DELETE":
                if self.ports.delete(user, target[1]):
                    self._json(200, {"deleted": target[1]})
                else:
                    self._problem(404, f"nothing has been pushed for {target[1]}")
            else:
                meta = self.ports.meta(user, target[1])
                if meta is None:
                    self._problem(404, f"nothing has been pushed for {target[1]}")
                else:
                    self._json(200, meta)
        except ValueError as error:
            self._problem(400, str(error), close=upload)
        except OSError as error:
            self._problem(500, str(error), close=upload)

    def _ports_put(self, user: str, output: str) -> None:
        try:
            length = int(self.headers.get("Content-Length") or -1)
        except ValueError:
            length = -1
        if length <= 0:
            self._problem(411, "a build arrives with a Content-Length and a body", close=True)
            return
        try:
            meta = self.ports.put(
                user,
                output,
                self.rfile.read,
                length,
                device=clean_device(self.headers.get("X-Gotg-Device", "")),
                sha256=self.headers.get("X-Gotg-Sha256", "").strip().lower(),
            )
        except TooLarge as error:
            self._problem(413, str(error), close=True)
            return
        except Incomplete as error:
            self._problem(400, str(error), close=True)
            return
        self._json(200, meta)

    def _ports_file(self, user: str, output: str) -> None:
        found = self.ports.build_path(user, output)
        if found is None:
            self._problem(404, f"nothing has been pushed for {output}")
            return
        path, meta = found
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            self._stream_fd(fd, f"gotg-{output}.run", meta["sha256"])
        finally:
            os.close(fd)
