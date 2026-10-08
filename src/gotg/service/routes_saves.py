"""`/saves`: the handler's saves routes, one function per decision.

A mixin of `Handler` rather than a module of functions because every step
answers through the handler's `_json`, `_problem` and `_send`, and reads its
`store`, `config` and `headers`; and its own file because `_saves` alone was
eighty-six lines of path parsing, method gating and five different replies in
one `try`. Now the path is a pure function (`saves_target`), the method gate
is one place, and each reply -- history, one generation, the head's metadata,
the head's bytes, a PUT -- is a method a test can aim at.
"""

from __future__ import annotations

from pathlib import Path

from ..tokens import default_user

# What a /saves/<attr>/... path asks for.
HEAD, META, HISTORY, GENERATION = "head", "meta", "history", "gen"


def saves_target(path: str) -> tuple[str, str, str] | None:
    """`(attr, what, generation)` for a /saves path (without its prefix), or
    None when nothing lives there.

    `generation` is the digits as written ("" unless `what` is GENERATION): the
    404 for a generation that is not kept names it as the caller wrote it.
    Digits only, so "-1", "1.5" and a bundle's own name never parse.
    """
    segments = path.strip("/").split("/")
    rest = segments[1:]
    if rest == []:
        return segments[0], HEAD, ""
    if rest == ["meta"]:
        return segments[0], META, ""
    if rest == ["history"]:
        return segments[0], HISTORY, ""
    if len(segments) == 3 and segments[1] == "gen" and segments[2].isascii() and segments[2].isdigit():
        return segments[0], GENERATION, segments[2]
    return None


def clean_device(raw: str) -> str:
    """The X-Gotg-Device header as it is stored and echoed back in metadata, so
    it is made printable and short rather than trusted."""
    return "".join(c for c in raw if c.isprintable())[:32]


class SavesRoutes:
    def _saves_user(self, principal: str) -> str:
        """The namespace a principal's saves live in.

        From authentication, never from the path: /saves/<attr> stays the
        whole wire surface, so no request can name another user's saves.
        Multiple devices — daniel-desktop, daniel-deck — share one user, which
        is the whole point of the column.
        """
        if principal == "legacy":
            return self.config.legacy_user
        if principal == "indexer":
            return "indexer"
        if self.token_store is not None:
            user = self.token_store.user_for(principal)
            if user:
                return user
        # An introspected principal on a storeless pod never reaches here:
        # saves 503 without a store. The remaining case is a token revoked
        # between authentication and now; its own namespace beats a guess.
        return default_user(principal)

    def _saves(self, rest: str, body: bytes | None, user: str) -> None:
        """`/saves/<attr>` is the head: PUT is `.save()`, GET is `.retrieve()`,
        and `/saves/<attr>/meta` says what is current without moving the
        bytes. Conflicts are answered here — a PUT carries the hash of the
        generation it descends from, and a parent that is not the head is a
        409 carrying what the head actually is. `/saves/<attr>/history` lists
        the kept generations and `/saves/<attr>/gen/<n>` is one of them, for a
        person picking a save to go back to."""
        if self.store is None:
            self._problem(503, "this service holds no saves store")
            return

        parts = self._split(rest)
        if parts is None:
            return
        target = saves_target(parts.path)
        if target is None:
            self._problem(404, f"nothing lives at /saves/{parts.path.strip('/')}")
            return
        attr, what, generation = target
        if what in (HISTORY, GENERATION) and self.command != "GET":
            self._problem(405, f"{self.command} is not something saves history answers")
            return
        if what == META and self.command != "GET":
            # Read-only: a PUT here used to fall through to the head's and
            # store the body as a generation.
            self._problem(405, f"{self.command} is not something the save's meta answers")
            return

        try:
            if what == HISTORY:
                self._saves_history(user, attr)
            elif what == GENERATION:
                self._saves_generation(user, attr, generation)
            elif self.command == "GET" and what == META:
                self._saves_meta(user, attr)
            elif self.command == "GET":
                self._saves_head(user, attr)
            elif self.command == "PUT":
                self._saves_put(user, attr, body, parts.query)
            else:
                self._problem(405, f"{self.command} is not something the saves store answers")
        except ValueError as error:
            self._problem(400, str(error))
        except OSError as error:
            self._problem(500, str(error))

    def _saves_history(self, user: str, attr: str) -> None:
        self._json(200, {"generations": self.store.history(user, attr)})

    def _saves_generation(self, user: str, attr: str, generation: str) -> None:
        found = self.store.generation_path(user, attr, int(generation))
        if found is None:
            self._problem(404, f"generation {generation} of {attr} is not kept")
            return
        path, record = found
        self._send_bundle(path, record)

    def _saves_meta(self, user: str, attr: str) -> None:
        meta = self.store.meta(user, attr)
        if meta is None:
            self._problem(404, f"nothing has been pushed for {attr}")
            return
        self._json(200, meta)

    def _saves_head(self, user: str, attr: str) -> None:
        meta = self.store.meta(user, attr)
        path = self.store.bundle_path(user, attr)
        if meta is None or path is None:
            self._problem(404, f"nothing has been pushed for {attr}")
            return
        self._send_bundle(path, meta)

    def _saves_put(self, user: str, attr: str, body: bytes | None, query: str) -> None:
        if not body:
            self._problem(400, "a save must arrive with its bundle as the body")
            return
        published = self.store.save(
            user,
            attr,
            body,
            parent=self.headers.get("X-Gotg-Parent", ""),
            device=clean_device(self.headers.get("X-Gotg-Device", "")),
            force="force=1" in query.split("&"),
        )
        self._json(published.status, published.meta)

    def _send_bundle(self, path: Path, record: dict) -> None:
        """A saved bundle's bytes, labelled with the generation and hash they are.

        The head (`GET /saves/<attr>`) and a kept generation (`/gen/<n>`) each
        wrote these five headers by hand; the two replies are now one shape.
        """
        self._send(
            200,
            path.read_bytes(),
            "application/zstd",
            {"X-Gotg-Generation": str(record["generation"]), "X-Gotg-Hash": record["hash"]},
        )
