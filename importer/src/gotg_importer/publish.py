"""Publishing catalog rows to the GOTG service — the indexer's new back half.

Where execute.py materializes files under /Games, this names the raw sources
in the catalog: every published entry points at the bytes the torrent already
has, plus the classifier's handler so the client knows what recipe turns them
into a runnable game. Both halves run side by side (dual-publish) until the
cutover; nothing here writes to any filesystem.

Hashing is the expensive pass, so the full catalog view is fetched once at
run start and a member whose (path, size, mtime) matches its stored row keeps
the stored hash. A hardlink entry gets its digest for free — execute.py
already hashed the destination, which is the same inode.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import execute as ex
from . import plan as pl

log = logging.getLogger("gotg.publish")

TIMEOUT = 30


class PublishError(Exception):
    """One entry could not be published — per-entry, never fatal to a run."""


class Collision(PublishError):
    """The service holds this id for different bytes — the same error the
    hardlink no-clobber used to surface, answered as a 409."""


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class CatalogAPI:
    def __init__(self, url: str, token: str):
        self.url = url.rstrip("/")
        self.token = token

    def _call(self, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
        request = urllib.request.Request(
            f"{self.url}{path}",
            data=json.dumps(payload).encode() if payload is not None else None,
            method=method,
        )
        request.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            try:
                body = json.loads(error.read() or b"{}")
            except json.JSONDecodeError:
                body = {}
            return error.code, body
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as error:
            raise PublishError(f"the catalog API is unreachable: {error}") from error

    def full_view(self) -> dict:
        status, body = self._call("GET", "/catalog?full=1")
        if status != 200:
            raise PublishError(f"could not fetch the catalog (HTTP {status})")
        return body

    def put(self, platform: str, game_id: str, payload: dict) -> dict:
        status, body = self._call("PUT", f"/catalog/{platform}/{game_id}", payload)
        if status == 409:
            raise Collision(f"{platform}/{game_id} already points at different bytes: {body.get('error', 'conflict')}")
        if status != 200:
            raise PublishError(f"PUT {platform}/{game_id} failed (HTTP {status}): {body.get('error', '')}")
        return body

    def sweep(self, since: str) -> dict:
        status, body = self._call("POST", "/catalog/sweep", {"since": since})
        if status == 409:
            # The rail: too much of the catalog would read as vanished. That
            # verdict belongs to a person, so it is reported, never confirmed
            # away automatically.
            raise PublishError(body.get("error", "sweep refused"))
        if status != 200:
            raise PublishError(f"sweep failed (HTTP {status}): {body.get('error', '')}")
        return body


@dataclass(frozen=True)
class _Member:
    name: str
    path: Path


def _members(op: pl.Op) -> list[_Member]:
    """The raw source files behind one op, named as the catalog will name them.

    A hardlinked single file travels under its canonical filename — the whole
    point of the catalog is that the id contract stops depending on renames.
    Everything else keeps its real names: a recipe needs the volume set or
    the archive exactly as released.
    """
    src = Path(op.src)
    if op.action == pl.ACTION_HARDLINK:
        return [_Member(Path(op.dst).name, src)]
    if op.action in (pl.ACTION_CONVERT,) or (op.action == pl.ACTION_EXTRACT and src.is_file()):
        return [_Member(src.name, src)]
    if op.action == pl.ACTION_EXTRACT:
        # A scene release: the volume set, the sfv beside it, and whatever
        # else shipped — the client's recipe picks what it needs.
        return [_Member(f.name, f) for f in sorted(src.iterdir()) if f.is_file()]
    if op.action == pl.ACTION_ARCHIVE:
        # A decrypted WiiU tree, fetched as a tree: names are relative paths.
        return [_Member(f.relative_to(src).as_posix(), f) for f in sorted(src.rglob("*")) if f.is_file()]
    return []


class Publisher:
    def __init__(self, api: CatalogAPI, *, allow_unhashed: bool = False):
        self.api = api
        self.allow_unhashed = allow_unhashed
        # (platform, id, name) -> stored file row, for the hash skip.
        self.known: dict[tuple[str, str, str], dict] = {}
        for game in self.api.full_view().get("games", []):
            for row in game.get("files", []):
                self.known[(game["platform"], game["id"], row["name"])] = row

    def _digest(self, op: pl.Op, member: _Member, stat, checksum: bool) -> str | None:
        stored = self.known.get((op.platform, op.entry_id, member.name))
        if (
            stored
            and stored.get("path") == str(member.path)
            and stored.get("size_bytes") == stat.st_size
            and stored.get("mtime") == int(stat.st_mtime)
        ):
            return stored.get("sha256")
        if not checksum:
            return None
        if op.action == pl.ACTION_HARDLINK:
            # The destination hash execute.py computed is this inode's hash.
            sidecar = Path(op.dst + ".sha256")
            if sidecar.is_file():
                return sidecar.read_text().strip() or None
        return ex.sha256_file(member.path)

    def publish(self, result: ex.Result, *, checksum: bool = True) -> None:
        """One executed op becomes one catalog entry. Raises PublishError."""
        op = result.op
        if op.action in (pl.ACTION_SKIP, pl.ACTION_MANUAL) or not result.ok or not op.entry_id:
            return

        files = []
        for member in _members(op):
            try:
                stat = member.path.stat()
            except OSError as error:
                raise PublishError(f"cannot stat {member.path}: {error}") from error
            digest = self._digest(op, member, stat, checksum)
            if digest is None and not self.allow_unhashed:
                raise PublishError(
                    f"no hash for {member.name} and --allow-unhashed is not set: "
                    "bytes served straight off the torrent tree deserve a verifier"
                )
            files.append(
                {
                    "name": member.name,
                    "path": str(member.path),
                    "size_bytes": stat.st_size,
                    "mtime": int(stat.st_mtime),
                    "sha256": digest,
                }
            )
        if not files:
            raise PublishError(f"{op.entry_id}: nothing to publish behind {op.src}")

        self.api.put(
            op.platform,
            op.entry_id,
            {"handler": op.handler, "title": op.title or op.entry_id, "files": files},
        )

    def sweep(self, since: str) -> None:
        report = self.api.sweep(since)
        for row in report.get("vanished", []):
            log.warning(
                "catalog entry not seen by this scan: %s/%s (last seen %s)",
                row.get("platform"),
                row.get("id"),
                row.get("seen_at"),
            )


LINK_HANDLERS = frozenset({"single_file", "no_intro_set"})


def diff_catalog(manifest_entries: dict, api: CatalogAPI) -> list[str]:
    """The Phase 3 gate: every manifest entry mapped in the catalog, or said why not.

    Equality only holds for the hardlink majority — a derived manifest entry
    (an RVZ, a WiiU zip) corresponds to a raw catalog entry whose recipe will
    produce the equivalent artifact, so those check id-presence and handler
    shape, not bytes.
    """
    catalog = {(game["platform"], game["id"]): game for game in api.full_view().get("games", [])}
    problems: list[str] = []

    seen = set()
    for entry in manifest_entries.values():
        key = (entry.platform, entry.game_id)
        seen.add(key)
        game = catalog.get(key)
        if game is None:
            problems.append(f"missing from catalog: {entry.platform}/{entry.game_id}")
            continue
        if game["handler"] not in LINK_HANDLERS:
            continue  # derived: the recipe replaces the byte comparison
        members = game.get("files", [])
        if len(members) != 1:
            problems.append(f"link entry with {len(members)} members: {entry.platform}/{entry.game_id}")
            continue
        member = members[0]
        if entry.sha256 and member.get("sha256") and entry.sha256 != member["sha256"]:
            problems.append(f"hash mismatch: {entry.platform}/{entry.game_id}")
        elif entry.size_bytes != member.get("size_bytes"):
            problems.append(f"size mismatch: {entry.platform}/{entry.game_id}")

    for key in sorted(set(catalog) - seen):
        problems.append(f"in catalog but not the manifest: {key[0]}/{key[1]}")
    return problems
