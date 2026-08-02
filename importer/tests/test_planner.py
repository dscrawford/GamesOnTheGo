"""End-to-end planning over real payload shapes, built on disk in a tmp dir.

Planning must stay side-effect free: these tests assert that nothing appears under
the games root, and that every op names a destination the client can serve.
"""

import pytest

from gotg_importer.plan import (
    ACTION_ARCHIVE,
    ACTION_CONVERT,
    ACTION_EXTRACT,
    ACTION_HARDLINK,
    ACTION_MANUAL,
    ACTION_SKIP,
    plan_single_archive,
)
from gotg_importer.planner import plan_source, summarize
from gotg_importer.rules import defaults
from gotg_importer.slugify import ENTRY_RE

RULES = defaults()


@pytest.fixture
def roots(tmp_path):
    src = tmp_path / "Torrents"
    games = tmp_path / "Games"
    src.mkdir()
    games.mkdir()
    return src, games


def make_dir(parent, name, files=(), subdirs=()):
    d = parent / name
    d.mkdir()
    for f in files:
        (d / f).write_bytes(b"x")
    for sub, subfiles in subdirs:
        (d / sub).mkdir()
        for f in subfiles:
            (d / sub / f).write_bytes(b"x")
    return d


def test_wiiu_decrypted_plans_a_single_zip(roots):
    src, games = roots
    make_dir(
        src,
        "Legend of Zelda, The - Twilight Princess HD (USA) (En,Fr,Es)",
        files=("00000002.app", "00000004.h3", "title.tmd"),
        subdirs=(("code", ("Zelda.rpx", "app.xml")), ("content", ()), ("meta", ())),
    )

    ops = plan_source(src / "Legend of Zelda, The - Twilight Princess HD (USA) (En,Fr,Es)", games, RULES)

    assert len(ops) == 1
    op = ops[0]
    assert op.action == ACTION_ARCHIVE
    assert op.entry_id == "usa.legend_of_zelda_twilight_princess_hd"
    assert op.dst == f"{games}/wiiu/usa.legend_of_zelda_twilight_princess_hd.zip"
    assert op.type == "file"


def test_wiiu_nus_is_deferred_to_manual(roots):
    src, games = roots
    make_dir(
        src,
        "Legend of Zelda, The - The Wind Waker HD (USA) (En,Fr,Es)",
        files=("00000002.app", "00000004.h3", "title.tmd"),
    )

    ops = plan_source(src / "Legend of Zelda, The - The Wind Waker HD (USA) (En,Fr,Es)", games, RULES)

    assert [op.action for op in ops] == [ACTION_MANUAL]
    assert ops[0].entry_id == "usa.legend_of_zelda_the_wind_waker_hd"


def test_scene_release_plans_an_extraction(roots):
    src, games = roots
    stem = "v-the_legend_of_zelda_skyward_sword_hd"
    make_dir(
        src,
        "The_Legend_of_Zelda_Skyward_Sword_HD_NSW-VENOM",
        files=("v-lozsshd.nfo", f"{stem}.nsp", f"{stem}.rar", f"{stem}.r00", f"{stem}.sfv"),
    )

    ops = plan_source(src / "The_Legend_of_Zelda_Skyward_Sword_HD_NSW-VENOM", games, RULES)

    assert len(ops) == 1
    op = ops[0]
    assert op.action == ACTION_EXTRACT
    assert op.platform == "switch"
    # Scene names carry no region tag, so it defaults and is flagged for confirmation.
    assert op.entry_id == "world.legend_of_zelda_skyward_sword_hd"
    assert "region" in op.reason


def test_scene_release_without_an_unpacked_rom_reads_the_archive(roots, monkeypatch):
    # The live library ships only the .rar set, which previously produced a
    # platform-less op writing to "/Games//world.….r00".
    import gotg_importer.scan as scanmod

    src, games = roots
    stem = "v-the_legend_of_zelda_skyward_sword_hd"
    make_dir(
        src,
        "The_Legend_of_Zelda_Skyward_Sword_HD_NSW-VENOM",
        files=("v-lozsshd.nfo", f"{stem}.rar", f"{stem}.r00", f"{stem}.r01", f"{stem}.sfv"),
    )
    monkeypatch.setattr(scanmod, "list_archive", lambda path: (f"{stem}.nsp",))

    ops = plan_source(src / "The_Legend_of_Zelda_Skyward_Sword_HD_NSW-VENOM", games, RULES)

    assert len(ops) == 1
    op = ops[0]
    assert op.action == ACTION_EXTRACT
    assert op.platform == "switch"
    assert op.dst == f"{games}/switch/world.legend_of_zelda_skyward_sword_hd.nsp"


def test_scene_release_of_an_unknown_platform_is_flagged_not_guessed(roots, monkeypatch):
    import gotg_importer.scan as scanmod

    src, games = roots
    make_dir(src, "Some_Release-GRP", files=("x.rar", "x.r00", "x.sfv"))
    monkeypatch.setattr(scanmod, "list_archive", lambda path: ("setup.exe",))

    ops = plan_source(src / "Some_Release-GRP", games, RULES)

    assert [op.action for op in ops] == [ACTION_MANUAL]
    assert ops[0].dst == "", "a platform-less op must not name a destination"


def test_scene_region_override_pins_the_region(roots):
    src, games = roots
    from dataclasses import replace

    rules = replace(RULES, scene_overrides=(("NSW-VENOM", "usa"),))
    stem = "v-the_legend_of_zelda_skyward_sword_hd"
    make_dir(src, "The_Legend_of_Zelda_Skyward_Sword_HD_NSW-VENOM", files=(f"{stem}.nsp", f"{stem}.rar", f"{stem}.sfv"))

    ops = plan_source(src / "The_Legend_of_Zelda_Skyward_Sword_HD_NSW-VENOM", games, rules)

    assert ops[0].entry_id == "usa.legend_of_zelda_skyward_sword_hd"
    assert ops[0].dst.endswith("/switch/usa.legend_of_zelda_skyward_sword_hd.nsp")


def test_no_intro_set_curates_to_one_rom_per_title(roots):
    src, games = roots
    make_dir(
        src,
        "Nintendo - Nintendo 64 (BigEndian)",
        files=(
            "Body Harvest (Europe).zip",
            "Body Harvest (USA).zip",
            "Body Harvest (USA) (Beta).zip",
            "AeroGauge (Japan) (Rev 1).zip",
            "AeroGauge (Japan).zip",
            "Thing (USA) (Proto).zip",
        ),
    )

    ops = plan_source(src / "Nintendo - Nintendo 64 (BigEndian)", games, RULES)
    kept = [op for op in ops if op.action == ACTION_HARDLINK]
    skipped = [op for op in ops if op.action == ACTION_SKIP]

    assert {op.entry_id for op in kept} == {"usa.body_harvest", "jpn.aerogauge_rev1"}
    assert len(skipped) == 1  # the proto-only title has no retail dump
    assert all(op.dst.startswith(f"{games}/n64/") for op in kept)


def test_single_rom_torrent_keeps_non_retail_dumps(roots):
    src, games = roots
    (src / "40 Winks (USA) (Proto) (2000-01-10).z64").write_bytes(b"x")

    ops = plan_source(src / "40 Winks (USA) (Proto) (2000-01-10).z64", games, RULES)

    # Deliberately downloaded, so 1G1R curation must not drop it.
    assert [op.action for op in ops] == [ACTION_HARDLINK]
    assert ops[0].entry_id == "usa.40_winks"


def test_planning_writes_nothing(roots):
    src, games = roots
    make_dir(src, "Nintendo - Nintendo 64 (BigEndian)", files=("Body Harvest (USA).zip",))

    plan_source(src / "Nintendo - Nintendo 64 (BigEndian)", games, RULES)

    assert list(games.iterdir()) == []


def test_every_planned_id_is_a_valid_entry_id(roots):
    src, games = roots
    make_dir(
        src,
        "Nintendo - Super Nintendo Entertainment System",
        files=(
            "Addams Family, The - Pugsley's Scavenger Hunt (USA).zip",
            "Advanced Dungeons & Dragons - Eye of the Beholder (USA).zip",
            "007 - The World Is Not Enough (Europe) (En,Fr,De).zip",
        ),
    )

    ops = plan_source(src / "Nintendo - Super Nintendo Entertainment System", games, RULES)

    assert ops
    for op in ops:
        assert ENTRY_RE.match(op.entry_id), op


def test_summary_line_format(roots):
    src, games = roots
    make_dir(src, "Nintendo - Nintendo 64 (BigEndian)", files=("Body Harvest (USA).zip",))
    ops = plan_source(src / "Nintendo - Nintendo 64 (BigEndian)", games, RULES)
    assert summarize(ops) == "hardlink=1 extract=0 archive=0 manual=0 skip=0"


def test_lone_archive_converts_to_the_format_the_emulator_wants():
    """Sunshine's real shape: a 7z holding an NKit image Dolphin can read but
    which is not the format the library stores."""
    op = plan_single_archive(
        "gamecube",
        "/Games",
        "/T/Super Mario Sunshine (USA).7z",
        "Super Mario Sunshine (USA).nkit.iso",
        target_ext="rvz",
    )
    assert op.action == ACTION_CONVERT
    assert op.entry_id == "usa.super_mario_sunshine"
    assert op.dst == "/Games/gamecube/usa.super_mario_sunshine.rvz"


def test_an_archive_already_in_the_target_format_is_only_unpacked():
    op = plan_single_archive(
        "gamecube",
        "/Games",
        "/T/Some Game (USA).7z",
        "Some Game (USA).rvz",
        target_ext="rvz",
    )
    assert op.action == ACTION_EXTRACT


def test_a_platform_with_no_target_keeps_whatever_it_holds():
    """Every platform imported so far: the ROM inside is already what ares wants."""
    op = plan_single_archive(
        "n64",
        "/Games",
        "/T/Some Game (USA).7z",
        "Some Game (USA).z64",
    )
    assert op.action == ACTION_EXTRACT
    assert op.dst == "/Games/n64/usa.some_game.z64"


def test_an_archive_whose_name_yields_no_id_is_flagged():
    op = plan_single_archive(
        "gamecube",
        "/Games",
        "/T/!!!.7z",
        "x.iso",
        target_ext="rvz",
    )
    assert op.action == ACTION_MANUAL
