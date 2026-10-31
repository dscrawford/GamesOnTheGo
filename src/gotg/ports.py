"""The ports store: portable builds of a library's games, one per output, per user.

A client with Nix builds a game's output as one self-running file
(`<library>#<output>.gotgPortable`, nix/portable.nix) and pushes it here; a
machine without Nix downloads it from the web app. The store keeps the newest
build of each output and nothing else -- a build is reproducible from the flake,
so history would be gigabytes of nothing.

Per user, like the saves: an uploaded file is a program somebody else would
run, so one person's upload is never another person's download.

    <root>/daniel/n64.usa.donkey_kong_64/current.json
    <root>/daniel/n64.usa.donkey_kong_64/build-3f9a1c2b4d5e.run

What a game *can* be built as is the flake's, not the store's: `available()`
reads the environment files (src/client/env/games) the way lib/catalog.nix
does, so the web app lists exactly the ports and variants `nix run` has.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .contract import ENTRY_ID_RE, PLATFORM_RE, SHA256_RE, utc_now
from .saves import USER_RE

# platform.region.name[.variant]: a library output, and so a directory name.
OUTPUT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,15}\.[a-z]{3,5}\.[a-z0-9][a-z0-9_]*(\.[a-z0-9][a-z0-9_-]*)?$")
VARIANT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
BUILD_RE = re.compile(r"^build-[0-9a-f]{12}\.run$")

# A port with its own GL for a machine without one runs to a couple of gigabytes.
DEFAULT_MAX_BYTES = 8 * 1024**3
CHUNK = 1 << 20


class TooLarge(Exception):
    """A build over the store's cap."""


class Incomplete(Exception):
    """The body ended before its Content-Length, or did not hash to what was claimed."""


def output_name(platform: str, game_id: str, variant: str = "") -> str:
    return f"{platform}.{game_id}{'.' + variant if variant else ''}"


def _description(path: Path) -> str:
    """The file's header comment, to its first full stop: what the file says it is.

    The `gotg play ...` usage most variant headers end with is the client's
    spelling, not a description, so it is dropped."""
    lines = []
    for line in path.read_text(errors="replace").splitlines():
        if not line.startswith("#"):
            break
        text = line.lstrip("#").strip()
        if not text:
            break
        lines.append(text)
    text = " ".join(lines)
    text = re.sub(r"\s*—?\s*`gotg play [^`]*`", "", text).strip(" —")
    # A sentence ends where the next one starts with a capital, and not at the
    # abbreviation in a title: "Super Smash Bros. Melee" is one name.
    cut = re.search(r"(?<!Bros)(?<!Mr)(?<!Dr)(?<!vs)\.(\s+(?=[A-Z])|$)", text)
    text = text[: cut.start() + 1] if cut else text
    return text if len(text) <= 200 else text[:199].rstrip() + "…"


def available(games_dir: Path | None, platform: str, game_id: str) -> list[dict]:
    """The builds the flake has for a game beyond its platform's emulator.

    The game's own file (a native port, or settings of its own) is the default
    output, and then `emulate` puts it back on the emulator; each
    `<id>.<variant>.nix` beside it is a variant. Empty for a game the flake
    runs only on its platform's emulator -- or when there is no directory to read."""
    if games_dir is None or not (PLATFORM_RE.match(platform) and ENTRY_ID_RE.match(game_id)):
        return []
    directory = games_dir / platform
    if not directory.is_dir():
        return []
    out = []
    own = directory / f"{game_id}.nix"
    if own.is_file():
        out.append({"variant": "", "description": _description(own)})
        out.append({"variant": "emulate", "description": f"The {platform} emulator instead."})
    prefix = f"{game_id}."
    for path in sorted(directory.iterdir()):
        name = path.name
        if not (name.startswith(prefix) and name.endswith(".nix")) or path == own:
            continue
        variant = name[len(prefix) : -len(".nix")]
        if VARIANT_RE.match(variant) and variant != "emulate":
            out.append({"variant": variant, "description": _description(path)})
    for entry in out:
        entry["output"] = output_name(platform, game_id, entry["variant"])
    return out


@dataclass
class PortsStore:
    root: Path
    max_bytes: int = DEFAULT_MAX_BYTES
    games_dir: Path | None = None
    # Held only to swap a pointer: an upload streams to its own temporary file
    # first, so a slow one never blocks another.
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _dir(self, user: str, output: str) -> Path:
        if not USER_RE.match(user):
            raise ValueError(f"invalid user name: {user}")
        if not OUTPUT_RE.match(output):
            raise ValueError(f"not a library output: {output}")
        return self.root / user / output

    def meta(self, user: str, output: str) -> dict | None:
        try:
            loaded = json.loads((self._dir(user, output) / "current.json").read_text())
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as error:
            raise OSError(f"unreadable pointer for {output}: {error}") from error
        # Data on disk becomes a path only through the same shape a write gives it.
        if not (isinstance(loaded.get("file"), str) and BUILD_RE.match(loaded["file"])):
            raise OSError(f"the pointer for {output} names a file it should not: {loaded.get('file')!r}")
        return loaded

    def build_path(self, user: str, output: str) -> tuple[Path, dict] | None:
        meta = self.meta(user, output)
        if meta is None:
            return None
        path = self._dir(user, output) / meta["file"]
        return (path, meta) if path.is_file() else None

    def listing(self, user: str) -> list[dict]:
        if not USER_RE.match(user):
            raise ValueError(f"invalid user name: {user}")
        user_dir = self.root / user
        if not user_dir.is_dir():
            return []
        out = []
        for path in sorted(user_dir.iterdir()):
            if path.is_dir() and OUTPUT_RE.match(path.name):
                meta = self.meta(user, path.name)
                if meta is not None:
                    out.append(meta)
        return out

    def for_game(self, user: str, platform: str, game_id: str) -> list[dict]:
        """What the flake can build for a game, each with its pushed build or None."""
        return [
            {**entry, "build": self.meta(user, entry["output"])}
            for entry in available(self.games_dir, platform, game_id)
        ]

    def put(
        self,
        user: str,
        output: str,
        read: Callable[[int], bytes],
        length: int,
        *,
        device: str = "",
        sha256: str = "",
    ) -> dict:
        """Stream `length` bytes from `read` into the store and make them the build.

        The file is whole and hashed on disk before the pointer names it; a
        body cut short, or one that does not hash to the `sha256` the client
        claimed, leaves nothing behind."""
        if length > self.max_bytes:
            raise TooLarge(f"a build is at most {self.max_bytes} bytes")
        if sha256 and not SHA256_RE.match(sha256):
            raise ValueError("X-Gotg-Sha256 is not a sha256")
        directory = self._dir(user, output)
        directory.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        fd, tmp_name = tempfile.mkstemp(dir=directory, prefix=".upload-", suffix=".part")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as sink:
                remaining = length
                while remaining:
                    chunk = read(min(CHUNK, remaining))
                    if not chunk:
                        raise Incomplete(f"the body ended {remaining} bytes short")
                    digest.update(chunk)
                    sink.write(chunk)
                    remaining -= len(chunk)
                sink.flush()
                os.fsync(sink.fileno())
            hexdigest = digest.hexdigest()
            if sha256 and sha256 != hexdigest:
                raise Incomplete(f"the body hashes to {hexdigest}, not the {sha256} it was sent as")
            os.chmod(tmp, 0o644)
            name = f"build-{hexdigest[:12]}.run"
            meta = {
                "version": 1,
                "output": output,
                "file": name,
                "sha256": hexdigest,
                "size": length,
                "device": device,
                "written_at": utc_now(),
            }
            with self._lock:
                tmp.replace(directory / name)
                pointer = directory / "current.json"
                part = pointer.with_suffix(".json.part")
                part.write_text(json.dumps(meta))
                part.replace(pointer)
                for old in directory.iterdir():
                    if BUILD_RE.match(old.name) and old.name != name:
                        old.unlink(missing_ok=True)
            return meta
        finally:
            tmp.unlink(missing_ok=True)

    def delete(self, user: str, output: str) -> bool:
        directory = self._dir(user, output)
        with self._lock:
            if self.meta(user, output) is None:
                return False
            (directory / "current.json").unlink()
            for old in directory.iterdir():
                if BUILD_RE.match(old.name):
                    old.unlink(missing_ok=True)
            return True
