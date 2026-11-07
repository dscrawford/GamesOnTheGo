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
    <root>/daniel/n64.usa.donkey_kong_64/win.json             the Windows zip (.gotgWindows)
    <root>/daniel/n64.usa.donkey_kong_64/win-8b04e1d2c3a9.zip

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
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .contract import ENTRY_ID_RE, PLATFORM_RE, SHA256_RE, utc_now
from .saves import USER_RE

# platform.region.name[.variant]: a library output, and so a directory name.
OUTPUT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,15}\.[a-z]{3,5}\.[a-z0-9][a-z0-9_]*(\.[a-z0-9][a-z0-9_-]*)?$")
VARIANT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
BUILD_RE = re.compile(r"^build-[0-9a-f]{12}\.run$")
WIN_RE = re.compile(r"^win-[0-9a-f]{12}\.zip$")

# A port with its own GL for a machine without one runs to a couple of gigabytes.
DEFAULT_MAX_BYTES = 8 * 1024**3
# A Windows zip is a launcher and the emulator's own build: ares ~65 MB, Dolphin ~100+ MB.
DEFAULT_MAX_WIN_BYTES = 1024**3
CHUNK = 1 << 20


class TooLarge(Exception):
    """A build over the store's cap."""


class Incomplete(Exception):
    """The body ended before its Content-Length, or did not hash to what was claimed."""


class NotABundle(ValueError):
    """A Windows upload that is not a zip laid out as nix/windows/bundle.nix makes one."""


@dataclass(frozen=True)
class Kind:
    """One kind of build an output can have: its pointer, and the files it names."""

    name: str
    pointer: str
    file_re: re.Pattern
    prefix: str
    suffix: str


KINDS = {
    "run": Kind("run", "current.json", BUILD_RE, "build-", ".run"),
    "win": Kind("win", "win.json", WIN_RE, "win-", ".zip"),
}


def _kind(kind: str) -> Kind:
    try:
        return KINDS[kind]
    except KeyError:
        raise ValueError(f"not a kind of build: {kind!r}") from None


def check_bundle(path: Path, output: str) -> None:
    """Refuse anything but a zip whose one top directory is gotg-<output>/ with
    gotg.exe and spec.json in it. Reads the central directory, extracts nothing."""
    with path.open("rb") as f:
        if f.read(4) != b"PK\x03\x04":
            raise NotABundle("a Windows build is a zip, and this does not start like one")
    try:
        with zipfile.ZipFile(path) as bundle:
            names = [info.filename for info in bundle.infolist()]
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError) as error:
        raise NotABundle(f"the zip's directory is unreadable: {error}") from error
    top = f"gotg-{output}"
    for name in names:
        # Somebody unzips this on Windows: nothing may land outside the top directory.
        parts = name.split("/")
        if "\\" in name or ":" in name or ".." in parts or parts[0] != top:
            raise NotABundle(f"the zip holds {name!r}, outside {top}/")
    for need in ("gotg.exe", "spec.json"):
        if f"{top}/{need}" not in names:
            raise NotABundle(f"the zip has no {top}/{need}")


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
    max_win_bytes: int = DEFAULT_MAX_WIN_BYTES
    # Held only to swap a pointer: an upload streams to its own temporary file
    # first, so a slow one never blocks another.
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _dir(self, user: str, output: str) -> Path:
        if not USER_RE.match(user):
            raise ValueError(f"invalid user name: {user}")
        if not OUTPUT_RE.match(output):
            raise ValueError(f"not a library output: {output}")
        return self.root / user / output

    def meta(self, user: str, output: str, kind: str = "run") -> dict | None:
        k = _kind(kind)
        try:
            loaded = json.loads((self._dir(user, output) / k.pointer).read_text())
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as error:
            raise OSError(f"unreadable pointer for {output}: {error}") from error
        # Data on disk becomes a path only through the same shape a write gives it.
        if not (isinstance(loaded.get("file"), str) and k.file_re.match(loaded["file"])):
            raise OSError(f"the pointer for {output} names a file it should not: {loaded.get('file')!r}")
        # A pointer written before there was a second kind is a .run's.
        return {**loaded, "kind": k.name}

    def build_path(self, user: str, output: str, kind: str = "run") -> tuple[Path, dict] | None:
        meta = self.meta(user, output, kind)
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
                for kind in KINDS:
                    meta = self.meta(user, path.name, kind)
                    if meta is not None:
                        out.append(meta)
        return out

    def for_game(self, user: str, platform: str, game_id: str) -> list[dict]:
        """What the flake can build for a game, each with its pushed build or None."""
        return [
            {**entry, "build": self.meta(user, entry["output"])}
            for entry in available(self.games_dir, platform, game_id)
        ]

    def windows_for_game(self, user: str, platform: str, game_id: str) -> list[dict]:
        """The game's pushed Windows zips, its default output first.

        Read from the store rather than from `available`: most games are only
        their emulator, and that plain output is the one with a zip."""
        if not (USER_RE.match(user) and PLATFORM_RE.match(platform) and ENTRY_ID_RE.match(game_id)):
            return []
        base = output_name(platform, game_id)
        user_dir = self.root / user
        if not user_dir.is_dir():
            return []
        out = []
        for path in sorted(user_dir.iterdir(), key=lambda p: (p.name != base, p.name)):
            variant = path.name[len(base) + 1 :] if path.name.startswith(base + ".") else ""
            if path.name != base and not VARIANT_RE.match(variant):
                continue
            if not (path.is_dir() and OUTPUT_RE.match(path.name)):
                continue
            meta = self.meta(user, path.name, "win")
            if meta is not None:
                out.append({"output": path.name, "variant": variant, "build": meta})
        return out

    def put(
        self,
        user: str,
        output: str,
        read: Callable[[int], bytes],
        length: int,
        *,
        device: str = "",
        sha256: str = "",
        kind: str = "run",
    ) -> dict:
        """Stream `length` bytes from `read` into the store and make them the build.

        The file is whole and hashed on disk before the pointer names it; a
        body cut short, one that does not hash to the `sha256` the client
        claimed, or a "win" that is not a bundle, leaves nothing behind."""
        k = _kind(kind)
        cap = self.max_win_bytes if k.name == "win" else self.max_bytes
        if length > cap:
            raise TooLarge(f"a {'Windows ' if k.name == 'win' else ''}build is at most {cap} bytes")
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
            if k.name == "win":
                check_bundle(tmp, output)
            os.chmod(tmp, 0o644)
            name = f"{k.prefix}{hexdigest[:12]}{k.suffix}"
            meta = {
                "version": 1,
                "kind": k.name,
                "output": output,
                "file": name,
                "sha256": hexdigest,
                "size": length,
                "device": device,
                "written_at": utc_now(),
            }
            with self._lock:
                tmp.replace(directory / name)
                pointer = directory / k.pointer
                part = pointer.with_suffix(".json.part")
                part.write_text(json.dumps(meta))
                part.replace(pointer)
                for old in directory.iterdir():
                    if k.file_re.match(old.name) and old.name != name:
                        old.unlink(missing_ok=True)
            return meta
        finally:
            tmp.unlink(missing_ok=True)

    def delete(self, user: str, output: str, kind: str = "run") -> bool:
        k = _kind(kind)
        directory = self._dir(user, output)
        with self._lock:
            if self.meta(user, output, kind) is None:
                return False
            (directory / k.pointer).unlink()
            for old in directory.iterdir():
                if k.file_re.match(old.name):
                    old.unlink(missing_ok=True)
            return True
