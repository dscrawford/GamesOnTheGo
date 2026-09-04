#!/usr/bin/env python3
"""What a Switch container holds, and registering it with Ryujinx.

Ryujinx applies updates and DLC only through games/<title id>/updates.json
and dlc.json, which only its library screen writes — and a launch by path
skips that screen. The title ids those files need exist only inside the NCA
headers, encrypted with the console's header key, so this reads exactly what
Ryujinx reads to find them: the file table, each NCA header, and the CNMT
that carries the version. Nothing is extracted or decrypted beyond that.

    content.py inspect  --keys prod.keys <container>...
    content.py register --keys prod.keys --ryujinx <config dir> <install dir>
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

SECTOR = 0x200
NCA_HEADER_SIZE = 0xC00
MEDIA_UNIT = 0x200

# NCA header content types.
CONTENT_PROGRAM = 0
CONTENT_META = 1
CONTENT_PUBLIC_DATA = 5

# CNMT content-meta types.
META_APPLICATION = 0x80
META_PATCH = 0x81
META_ADD_ON = 0x82

# Program-index and content-kind bits Ryujinx masks off to get a base id.
ID_BASE_MASK = ~0x1FFF & 0xFFFFFFFFFFFFFFFF

XCI_ROOT_OFFSET_FIELD = 0x130


class ContentError(Exception):
    pass


@dataclass(frozen=True)
class Entry:
    name: str
    offset: int  # absolute, in the file
    size: int


@dataclass
class Container:
    path: Path
    applications: dict[int, int] = field(default_factory=dict)  # title id -> version
    patches: dict[int, int] = field(default_factory=dict)
    dlc: list[tuple[int, str]] = field(default_factory=list)  # (title id, nca path)

    def as_json(self) -> dict:
        return {
            "path": str(self.path),
            "applications": [{"title_id": f"{t:016x}", "version": v} for t, v in sorted(self.applications.items())],
            "patches": [{"title_id": f"{t:016x}", "version": v} for t, v in sorted(self.patches.items())],
            "dlc": [{"title_id": f"{t:016x}", "path": p} for t, p in sorted(self.dlc)],
        }


# --- keys ---------------------------------------------------------------------


def load_keys(path: Path) -> dict[str, bytes]:
    keys: dict[str, bytes] = {}
    for line in path.read_text().splitlines():
        name, sep, value = line.partition("=")
        if not sep:
            continue
        try:
            keys[name.strip().lower()] = bytes.fromhex(value.strip())
        except ValueError:
            continue
    if "header_key" not in keys or len(keys["header_key"]) != 32:
        raise ContentError(f"{path} carries no header_key")
    return keys


# --- partition file systems ---------------------------------------------------


def _read(fh, offset: int, size: int) -> bytes:
    fh.seek(offset)
    data = fh.read(size)
    if len(data) != size:
        raise ContentError(f"short read at {offset:#x}")
    return data


def _partition(fh, base: int, hfs: bool) -> list[Entry]:
    """The entries of a PFS0 (NSP) or HFS0 (XCI partition) table at `base`."""
    magic, count, strings_size = struct.unpack_from("<4sII", _read(fh, base, 0x10))
    expected = b"HFS0" if hfs else b"PFS0"
    if magic != expected:
        raise ContentError(f"expected {expected.decode()} at {base:#x}, found {magic!r}")
    if count > 4096 or strings_size > 1 << 20:
        raise ContentError(f"implausible file table at {base:#x} ({count} entries, {strings_size:#x} of names)")
    entry_size = 0x40 if hfs else 0x18
    table = _read(fh, base + 0x10, count * entry_size)
    strings = _read(fh, base + 0x10 + count * entry_size, strings_size)
    data_start = base + 0x10 + count * entry_size + strings_size
    entries = []
    for i in range(count):
        offset, size, name_offset = struct.unpack_from("<QQI", table, i * entry_size)
        end = strings.find(b"\0", name_offset)
        name = strings[name_offset : end if end >= 0 else None].decode("utf-8", "replace")
        entries.append(Entry(name, data_start + offset, size))
    return entries


def _nca_entries(fh, path: Path) -> list[Entry]:
    """Every .nca in the container: an NSP is one PFS0, an XCI's games live in
    its secure partition, reached through the root HFS0."""
    if path.suffix.lower() == ".nsp":
        return [e for e in _partition(fh, 0, hfs=False) if e.name.lower().endswith(".nca")]
    head = _read(fh, 0, 0x200)
    if head[0x100:0x104] != b"HEAD":
        raise ContentError(f"{path.name} is not an XCI")
    root_offset = struct.unpack_from("<Q", head, XCI_ROOT_OFFSET_FIELD)[0]
    root = _partition(fh, root_offset, hfs=True)
    secure = next((e for e in root if e.name == "secure"), None)
    if secure is None:
        raise ContentError(f"{path.name} has no secure partition")
    return [e for e in _partition(fh, secure.offset, hfs=True) if e.name.lower().endswith(".nca")]


# --- NCA ----------------------------------------------------------------------


def _xts_decrypt(key: bytes, data: bytes, first_sector: int = 0) -> bytes:
    """Nintendo's XTS: one tweak per 0x200 sector, the sector number big-endian."""
    out = bytearray()
    for i in range(0, len(data), SECTOR):
        tweak = (first_sector + i // SECTOR).to_bytes(16, "big")
        decryptor = Cipher(algorithms.AES(key), modes.XTS(tweak)).decryptor()
        out += decryptor.update(data[i : i + SECTOR]) + decryptor.finalize()
    return bytes(out)


def _ecb_decrypt(key: bytes, data: bytes) -> bytes:
    decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    return decryptor.update(data) + decryptor.finalize()


def _ctr_decrypt(key: bytes, section_ctr: int, offset: int, data: bytes) -> bytes:
    nonce = section_ctr.to_bytes(8, "big") + (offset >> 4).to_bytes(8, "big")
    decryptor = Cipher(algorithms.AES(key), modes.CTR(nonce)).decryptor()
    return decryptor.update(data) + decryptor.finalize()


@dataclass(frozen=True)
class NcaHeader:
    content_type: int
    title_id: int
    key_generation: int
    rights_id: bytes
    raw: bytes

    @property
    def is_program_index_zero(self) -> bool:
        return (self.title_id & 0xFFF) == 0


def _nca_header(fh, entry: Entry, keys: dict[str, bytes]) -> NcaHeader | None:
    if entry.size < NCA_HEADER_SIZE:
        return None
    raw = _xts_decrypt(keys["header_key"], _read(fh, entry.offset, NCA_HEADER_SIZE))
    magic = raw[0x200:0x204]
    if magic not in (b"NCA3", b"NCA2"):
        # A wrong header key decrypts to noise; say which file rather than
        # silently registering nothing.
        raise ContentError(f"{entry.name}: header did not decrypt (magic {magic!r}) — is prod.keys right?")
    content_type = raw[0x205]
    key_generation = max(raw[0x206], raw[0x220])
    title_id = struct.unpack_from("<Q", raw, 0x210)[0]
    return NcaHeader(content_type, title_id, key_generation, raw[0x230:0x240], raw)


def _cnmt(fh, entry: Entry, header: NcaHeader, keys: dict[str, bytes]) -> tuple[int, int, int] | None:
    """(meta type, title id, version) out of a Meta NCA's CNMT — a PFS0 in
    section 0 under AES-CTR with a key-area key, never a title key, so no
    ticket is needed. None when the key set cannot open it; the caller falls
    back to the header alone."""
    index = header.key_generation - 1 if header.key_generation else 0
    kaek = keys.get(f"key_area_key_application_{index:02x}")
    if kaek is None or any(header.rights_id):
        return None
    raw = header.raw
    ctr_key = _ecb_decrypt(kaek, raw[0x300:0x340])[0x20:0x30]

    media_start = struct.unpack_from("<I", raw, 0x240)[0]
    fs = raw[0x400:0x600]
    # PartitionFS, HierarchicalSha256, AES-CTR — the shape every retail
    # content-meta NCA has; anything else is not a CNMT this can read.
    fs_type, hash_type, encryption = fs[2], fs[3], fs[4]
    if fs_type != 1 or hash_type != 2 or encryption != 3:
        return None
    layer_count = struct.unpack_from("<I", fs, 0x8 + 0x24)[0]
    if layer_count < 2:
        return None
    pfs_offset, pfs_size = struct.unpack_from("<QQ", fs, 0x8 + 0x28 + 0x10)
    section_ctr = struct.unpack_from("<Q", fs, 0x140)[0]
    if not 0x10 <= pfs_size <= 1 << 20:
        return None

    section_offset = entry.offset + media_start * MEDIA_UNIT + pfs_offset
    relative = media_start * MEDIA_UNIT + pfs_offset
    try:
        encrypted = _read(fh, section_offset, pfs_size)
    except ContentError:
        return None  # a section past the end of the file: not a CNMT this can read
    pfs = _ctr_decrypt(ctr_key, section_ctr, relative, encrypted)
    magic, count, strings_size = struct.unpack_from("<4sII", pfs, 0)
    if magic != b"PFS0" or count > 64:
        return None
    table = pfs[0x10 : 0x10 + count * 0x18]
    if len(table) < count * 0x18:
        return None
    strings = pfs[0x10 + count * 0x18 : 0x10 + count * 0x18 + strings_size]
    data_start = 0x10 + count * 0x18 + strings_size
    for i in range(count):
        offset, size, name_offset = struct.unpack_from("<QQI", table, i * 0x18)
        end = strings.find(b"\0", name_offset)
        name = strings[name_offset : end if end >= 0 else None]
        if not name.lower().endswith(b".cnmt") or size < 0x20:
            continue
        cnmt = pfs[data_start + offset : data_start + offset + 0x20]
        if len(cnmt) < 0x20:
            return None
        title_id, version = struct.unpack_from("<QI", cnmt, 0)
        return cnmt[0xC], title_id, version
    return None


def inspect(path: Path, keys: dict[str, bytes]) -> Container:
    container = Container(path)
    with path.open("rb") as fh:
        for entry in _nca_entries(fh, path):
            header = _nca_header(fh, entry, keys)
            if header is None:
                continue
            if header.content_type == CONTENT_PUBLIC_DATA:
                container.dlc.append((header.title_id, f"/{entry.name}"))
            elif header.content_type == CONTENT_META:
                meta = _cnmt(fh, entry, header, keys)
                if meta is None:
                    continue
                kind, title_id, version = meta
                if kind == META_APPLICATION:
                    container.applications[title_id] = version
                elif kind == META_PATCH:
                    container.patches[title_id] = version
            elif header.content_type == CONTENT_PROGRAM and header.title_id & 0x800:
                # A patch whose CNMT the key set could not open: known to
                # exist, version unknown — better registered at 0 than lost.
                container.patches.setdefault(header.title_id, 0)
    return container


# --- registration -------------------------------------------------------------


def base_id(title_id: int) -> int:
    return title_id & ID_BASE_MASK


def _containers(install: Path) -> list[Path]:
    found = [p for p in install.rglob("*") if p.is_file() and p.suffix.lower() in (".nsp", ".xci")]
    return sorted(found)


def _is_extra(path: Path, install: Path) -> bool:
    return "extras" in path.relative_to(install).parts


def register(install: Path, ryujinx: Path, keys: dict[str, bytes]) -> dict:
    """Write updates.json and dlc.json for every title under `install`.

    The install directory is authoritative: what vanished from it is dropped;
    entries naming files elsewhere (added by hand in Ryujinx) are kept.

    Which titles get written is decided by the game itself, never by extras/:
    an attached file claiming to be some other title's application would
    otherwise write that title's registration, and the config is shared.
    """
    containers: list[tuple[Path, Container]] = []
    for path in _containers(install):
        try:
            containers.append((path, inspect(path, keys)))
        except (ContentError, struct.error) as error:
            # A broken or hostile extra must not cost the game its launch;
            # the game itself failing to read is the caller's to hear about.
            if not _is_extra(path, install):
                raise
            print(f"switch-content: skipping {path.name}: {error}", file=sys.stderr)
    bases = {base_id(t) for p, c in containers if not _is_extra(p, install) for t in c.applications}
    if not bases:
        raise ContentError(f"no application found under {install}")
    containers = [c for _, c in containers]

    report: dict[str, dict] = {}
    for base in sorted(bases):
        patch_sets = sorted(
            ((v, str(c.path)) for c in containers for t, v in c.patches.items() if base_id(t) == base),
            key=lambda item: (item[0], item[1]),
        )
        dlc_by_container: dict[str, list[tuple[int, str]]] = {}
        for c in containers:
            mine = [(t, p) for t, p in c.dlc if base_id(t) == base]
            if mine:
                dlc_by_container[str(c.path)] = sorted(mine)

        games = ryujinx / "games" / f"{base:016x}"
        games.mkdir(parents=True, exist_ok=True)
        selected = _write_updates(games / "updates.json", install, patch_sets)
        _write_dlc(games / "dlc.json", install, dlc_by_container)
        report[f"{base:016x}"] = {
            "update": selected,
            "dlc": sum(len(v) for v in dlc_by_container.values()),
        }
    return report


def _under(path: str, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _write_json(path: Path, payload) -> None:
    tmp = path.with_suffix(path.suffix + ".gotg")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(path)


def _write_updates(path: Path, install: Path, patches: list[tuple[int, str]]) -> str | None:
    existing = _load_json(path, {})
    kept = [p for p in existing.get("paths", []) if isinstance(p, str) and not _under(p, install) and Path(p).is_file()]
    paths = kept + [p for _, p in patches]
    # Ryujinx keeps the person's own choice among files it manages itself;
    # among what the client installed the latest version wins, which is the
    # whole reason the update was fetched.
    selected = patches[-1][1] if patches else existing.get("selected")
    if selected not in paths:
        selected = paths[-1] if paths else None
    if not paths:
        if path.exists():
            path.unlink()
        return None
    _write_json(path, {"selected": selected, "paths": paths})
    return selected


def _write_dlc(path: Path, install: Path, mine: dict[str, list[tuple[int, str]]]) -> None:
    existing = _load_json(path, [])
    kept = [
        c
        for c in existing
        if isinstance(c, dict)
        and isinstance(c.get("path"), str)
        and not _under(c["path"], install)
        and Path(c["path"]).is_file()
    ]
    ours = [
        {
            "path": container,
            "dlc_nca_list": [{"path": nca, "title_id": title_id, "is_enabled": True} for title_id, nca in ncas],
        }
        for container, ncas in sorted(mine.items())
    ]
    if not kept and not ours:
        if path.exists():
            path.unlink()
        return
    _write_json(path, kept + ours)


# --- CLI ----------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="What a Switch container holds, and registering it with Ryujinx.")
    sub = parser.add_subparsers(dest="command", required=True)
    show = sub.add_parser("inspect", help="print what each container holds, as JSON")
    show.add_argument("--keys", type=Path, required=True)
    show.add_argument("containers", nargs="+", type=Path)
    reg = sub.add_parser("register", help="write Ryujinx's updates.json and dlc.json for an install")
    reg.add_argument("--keys", type=Path, required=True)
    reg.add_argument("--ryujinx", type=Path, required=True, help="Ryujinx's config directory")
    reg.add_argument("install", type=Path)
    args = parser.parse_args(argv)

    try:
        keys = load_keys(args.keys)
        if args.command == "inspect":
            print(json.dumps([inspect(p, keys).as_json() for p in args.containers], indent=2))
        else:
            report = register(args.install, args.ryujinx, keys)
            for base, what in report.items():
                update = Path(what["update"]).name if what["update"] else "none"
                print(f"{base}: update {update}, {what['dlc']} DLC", file=sys.stderr)
    except (ContentError, OSError, struct.error, ValueError) as error:
        print(f"switch-content: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
