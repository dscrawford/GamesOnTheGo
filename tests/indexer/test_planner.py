"""End-to-end planning over real payload shapes, built on disk in a tmp dir.

Planning must stay side-effect free: these tests assert that nothing appears under
the games root, and that every op names a destination the client can serve.
"""

import pytest

from gotg.indexer.plan import (
    ACTION_ARCHIVE,
    ACTION_CONVERT,
    ACTION_EXTRACT,
    ACTION_HARDLINK,
    ACTION_MANUAL,
    ACTION_SKIP,
    plan_single_archive,
)
from gotg.indexer.planner import plan_source, summarize
from gotg.indexer.rules import defaults
from gotg.indexer.slugify import ENTRY_RE

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
    import gotg.indexer.scan as scanmod

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
    import gotg.indexer.scan as scanmod

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


def test_a_lone_archive_is_planned_end_to_end(tmp_path, monkeypatch):
    """plan_source must reach the single_archive planner.

    The unit test above covers the planner itself; this covers the dispatch that
    finds it — which is exactly the seam a real dry run caught as
    "unknown handler 'single_archive'".
    """
    from gotg.indexer import scan as sc
    from gotg.indexer.planner import plan_source
    from gotg.indexer.rules import load as load_rules

    src = tmp_path / "Super Mario Sunshine (USA).7z"
    src.write_bytes(b"archive")
    monkeypatch.setattr(sc, "list_archive_file", lambda p: ("Super Mario Sunshine (USA).nkit.iso",))

    ops = plan_source(src, "/Games", load_rules())

    assert len(ops) == 1
    assert ops[0].action == ACTION_CONVERT
    assert ops[0].dst == "/Games/gamecube/usa.super_mario_sunshine.rvz"


def test_scene_title_comes_from_the_release_not_the_inner_codename(roots):
    """A scene release names the game in its directory; the file inside is often
    a codename. Luigi's Mansion 2 HD shipped as hr-banra.xci inside
    Luigis_Mansion_2_HD_NSW-HR, and taking the inner name gave a library entry
    called "Banra"."""
    src, games = roots
    stem = "hr-banra"
    make_dir(
        src,
        "Luigis_Mansion_2_HD_NSW-HR",
        files=(f"{stem}.nfo", f"{stem}.xci", f"{stem}.rar", f"{stem}.r00", f"{stem}.sfv"),
    )

    ops = plan_source(src / "Luigis_Mansion_2_HD_NSW-HR", games, RULES)

    assert len(ops) == 1
    op = ops[0]
    assert op.entry_id == "world.luigis_mansion_2_hd"
    # "HD" survives as an initialism rather than becoming "Hd".
    assert op.title == "Luigis Mansion 2 HD"


# --- updates and DLC attach to their base ------------------------------------


def test_a_scene_update_attaches_to_its_base_game(roots):
    from gotg.indexer.plan import ACTION_ATTACH

    src, games = roots
    make_dir(
        src,
        "The_Legend_of_Zelda_Tears_of_the_Kingdom_Update_v1.4.3_PROPER_NSW-SUXXORS",
        files=("sxs-totk_v720896.nsp", "sxs-totk_v720896.rar", "sxs-totk_v720896.r00", "sxs-totk_v720896.sfv"),
    )

    ops = plan_source(src / "The_Legend_of_Zelda_Tears_of_the_Kingdom_Update_v1.4.3_PROPER_NSW-SUXXORS", games, RULES)

    assert len(ops) == 1
    op = ops[0]
    assert op.action == ACTION_ATTACH
    assert op.entry_id == "world.legend_of_zelda_tears_of_the_kingdom"
    assert (op.role, op.version) == ("update", "1.4.3")
    assert op.dst == ""
    assert op.handler == "scene_archive"
    assert "update v1.4.3" in op.reason
    assert not list(games.iterdir())


def test_a_no_intro_update_archive_attaches_by_its_tags(roots):
    from gotg.indexer.plan import ACTION_ATTACH

    src, games = roots
    (src / "Legend of Zelda, The - Breath of the Wild (World) (v1.6.0) (Update).7z").write_bytes(b"7z")

    def listing(path):
        return ("update.nsp",)

    import gotg.indexer.scan as scan

    real = scan.list_archive_file
    scan.list_archive_file = listing
    try:
        ops = plan_source(src / "Legend of Zelda, The - Breath of the Wild (World) (v1.6.0) (Update).7z", games, RULES)
    finally:
        scan.list_archive_file = real

    assert [(o.action, o.entry_id, o.role, o.version) for o in ops] == [
        (ACTION_ATTACH, "world.legend_of_zelda_breath_of_the_wild", "update", "1.6.0")
    ]


def test_a_scene_base_release_still_extracts(roots):
    src, games = roots
    make_dir(src, "Luigis_Mansion_2_HD_PROPER_NSW-HR", files=("hr-banra.xci", "hr-banra.rar", "hr-banra.r00"))

    ops = plan_source(src / "Luigis_Mansion_2_HD_PROPER_NSW-HR", games, RULES)

    assert [(o.action, o.entry_id, o.role) for o in ops] == [(ACTION_EXTRACT, "world.luigis_mansion_2_hd", "")]


def test_an_update_for_a_platform_without_extras_support_is_quarantined(roots):
    src, games = roots
    (src / "Some Game (USA) (v1.1) (Update).7z").write_bytes(b"7z")

    import gotg.indexer.scan as scan

    real = scan.list_archive_file
    scan.list_archive_file = lambda path: ("Some Game (USA).rvz",)
    try:
        ops = plan_source(src / "Some Game (USA) (v1.1) (Update).7z", games, RULES)
    finally:
        scan.list_archive_file = real

    assert [(o.action, o.platform, o.entry_id) for o in ops] == [(ACTION_MANUAL, "gamecube", "usa.some_game")]
    assert "no recipe for extras" in ops[0].reason


def test_a_scene_update_with_an_underscore_version_keeps_every_group(roots):
    from gotg.indexer.plan import ACTION_ATTACH

    src, games = roots
    make_dir(src, "Game_Update_v1_2_1_NSW-GRP", files=("g.nsp", "g.rar", "g.r00", "g.sfv"))
    make_dir(src, "Game_Update_v1_9_0_NSW-GRP", files=("h.nsp", "h.rar", "h.r00", "h.sfv"))

    first = plan_source(src / "Game_Update_v1_2_1_NSW-GRP", games, RULES)[0]
    second = plan_source(src / "Game_Update_v1_9_0_NSW-GRP", games, RULES)[0]

    assert (first.action, first.entry_id, first.version) == (ACTION_ATTACH, "world.game", "1.2.1")
    assert (second.action, second.version) == (ACTION_ATTACH, "1.9.0")


# --- a Redump set: one archive per disc, chosen 1G1R ----------------------------


def test_an_archive_set_plans_one_conversion_per_kept_game(roots):
    src, games = roots
    make_dir(
        src,
        "Nintendo - GameCube",
        files=(
            "007 - Nightfire (USA).7z",
            "007 - Nightfire (Europe).7z",
            "1080 Avalanche (USA).7z",
            "1080 Avalanche (USA) (Rev 1).7z",
            "Battalion Wars (USA) (Beta).7z",
            "Luigi's Mansion (USA).7z",
        ),
    )

    from gotg.indexer.rules import load

    # The shipped rules: the built-in defaults name no target format, and the
    # conversion to RVZ is exactly what rules.yaml adds.
    ops = plan_source(src / "Nintendo - GameCube", games, load())
    kept = sorted(
        (o.entry_id, o.action, o.handler, o.src.rsplit("/", 1)[-1]) for o in ops if o.action == ACTION_CONVERT
    )
    assert kept == [
        ("usa.007_nightfire", ACTION_CONVERT, "single_archive", "007 - Nightfire (USA).7z"),
        ("usa.1080_avalanche_rev1", ACTION_CONVERT, "single_archive", "1080 Avalanche (USA) (Rev 1).7z"),
        ("usa.luigis_mansion", ACTION_CONVERT, "single_archive", "Luigi's Mansion (USA).7z"),
    ]
    # The beta has no retail sibling: skipped, said so, never a catalog row.
    skipped = [o for o in ops if o.action == ACTION_SKIP]
    assert [o.src.rsplit("/", 1)[-1] for o in skipped] == ["Battalion Wars (USA) (Beta).7z"]
    assert all(o.dst.endswith(".rvz") for o in ops if o.action == ACTION_CONVERT)
    assert not list(games.iterdir())


def test_an_archive_set_without_a_target_format_keeps_the_archive_name(roots):
    from gotg.indexer.plan import plan_archive_set

    ops = plan_archive_set("snes", "/g", "/t/set", ["Chrono Trigger (USA).7z"], target_ext="")
    assert [(o.action, o.dst) for o in ops] == [(ACTION_EXTRACT, "/g/snes/usa.chrono_trigger.7z")]
