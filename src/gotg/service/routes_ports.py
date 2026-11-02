"""`/ports`: portable builds a client pushed, and what the flake can build.

    GET    /ports                       the user's pushed builds
    GET    /ports/<platform>/<id>       the game's ports and variants, each with its build or null
    GET    /ports/<output>              one build's record
    GET    /ports/<output>/file         its bytes (resumable)
    PUT    /ports/<output>              push one (streamed: X-Gotg-Sha256, X-Gotg-Device)
    DELETE /ports/<output>              drop it

The browser build of a game, built on request by a client (`gotg port worker`):

    GET    /ports/requests              pending requests: what a worker should build
    GET    /ports/<output>/web          {build, request} -- what the player page polls
    POST   /ports/<output>/web          ask for a build (the live request if one is)
    PUT    /ports/<output>/web          push one (streamed, as above); ends the request
    GET    /ports/<output>/web/file     its bytes: one JavaScript file
    POST   /ports/<output>/web/claim    a worker takes the request (409 if it is not there to take)
    POST   /ports/<output>/web/fail     a worker gave up: {"error": "..."}
    DELETE /ports/<output>/web          drop the build

A build is the one body this service never holds in memory: it runs to
gigabytes, so the handler leaves it unread (app.py) and `put` streams it to disk.
"""

from __future__ import annotations

import json
import os

from ..ports import OUTPUT_RE, Incomplete, NotWebBuildable, TooLarge, web_builder
from .routes_saves import clean_device

# (method allowed, ...) per target kind.
ALLOWED = {
    "list": ("GET", "HEAD"),
    "requests": ("GET", "HEAD"),
    "game": ("GET", "HEAD"),
    "file": ("GET", "HEAD"),
    "build": ("GET", "HEAD", "PUT", "DELETE"),
    "web": ("GET", "HEAD", "POST", "PUT", "DELETE"),
    "web-file": ("GET", "HEAD"),
    "web-claim": ("POST",),
    "web-fail": ("POST",),
}


def ports_target(path: str) -> tuple[str, ...] | None:
    """`("list",)`, `("requests",)`, `("game", platform, id)`, `("build", output)`,
    `("file", output)`, or `("web" | "web-file" | "web-claim" | "web-fail", output)`."""
    segments = [s for s in path.strip("/").split("/") if s]
    if not segments:
        return ("list",)
    if segments == ["requests"]:
        return ("requests",)
    if segments[0:1] and OUTPUT_RE.match(segments[0]) and segments[1:2] == ["web"]:
        tail = segments[2:]
        if not tail:
            return ("web", segments[0])
        if len(tail) == 1 and tail[0] in ("file", "claim", "fail"):
            return (f"web-{tail[0]}", segments[0])
        return None
    if len(segments) == 1 and OUTPUT_RE.match(segments[0]):
        return ("build", segments[0])
    if len(segments) == 2 and segments[1] == "file" and OUTPUT_RE.match(segments[0]):
        return ("file", segments[0])
    if len(segments) == 2 and "." in segments[1] and "." not in segments[0]:
        return ("game", segments[0], segments[1])
    return None


class PortsRoutes:
    def _ports(self, rest: str, principal: str, body: bytes | None = None) -> None:
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
        if self.command not in ALLOWED[kind]:
            self._problem(405, f"{self.command} is not something /ports/{parts.path.strip('/')} answers", close=upload)
            return
        try:
            if kind == "list":
                self._json(200, {"version": 1, "ports": self.ports.listing(user)})
            elif kind == "requests":
                self._json(200, {"version": 1, "requests": self.ports.pending(user)})
            elif kind == "game":
                self._json(
                    200,
                    {
                        "version": 1,
                        "available": self.ports.for_game(user, target[1], target[2]),
                        "web": self.ports.web_for_game(user, target[1], target[2]),
                    },
                )
            elif kind == "file":
                self._ports_file(user, target[1])
            elif kind.startswith("web"):
                self._ports_web(kind, user, target[1], body)
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
        except NotWebBuildable as error:
            self._problem(404, str(error), close=upload)
        except ValueError as error:
            self._problem(400, str(error), close=upload)
        except OSError as error:
            self._problem(500, str(error), close=upload)

    def _ports_web(self, kind: str, user: str, output: str, body: bytes | None) -> None:
        ports = self.ports
        device = clean_device(self.headers.get("X-Gotg-Device", ""))
        if kind == "web-file":
            self._ports_file(user, output, "web")
        elif kind == "web-claim":
            claimed = ports.claim(user, output, device)
            if claimed is None:
                self._problem(409, f"no request for {output} is waiting to be built")
            else:
                self._json(200, claimed)
        elif kind == "web-fail":
            try:
                fields = json.loads(body or b"{}")
            except ValueError:
                fields = None
            if not isinstance(fields, dict) or not isinstance(fields.get("error", ""), str):
                self._problem(400, 'the body is {"error": "..."}')
                return
            failed = ports.fail(user, output, fields.get("error") or "the build failed", device)
            if failed is None:
                self._problem(409, f"no build of {output} is under way")
            else:
                self._json(200, failed)
        elif self.command == "PUT":
            self._ports_put(user, output, "web")
        elif self.command == "POST":
            self._json(200, {"version": 1, "request": ports.request_web(user, output)})
        elif self.command == "DELETE":
            if ports.delete(user, output, "web"):
                self._json(200, {"deleted": output})
            else:
                self._problem(404, f"no browser build of {output}")
        else:
            if web_builder(ports.web_dir, output) is None:
                raise NotWebBuildable(f"{output} has no browser build")
            self._json(
                200,
                {
                    "version": 1,
                    "output": output,
                    "build": ports.meta(user, output, "web"),
                    "request": ports.request_state(user, output),
                },
            )

    def _ports_put(self, user: str, output: str, kind: str = "run") -> None:
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
                kind=kind,
            )
        except TooLarge as error:
            self._problem(413, str(error), close=True)
            return
        except Incomplete as error:
            self._problem(400, str(error), close=True)
            return
        self._json(200, meta)

    def _ports_file(self, user: str, output: str, kind: str = "run") -> None:
        found = self.ports.build_path(user, output, kind)
        if found is None:
            self._problem(404, f"nothing has been pushed for {output}")
            return
        path, meta = found
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            self._stream_fd(fd, f"gotg-{output}.{'js' if kind == 'web' else 'run'}", meta["sha256"])
        finally:
            os.close(fd)
