"""What a PS4 package or extracted game says about itself, read from its param.sfo.

A PS4 release name rarely carries a No-Intro region tag, and a bare CUSA folder
carries no title at all, so the id comes from the game's own metadata instead.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

from ..contract import HANDLER_PS4_TREE, HANDLER_SINGLE_FILE
from .plan import ACTION_ARCHIVE, ACTION_HARDLINK, ACTION_MANUAL, Op
from .slugify import title_slug

PKG_MAGIC = b"\x7fCNT"
_PKG_HEADER = 0x60
_PKG_ENTRY = 32
_SFO_ENTRY_ID = 0x1000
# A param.sfo is a few kilobytes; anything far past that is not one.
_SFO_MAX = 1 << 20
_MAX_ENTRIES = 4096

# The content id's service prefix is the store region it was sold in.
_REGIONS = {"UP": "usa", "EP": "eur", "JP": "jpn"}

ROLES = {"gd": "", "gp": "update", "ac": "dlc"}


@dataclass(frozen=True)
class Ps4Info:
    category: str
    title: str
    title_id: str
    content_id: str
    version: str = ""

    @property
    def region(self) -> str:
        return _REGIONS.get(self.content_id[:2], "world")

    @property
    def role(self) -> str | None:
        """'' for a game, 'update' or 'dlc' for an extra, None for anything else."""
        return ROLES.get(self.category)


@dataclass(frozen=True)
class Ps4Member:
    """One PS4 game or extra in a payload; ``name`` is relative to it, "" for the payload itself."""

    name: str
    info: Ps4Info
    is_dir: bool


def parse_sfo(data: bytes) -> dict[str, str | int]:
    """The key/value table of a param.sfo. Raises ValueError on anything malformed."""
    if len(data) < 20 or data[:4] != b"\x00PSF":
        raise ValueError("not a param.sfo")
    key_start, data_start, count = struct.unpack_from("<III", data, 8)
    if count > _MAX_ENTRIES or 20 + count * 16 > len(data):
        raise ValueError("param.sfo index runs past the file")
    out: dict[str, str | int] = {}
    for i in range(count):
        key_off, fmt, length, _max, data_off = struct.unpack_from("<HHIII", data, 20 + i * 16)
        k = key_start + key_off
        end = data.find(b"\x00", k)
        if end < 0:
            raise ValueError("param.sfo key is unterminated")
        key = data[k:end].decode("ascii", "replace")
        v = data_start + data_off
        raw = data[v : v + length]
        if len(raw) != length:
            raise ValueError(f"param.sfo value {key} runs past the file")
        if fmt == 0x0404 and length == 4:
            out[key] = struct.unpack("<I", raw)[0]
        else:
            out[key] = raw.split(b"\x00", 1)[0].decode("utf-8", "replace")
    return out


def info_from_sfo(sfo: dict[str, str | int]) -> Ps4Info:
    def text(key: str) -> str:
        value = sfo.get(key, "")
        return value.strip() if isinstance(value, str) else ""

    # A title may carry line breaks the console's tile wraps on.
    title = " ".join(text("TITLE").split())
    return Ps4Info(
        category=text("CATEGORY").lower(),
        title=title,
        title_id=text("TITLE_ID"),
        content_id=text("CONTENT_ID"),
        version=text("APP_VER") or text("VERSION"),
    )


def read_pkg(path: Path) -> Ps4Info | None:
    """A PS4 .pkg's metadata, or None when the file is not one (a PS3 .pkg is ``\\x7fPKG``)."""
    try:
        with open(path, "rb") as f:
            header = f.read(_PKG_HEADER)
            if len(header) < _PKG_HEADER or header[:4] != PKG_MAGIC:
                return None
            (count,) = struct.unpack_from(">I", header, 0x10)
            (table,) = struct.unpack_from(">I", header, 0x18)
            if count > _MAX_ENTRIES:
                return None
            f.seek(table)
            entries = f.read(count * _PKG_ENTRY)
            for i in range(len(entries) // _PKG_ENTRY):
                entry_id, _name, _f1, _f2, offset, size = struct.unpack_from(">IIIIII", entries, i * _PKG_ENTRY)
                if entry_id != _SFO_ENTRY_ID:
                    continue
                if size > _SFO_MAX:
                    return None
                f.seek(offset)
                return info_from_sfo(parse_sfo(f.read(size)))
    except (OSError, ValueError, struct.error):
        return None
    return None


def read_tree(path: Path) -> Ps4Info | None:
    """An extracted game's metadata: a folder holding eboot.bin and sce_sys/param.sfo."""
    sfo = path / "sce_sys" / "param.sfo"
    try:
        if not (path / "eboot.bin").is_file() or sfo.stat().st_size > _SFO_MAX:
            return None
        return info_from_sfo(parse_sfo(sfo.read_bytes()))
    except (OSError, ValueError, struct.error):
        return None


def probe(path: Path, is_dir: bool, files: tuple[str, ...] = (), dirs: tuple[str, ...] = ()) -> tuple[Ps4Member, ...]:
    """Every PS4 package or extracted game at ``path`` or directly inside it."""
    if not is_dir:
        info = read_pkg(path) if path.name.lower().endswith(".pkg") else None
        return (Ps4Member("", info, False),) if info else ()
    if "eboot.bin" in files:
        info = read_tree(path)
        return (Ps4Member("", info, True),) if info else ()
    found = [Ps4Member(f, i, False) for f in files if f.lower().endswith(".pkg") and (i := read_pkg(path / f))]
    found += [Ps4Member(d, i, True) for d in dirs if (i := read_tree(path / d))]
    return tuple(found)


def plan_ps4(games_root: str, src: str, member: Ps4Member) -> Op:
    """A PS4 game, named from its own param.sfo: a .pkg hardlinked, an extracted tree fetched as is.

    The client unpacks a .pkg on install (env/ps4.nix), so both land as the same
    folder. Updates and DLC have no recipe yet and wait for a person.
    """
    info = member.info
    src = f"{src}/{member.name}" if member.name else src
    role = info.role
    label = info.title or info.title_id or src.rsplit("/", 1)[-1]
    if role is None:
        return Op(
            ACTION_MANUAL,
            "ps4",
            src,
            "",
            "",
            title=label,
            reason=f"PS4 content of category {info.category or '(none)'} is not a game",
        )
    if role:
        return Op(
            ACTION_MANUAL,
            "ps4",
            src,
            "",
            "",
            title=label,
            role=role,
            version=info.version,
            reason=f"PS4 {role} for {info.title_id or '?'}; ps4 has no recipe for extras",
        )
    slug = title_slug(info.title) or title_slug(info.title_id)
    if not slug:
        return Op(ACTION_MANUAL, "ps4", src, "", "", title=label, reason="param.sfo names no title or title id")
    entry = f"{info.region}.{slug}"
    if member.is_dir:
        return Op(
            ACTION_ARCHIVE,
            "ps4",
            src,
            f"{games_root}/ps4/{entry}",
            entry,
            title=label,
            type="file",
            handler=HANDLER_PS4_TREE,
            reason="extracted tree; fetched by the client as it is",
        )
    return Op(
        ACTION_HARDLINK,
        "ps4",
        src,
        f"{games_root}/ps4/{entry}.pkg",
        entry,
        title=label,
        type="file",
        handler=HANDLER_SINGLE_FILE,
    )
