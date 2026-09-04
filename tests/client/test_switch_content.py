"""The Switch content reader, against containers this file builds.

Every NCA here is encrypted the way a console's are — header under XTS with
a header key, key area under ECB with a key-area key, the content-meta
section under CTR — with keys made up per test. What is pinned is that the
reader walks the same layout Ryujinx does and writes the two files Ryujinx
reads back: updates.json selecting the newest patch, dlc.json naming every
DLC NCA by title id.
"""

from __future__ import annotations

import json
import os
import struct
import sys
from pathlib import Path

import pytest

pytest.importorskip("cryptography")
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src" / "client" / "env" / "switch"))
sys.path.insert(0, os.getcwd())
import content  # noqa: E402

HEADER_KEY = bytes(range(32))
KAEK = bytes(range(16, 32))
BASE = 0x0100F2C0115B6000


def _xts_encrypt(key: bytes, data: bytes) -> bytes:
    out = bytearray()
    for i in range(0, len(data), 0x200):
        tweak = (i // 0x200).to_bytes(16, "big")
        enc = Cipher(algorithms.AES(key), modes.XTS(tweak)).encryptor()
        out += enc.update(data[i : i + 0x200]) + enc.finalize()
    return bytes(out)


def _ecb_encrypt(key: bytes, data: bytes) -> bytes:
    enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return enc.update(data) + enc.finalize()


def _ctr_encrypt(key: bytes, section_ctr: int, offset: int, data: bytes) -> bytes:
    nonce = section_ctr.to_bytes(8, "big") + (offset >> 4).to_bytes(8, "big")
    enc = Cipher(algorithms.AES(key), modes.CTR(nonce)).encryptor()
    return enc.update(data) + enc.finalize()


def _pfs0(files: list[tuple[str, bytes]]) -> bytes:
    names = b"".join(n.encode() + b"\0" for n, _ in files)
    names += b"\0" * (-len(names) % 0x10)
    table = b""
    offset = 0
    name_offset = 0
    for name, data in files:
        table += struct.pack("<QQII", offset, len(data), name_offset, 0)
        offset += len(data)
        name_offset += len(name.encode()) + 1
    return struct.pack("<4sIII", b"PFS0", len(files), len(names), 0) + table + names + b"".join(d for _, d in files)


def _hfs0(files: list[tuple[str, bytes]]) -> bytes:
    names = b"".join(n.encode() + b"\0" for n, _ in files)
    names += b"\0" * (-len(names) % 0x10)
    table = b""
    offset = 0
    name_offset = 0
    for name, data in files:
        table += struct.pack("<QQIIQ", offset, len(data), name_offset, 0, 0) + b"\0" * 0x20
        offset += len(data)
        name_offset += len(name.encode()) + 1
    return struct.pack("<4sIII", b"HFS0", len(files), len(names), 0) + table + names + b"".join(d for _, d in files)


def _nca(content_type: int, title_id: int, *, cnmt: tuple[int, int, int] | None = None, keygen: int = 3) -> bytes:
    """One NCA: a header, and for a Meta NCA a section 0 holding the CNMT."""
    header = bytearray(0xC00)
    header[0x200:0x204] = b"NCA3"
    header[0x205] = content_type
    header[0x206] = keygen
    struct.pack_into("<Q", header, 0x210, title_id)
    header[0x220] = keygen
    ctr_key = bytes(range(64, 80))
    keys = b"\0" * 0x20 + ctr_key + b"\0" * 0x10
    header[0x300:0x340] = _ecb_encrypt(KAEK, keys)
    section = b""
    if cnmt is not None:
        kind, cnmt_title, version = cnmt
        meta = struct.pack("<QIB", cnmt_title, version, kind) + b"\0" * 0x13
        pfs = _pfs0([(f"Patch_{cnmt_title:016x}.cnmt", meta)])
        section_ctr = 0x7
        region_offset = 0x20
        struct.pack_into("<II", header, 0x240, 6, 6 + (region_offset + len(pfs) + 0x1FF) // 0x200)
        fs = header[0x400:0x600]
        fs[0] = 2
        fs[2], fs[3], fs[4] = 1, 2, 3
        struct.pack_into("<I", fs, 0x8 + 0x24, 2)
        struct.pack_into("<QQ", fs, 0x8 + 0x28 + 0x10, region_offset, len(pfs))
        struct.pack_into("<Q", fs, 0x140, section_ctr)
        header[0x400:0x600] = fs
        plain = b"\0" * region_offset + pfs
        section = _ctr_encrypt(ctr_key, section_ctr, 6 * 0x200, plain)
    return _xts_encrypt(HEADER_KEY, bytes(header)) + section


def nsp(path: Path, ncas: list[tuple[str, bytes]]) -> Path:
    path.write_bytes(_pfs0(ncas))
    return path


def xci(path: Path, ncas: list[tuple[str, bytes]]) -> Path:
    secure = _hfs0(ncas)
    root = _hfs0([("update", b""), ("normal", b""), ("secure", secure)])
    head = bytearray(0x200)
    head[0x100:0x104] = b"HEAD"
    struct.pack_into("<QQ", head, 0x130, 0xF000, 0)
    path.write_bytes(bytes(head) + b"\0" * (0xF000 - 0x200) + root)
    return path


def base_ncas(title_id=BASE, version=0):
    return [
        ("00.nca", _nca(content.CONTENT_PROGRAM, title_id)),
        ("00.cnmt.nca", _nca(content.CONTENT_META, title_id, cnmt=(content.META_APPLICATION, title_id, version))),
    ]


def patch_ncas(version, title_id=BASE):
    patch_id = title_id | 0x800
    return [
        ("p.nca", _nca(content.CONTENT_PROGRAM, patch_id)),
        ("p.cnmt.nca", _nca(content.CONTENT_META, patch_id, cnmt=(content.META_PATCH, patch_id, version))),
    ]


def dlc_ncas(index, title_id=BASE):
    return [(f"d{index}.nca", _nca(content.CONTENT_PUBLIC_DATA, title_id + 0x1000 + index))]


@pytest.fixture
def keys(tmp_path):
    path = tmp_path / "prod.keys"
    path.write_text(f"header_key = {HEADER_KEY.hex()}\nkey_area_key_application_02 = {KAEK.hex()}\nmaster_key_00 = 00\n")
    return content.load_keys(path)


# --- reading ------------------------------------------------------------------


def test_an_nsp_names_its_application_and_version(tmp_path, keys):
    container = content.inspect(nsp(tmp_path / "game.nsp", base_ncas(version=0x10000)), keys)
    assert container.applications == {BASE: 0x10000}
    assert container.patches == {} and container.dlc == []


def test_an_xci_is_read_through_its_secure_partition(tmp_path, keys):
    container = content.inspect(xci(tmp_path / "game.xci", base_ncas() + patch_ncas(0x20000) + dlc_ncas(1)), keys)
    assert container.applications == {BASE: 0}
    assert container.patches == {BASE | 0x800: 0x20000}
    assert container.dlc == [(BASE + 0x1001, "/d1.nca")]


def test_a_wrong_header_key_is_an_error_not_silence(tmp_path):
    bad = tmp_path / "bad.keys"
    bad.write_text(f"header_key = {bytes(32).hex()}\n")
    nsp(tmp_path / "game.nsp", base_ncas())
    with pytest.raises(content.ContentError, match="did not decrypt"):
        content.inspect(tmp_path / "game.nsp", content.load_keys(bad))


def test_a_patch_whose_cnmt_cannot_be_opened_is_still_a_patch(tmp_path, keys):
    # A key generation the key set lacks: the version is unknown, the patch is not.
    ncas = [("p.nca", _nca(content.CONTENT_PROGRAM, BASE | 0x800, keygen=9))]
    container = content.inspect(nsp(tmp_path / "u.nsp", ncas), keys)
    assert container.patches == {BASE | 0x800: 0}


# --- registration -------------------------------------------------------------


def install_dir(tmp_path):
    install = tmp_path / "world.zelda"
    (install / "extras").mkdir(parents=True)
    nsp(install / "world.zelda.nsp", base_ncas())
    return install


def test_register_selects_the_newest_update_and_lists_every_dlc(tmp_path, keys):
    install = install_dir(tmp_path)
    nsp(install / "extras" / "update_1.1-a.nsp", patch_ncas(0x10000))
    nsp(install / "extras" / "update_1.4-b.nsp", patch_ncas(0x40000))
    nsp(install / "extras" / "dlc-pack.nsp", dlc_ncas(1) + dlc_ncas(2))
    ryujinx = tmp_path / "Ryujinx"

    report = content.register(install, ryujinx, keys)

    games = ryujinx / "games" / f"{BASE:016x}"
    updates = json.loads((games / "updates.json").read_text())
    assert updates["selected"] == str(install / "extras" / "update_1.4-b.nsp")
    assert updates["paths"] == [str(install / "extras" / "update_1.1-a.nsp"), str(install / "extras" / "update_1.4-b.nsp")]
    dlc = json.loads((games / "dlc.json").read_text())
    assert dlc == [
        {
            "path": str(install / "extras" / "dlc-pack.nsp"),
            "dlc_nca_list": [
                {"path": "/d1.nca", "title_id": BASE + 0x1001, "is_enabled": True},
                {"path": "/d2.nca", "title_id": BASE + 0x1002, "is_enabled": True},
            ],
        }
    ]
    assert report == {f"{BASE:016x}": {"update": str(install / "extras" / "update_1.4-b.nsp"), "dlc": 2}}


def test_on_cart_content_registers_the_cartridge_itself(tmp_path, keys):
    install = tmp_path / "world.botw"
    install.mkdir()
    cart = xci(install / "world.botw.xci", base_ncas() + patch_ncas(0x60000) + dlc_ncas(1))
    ryujinx = tmp_path / "Ryujinx"

    content.register(install, ryujinx, keys)

    games = ryujinx / "games" / f"{BASE:016x}"
    assert json.loads((games / "updates.json").read_text())["selected"] == str(cart)
    assert json.loads((games / "dlc.json").read_text())[0]["path"] == str(cart)


def test_register_is_idempotent_and_drops_what_left_the_install(tmp_path, keys):
    install = install_dir(tmp_path)
    update = nsp(install / "extras" / "update.nsp", patch_ncas(0x10000))
    ryujinx = tmp_path / "Ryujinx"
    content.register(install, ryujinx, keys)
    games = ryujinx / "games" / f"{BASE:016x}"
    first = (games / "updates.json").read_bytes()

    content.register(install, ryujinx, keys)
    assert (games / "updates.json").read_bytes() == first

    update.unlink()
    content.register(install, ryujinx, keys)
    assert not (games / "updates.json").exists()
    assert not (games / "dlc.json").exists()


def test_register_keeps_content_added_by_hand_elsewhere(tmp_path, keys):
    install = install_dir(tmp_path)
    nsp(install / "extras" / "update.nsp", patch_ncas(0x10000))
    elsewhere = nsp(tmp_path / "hand-added.nsp", patch_ncas(0x90000))
    gone = tmp_path / "vanished.nsp"
    ryujinx = tmp_path / "Ryujinx"
    games = ryujinx / "games" / f"{BASE:016x}"
    games.mkdir(parents=True)
    (games / "updates.json").write_text(json.dumps({"selected": str(elsewhere), "paths": [str(elsewhere), str(gone)]}))

    content.register(install, ryujinx, keys)

    updates = json.loads((games / "updates.json").read_text())
    assert updates["paths"] == [str(elsewhere), str(install / "extras" / "update.nsp")]
    # The client's own newest update wins the selection; the hand-added file stays available.
    assert updates["selected"] == str(install / "extras" / "update.nsp")


def test_register_without_an_application_is_an_error(tmp_path, keys):
    install = tmp_path / "empty"
    (install / "extras").mkdir(parents=True)
    nsp(install / "extras" / "update.nsp", patch_ncas(0x10000))
    with pytest.raises(content.ContentError, match="no application"):
        content.register(install, tmp_path / "Ryujinx", keys)


@pytest.mark.usefixtures("keys")
def test_the_cli_reports_and_exits_cleanly(tmp_path, capsys):
    install = install_dir(tmp_path)
    nsp(install / "extras" / "update.nsp", patch_ncas(0x10000))
    keyfile = tmp_path / "prod.keys"

    assert content.main(["register", "--keys", str(keyfile), "--ryujinx", str(tmp_path / "R"), str(install)]) == 0
    assert "update update.nsp, 0 DLC" in capsys.readouterr().err

    assert content.main(["inspect", "--keys", str(keyfile), str(install / "world.zelda.nsp")]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown[0]["applications"] == [{"title_id": f"{BASE:016x}", "version": 0}]

    assert content.main(["register", "--keys", str(tmp_path / "missing.keys"), "--ryujinx", "x", str(install)]) == 1
