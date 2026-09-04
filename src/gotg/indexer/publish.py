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

from ..contract import EXTRAS_PREFIX, LINK_HANDLERS, SHA256_RE, is_extra, valid_filename
from . import execute as ex
from . import plan as pl
from .slugify import title_slug

log = logging.getLogger("gotg.publish")

TIMEOUT = 30


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects: urllib's default copies the Authorization header onto
    the redirected request even cross-origin, which would hand the index token
    to whatever a Location header names — the same leak afd63b3 fixed in the
    proxy."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ARG002
        return None


_OPENER = urllib.request.build_opener(_NoRedirect())


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
            with _OPENER.open(request, timeout=TIMEOUT) as response:  # noqa: S310
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


def extras_prefix(op: pl.Op) -> str:
    """Where one update or DLC release's members live on the base entry.

    Named by role and version, so a re-released update of the same version
    replaces the earlier one rather than sitting beside it; a release naming
    no version falls back to its own name.
    """
    tail = op.version or title_slug(Path(op.src).name.rsplit(".", 1)[0]) or "release"
    return f"{EXTRAS_PREFIX}{op.role}_{tail}/"


def _kept_extras(stored: dict | None, *, dropping: str = "") -> list[dict]:
    """The extras a stored entry carries that still exist, minus one release."""
    if not stored:
        return []
    return [
        f
        for f in stored.get("files", [])
        if is_extra(f["name"]) and not (dropping and f["name"].startswith(dropping)) and Path(f["path"]).is_file()
    ]


def _members(op: pl.Op) -> list[_Member]:
    """The raw source files behind one op, named as the catalog will name them.

    A hardlinked single file travels under its canonical filename — the whole
    point of the catalog is that the id contract stops depending on renames.
    Everything else keeps its real names: a recipe needs the volume set or
    the archive exactly as released.
    """
    src = Path(op.src)
    if op.action == pl.ACTION_ATTACH:
        prefix = extras_prefix(op)
        files = [src] if src.is_file() else [f for f in sorted(src.iterdir()) if f.is_file()]
        return [_Member(prefix + f.name, f) for f in files]
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
        self.published_this_run: set[tuple[str, str]] = set()
        self.known: dict[tuple[str, str, str], dict] = {}
        # (platform, id) -> the whole stored entry: what an update attaches to,
        # and where a base being republished finds the extras it already has.
        self.rows: dict[tuple[str, str], dict] = {}
        for game in self.api.full_view().get("games", []):
            self.rows[(game["platform"], game["id"])] = game
            for row in game.get("files", []):
                self.known[(game["platform"], game["id"], row["name"])] = row

    def _digest(self, op: pl.Op, member: _Member, stat, checksum: bool) -> str | None:
        # The stored hash must exist to short-circuit: an entry published
        # under --allow-unhashed must acquire its hash on the next hashing
        # run, not match its way out of ever getting one.
        stored = self.known.get((op.platform, op.entry_id, member.name))
        if (
            stored
            and stored.get("sha256")
            and stored.get("path") == str(member.path)
            and stored.get("size_bytes") == stat.st_size
            and stored.get("mtime") == int(stat.st_mtime)
        ):
            return stored["sha256"]
        if not checksum:
            return None
        if op.action == pl.ACTION_HARDLINK:
            # The destination hash execute.py computed is this inode's hash —
            # but only if it still is this inode, and the sidecar is shaped
            # like a hash. Anything suspect falls through to hashing: this
            # value becomes the ETag resumes validate against, so a stale one
            # is worse than a slow one.
            sidecar = Path(op.dst + ".sha256")
            try:
                if sidecar.is_file():
                    text = sidecar.read_text().strip()
                    if SHA256_RE.fullmatch(text) and Path(op.dst).stat().st_ino == stat.st_ino:
                        return text
            except OSError:
                pass
        return ex.sha256_file(member.path)

    def publish(self, result: ex.Result, *, checksum: bool = True) -> None:
        """One executed op becomes one catalog entry. Raises PublishError."""
        op = result.op
        if op.action in (pl.ACTION_SKIP, pl.ACTION_MANUAL) or not result.ok or not op.entry_id:
            return

        try:
            members = _members(op)
        except OSError as error:
            # A torrent pruned between execute and publish: per-entry, like
            # every other publish failure, never the whole run's problem.
            raise PublishError(f"cannot enumerate {op.src!r}: {error}") from error

        files = []
        for member in members:
            if not valid_filename(member.name):
                raise PublishError(f"member name violates the contract: {member.name!r}")
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

        key = (op.platform, op.entry_id)
        stored = self.rows.get(key)
        if op.action == pl.ACTION_ATTACH:
            if stored is None:
                raise PublishError(f"{op.entry_id}: no base game in the catalog to attach this {op.role} to")
            payload = {
                "handler": stored["handler"],
                "title": stored["title"],
                "files": [f for f in stored["files"] if not is_extra(f["name"])]
                + _kept_extras(stored, dropping=extras_prefix(op))
                + files,
            }
        else:
            payload = {"handler": op.handler, "title": op.title or op.entry_id, "files": files + _kept_extras(stored)}

        self.api.put(op.platform, op.entry_id, payload)
        self.rows[key] = {"platform": op.platform, "id": op.entry_id, **payload}
        self.published_this_run.add(key)

    def sweep(self, since: str) -> None:
        report = self.api.sweep(since)
        for row in report.get("vanished", []):
            log.warning(
                "catalog entry not seen by this scan: %s/%s (last seen %s)",
                row.get("platform"),
                row.get("id"),
                row.get("seen_at"),
            )


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


def publish_games_root(publisher: Publisher, entries: dict, cfg) -> tuple[int, int]:
    """Publish rows for manifest entries whose bytes now live only in /Games.

    The torrent pass covers what still seeds; everything older survives as the
    hardlinks the importer made, so the tree itself is the raw source. The
    manifest gates the walk — keys and firmware never entered it, so they can
    never become catalog entries here. Returns (published, errors).
    """
    published = errors = 0
    for entry in entries.values():
        key = (entry.platform, entry.game_id)
        if key in publisher.published_this_run or entry.type != "file":
            continue
        local = cfg.games_root / Path(entry.path).relative_to(cfg.path_prefix)
        try:
            if not valid_filename(local.name):
                raise PublishError(f"member name violates the contract: {local.name!r}")
            st = local.stat()
            sha = entry.sha256
            if not sha and not publisher.allow_unhashed:
                sha = ex.sha256_file(local)
            publisher.api.put(
                entry.platform,
                entry.game_id,
                {
                    "handler": "single_file",
                    "title": entry.title or entry.game_id,
                    "files": [
                        {
                            "name": local.name,
                            "path": str(local),
                            "size_bytes": st.st_size,
                            "mtime": int(st.st_mtime),
                            "sha256": sha,
                        }
                    ]
                    + _kept_extras(publisher.rows.get(key)),
                },
            )
            published += 1
        except (OSError, PublishError) as error:
            log.error("games-root publish %s: %s", entry.game_id, error)
            errors += 1
    return published, errors
