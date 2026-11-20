"""PS4 payloads: named from their own param.sfo, as a package or an extracted tree."""

import struct

import pytest

from gotg.contract import ENTRY_ID_RE, HANDLER_PS4_TREE, HANDLER_SINGLE_FILE
from gotg.indexer import ps4
from gotg.indexer.classify import HANDLER_MANUAL, classify
from gotg.indexer.plan import ACTION_ARCHIVE, ACTION_HARDLINK, ACTION_MANUAL
from gotg.indexer.planner import plan_source
from gotg.indexer.rules import defaults
from gotg.indexer.scan import scan

RULES = defaults()


def sfo(**values) -> bytes:
    keys = sorted(values)
    key_table = b""
    data_table = b""
    index = b""
    for key in keys:
        value = values[key]
        if isinstance(value, int):
            raw, fmt = struct.pack("<I", value), 0x0404
        else:
            raw, fmt = value.encode() + b"\x00", 0x0204
        padded = raw + b"\x00" * (-len(raw) % 4)
        index += struct.pack("<HHIII", len(key_table), fmt, len(raw), len(padded), len(data_table))
        key_table += key.encode() + b"\x00"
        data_table += padded
    key_table += b"\x00" * (-len(key_table) % 4)
    key_start = 20 + len(index)
    header = b"\x00PSF" + struct.pack("<IIII", 0x0101, key_start, key_start + len(key_table), len(keys))
    return header + index + key_table + data_table


def pkg(param: bytes, magic: bytes = b"\x7fCNT") -> bytes:
    table = 0x100
    data = table + 2 * 32
    header = bytearray(0x100)
    header[:4] = magic
    struct.pack_into(">I", header, 0x10, 2)
    struct.pack_into(">I", header, 0x18, table)
    entries = struct.pack(">IIIIIIQ", 0x0001, 0, 0, 0, data, 0, 0)
    entries += struct.pack(">IIIIIIQ", 0x1000, 0, 0, 0, data, len(param), 0)
    return bytes(header) + entries + param


def game_sfo(title="Bloodborne", category="gd", content_id="UP9000-CUSA00900_00-BLOODBORNE0000NA", **extra) -> bytes:
    return sfo(TITLE=title, CATEGORY=category, CONTENT_ID=content_id, TITLE_ID=content_id[7:16], **extra)


@pytest.fixture
def roots(tmp_path):
    src, games = tmp_path / "Torrents", tmp_path / "Games"
    src.mkdir()
    games.mkdir()
    return src, games


def make_tree(parent, name, param):
    tree = parent / name
    (tree / "sce_sys").mkdir(parents=True)
    (tree / "eboot.bin").write_bytes(b"\x7fELF")
    (tree / "sce_sys" / "param.sfo").write_bytes(param)
    return tree


def test_sfo_reads_text_and_numbers():
    table = ps4.parse_sfo(sfo(TITLE="Gravity Rush", APP_TYPE=1))
    assert table == {"APP_TYPE": 1, "TITLE": "Gravity Rush"}


@pytest.mark.parametrize("data", [b"", b"\x00PSF" + b"\xff" * 16, b"not an sfo at all"])
def test_a_broken_sfo_is_an_error_not_a_crash(data):
    with pytest.raises(ValueError):
        ps4.parse_sfo(data)


def test_a_pkg_is_named_from_its_sfo(roots):
    src, games = roots
    f = src / "whatever-the-uploader-typed.pkg"
    f.write_bytes(pkg(game_sfo(title="Bloodborne™")))
    [op] = plan_source(f, games, RULES)
    assert op.action == ACTION_HARDLINK
    assert op.platform == "ps4"
    assert op.entry_id == "usa.bloodborne"
    assert op.dst == f"{games}/ps4/usa.bloodborne.pkg"
    assert op.handler == HANDLER_SINGLE_FILE
    assert not any(games.iterdir())


def test_a_ps3_pkg_is_not_filed_as_ps4(roots):
    src, _ = roots
    f = src / "game.pkg"
    f.write_bytes(pkg(game_sfo(), magic=b"\x7fPKG"))
    verdict = classify(scan(f), RULES)
    assert verdict.handler == HANDLER_MANUAL
    assert "PS4" in verdict.reason


def test_an_extracted_tree_is_fetched_as_a_tree(roots):
    src, games = roots
    tree = make_tree(src, "CUSA00900", game_sfo(content_id="EP9000-CUSA00207_00-BLOODBORNE0000EU"))
    [op] = plan_source(tree, games, RULES)
    assert op.action == ACTION_ARCHIVE
    assert op.entry_id == "eur.bloodborne"
    assert op.handler == HANDLER_PS4_TREE
    assert op.src == str(tree)


def test_a_torrent_directory_plans_every_package_and_tree_inside(roots):
    src, games = roots
    release = src / "Bloodborne [CUSA00900]"
    release.mkdir()
    (release / "base.pkg").write_bytes(pkg(game_sfo()))
    (release / "patch.pkg").write_bytes(pkg(game_sfo(category="gp", APP_VER="01.09")))
    make_tree(release, "CUSA03173", game_sfo(title="Persona 5", content_id="UP4062-CUSA05877_00-PERSONA5US000000"))
    (release / "readme.nfo").write_bytes(b"x")
    ops = {op.src.rsplit("/", 1)[-1]: op for op in plan_source(release, games, RULES)}
    assert ops["base.pkg"].action == ACTION_HARDLINK
    assert ops["base.pkg"].handler == HANDLER_SINGLE_FILE
    assert ops["CUSA03173"].action == ACTION_ARCHIVE
    assert ops["CUSA03173"].handler == HANDLER_PS4_TREE
    assert ops["CUSA03173"].entry_id == "usa.persona_5"
    # Updates and DLC wait for a person until ps4 has a recipe for extras.
    assert ops["patch.pkg"].action == ACTION_MANUAL
    assert ops["patch.pkg"].role == "update"
    assert ops["patch.pkg"].version == "01.09"


@pytest.mark.parametrize(
    "content_id, region",
    [
        ("UP9000-CUSA00900_00-X", "usa"),
        ("EP9000-CUSA00207_00-X", "eur"),
        ("JP9000-CUSA00177_00-X", "jpn"),
        ("HP9000-CUSA01363_00-X", "world"),
        ("", "world"),
    ],
)
def test_the_region_is_the_store_the_content_id_names(content_id, region):
    assert ps4.Ps4Info("gd", "t", "CUSA00000", content_id).region == region


def test_a_title_with_no_ascii_falls_back_to_the_title_id(roots):
    src, games = roots
    f = src / "game.pkg"
    f.write_bytes(pkg(game_sfo(title="ブラッドボーン", content_id="JP9000-CUSA00177_00-BLOODBORNE0000JP")))
    [op] = plan_source(f, games, RULES)
    assert op.entry_id == "jpn.cusa00177"
    assert ENTRY_ID_RE.match(op.entry_id)


def test_content_that_is_not_a_game_or_extra_waits_for_a_person(roots):
    src, games = roots
    f = src / "app.pkg"
    f.write_bytes(pkg(game_sfo(category="gda")))
    [op] = plan_source(f, games, RULES)
    assert op.action == ACTION_MANUAL
