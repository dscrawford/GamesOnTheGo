"""The saves store: generations of content-addressed bundles, per environment.

The clients used to decide conflicts among themselves — read the remote's
pointer, compare generations, upload, and hope nobody else published in
between. A dumb blob store cannot close that window, because WebDAV has no
compare-and-swap. This server is one process holding one lock, so it can:
a push carries the hash of the generation it descends from, and the store
either advances the head atomically or answers 409 with what is actually
there. That answer *is* the conflict model; the client's whole job is to
repeat it to a person.

What is stored is opaque here on purpose. A bundle is a tar of files relative
to an environment's state directory — no path from any machine — but the
server never opens it: verification and extraction belong to the machine whose
disk it lands on. The store knows sizes, hashes, generations and nothing else.

Layout, under one data directory, namespaced by the user the token belongs
to — the user arrives from authentication, never from the request path, so no
client can name another user's saves at all:

    <root>/daniel/env-n64/current.json
    <root>/daniel/env-n64/gen/000042-3f9a1c2b4d5e.tar.zst

The newest few generations are kept and the rest pruned — retention is the
server's job now, so every client stops needing delete rights on anything.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

# The same shapes the client validates. An attr becomes a directory name and a
# bundle name becomes a file name, so nothing that does not match is touched.
ATTR_RE = re.compile(r"^env-[a-z0-9][a-z0-9_-]*$")
# The token store's name shape. A user becomes a directory beside other
# users' directories, so nothing looser may pass; the leading class also
# keeps it from ever colliding with the dot-prefixed .tokens dir.
USER_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
GEN_RE = re.compile(r"^\d{6}-[0-9a-f]{12}\.tar\.zst$")

DEFAULT_KEEP = 3
DEFAULT_MAX_BYTES = 64 * 1024 * 1024


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class Publish:
    """What a save() decided: the meta that is now current, and whether the
    caller's bundle became it. `conflict` carries the head that was already
    there, which is everything a client needs to explain the refusal."""

    status: int
    meta: dict


@dataclass
class SavesStore:
    root: Path
    keep: int = DEFAULT_KEEP
    max_bytes: int = DEFAULT_MAX_BYTES
    # One lock for the whole store: a handful of machines pushing kilobytes do
    # not need finer grain, and one lock cannot deadlock with itself.
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # --- reading -------------------------------------------------------------

    def _attr_dir(self, user: str, attr: str) -> Path:
        if not USER_RE.match(user):
            raise ValueError(f"invalid user name: {user}")
        if not ATTR_RE.match(attr):
            raise ValueError(f"invalid environment name: {attr}")
        return self.root / user / attr

    def meta(self, user: str, attr: str) -> dict | None:
        """The current pointer, or None when nothing has been pushed."""
        path = self._attr_dir(user, attr) / "current.json"
        try:
            loaded = json.loads(path.read_text())
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as error:
            raise OSError(f"unreadable pointer for {attr}: {error}") from error
        bundle = loaded.get("bundle", "")
        # The pointer is data on disk, and disks get edited: nothing in it is
        # trusted to become a path without passing the same shape checks a
        # request would.
        if not (isinstance(bundle, str) and bundle.startswith("gen/") and GEN_RE.match(bundle[4:])):
            raise OSError(f"the pointer for {attr} names a bundle it should not: {bundle!r}")
        return loaded

    def bundle_path(self, user: str, attr: str) -> Path | None:
        """Where the current bundle's bytes are, or None when there are none."""
        meta = self.meta(user, attr)
        if meta is None:
            return None
        path = self._attr_dir(user, attr) / meta["bundle"]
        return path if path.is_file() else None

    # --- writing -------------------------------------------------------------

    def save(self, user: str, attr: str, body: bytes, parent: str, device: str, *, force: bool = False) -> Publish:
        """Advance the head, or refuse with what is there.

        The rules, in the order they are applied:
          * the same bytes again is a success that changes nothing — pushing
            twice must stay free;
          * a parent that is not the current head is a 409, unless forced —
            and even forced, the losing generation stays until retention ages
            it out;
          * the bundle is durable on disk before the pointer names it, so the
            worst an interruption can leave behind is an orphan file.
        """
        with self._lock:
            current = self.meta(user, attr)
            digest = hashlib.sha256(body).hexdigest()

            if current is not None and digest == current["hash"]:
                return Publish(200, current)
            if current is not None and not force and parent != current["hash"]:
                return Publish(409, current)

            generation = (current["generation"] if current is not None else 0) + 1
            name = f"{generation:06d}-{digest[:12]}.tar.zst"
            gen_dir = self._attr_dir(user, attr) / "gen"
            gen_dir.mkdir(parents=True, exist_ok=True)

            bundle = gen_dir / name
            tmp = bundle.with_suffix(".part")
            tmp.write_bytes(body)
            tmp.replace(bundle)

            meta = {
                "version": 1,
                "attr": attr,
                "generation": generation,
                "parent": current["hash"] if current is not None else "",
                "hash": digest,
                "bundle": f"gen/{name}",
                "size": len(body),
                "device": device,
                "written_at": _utc_now(),
            }
            pointer = self._attr_dir(user, attr) / "current.json"
            tmp = pointer.with_suffix(".part")
            tmp.write_text(json.dumps(meta))
            tmp.replace(pointer)

            self._prune(user, attr, keep_name=name)
            return Publish(200, meta)

    def _prune(self, user: str, attr: str, keep_name: str) -> None:
        """Drop all but the newest few generations.

        The one the pointer names is never deleted whatever the arithmetic
        says, and a file not shaped like a generation is not ours to delete.
        Zero-padded names make lexical order age order.
        """
        gen_dir = self._attr_dir(user, attr) / "gen"
        names = sorted(p.name for p in gen_dir.iterdir() if GEN_RE.match(p.name))
        for name in names[: max(0, len(names) - self.keep)]:
            if name != keep_name:
                (gen_dir / name).unlink(missing_ok=True)
