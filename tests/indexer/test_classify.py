"""Classification ladder, exercised against the real payload shapes in the library."""

from pathlib import Path

import pytest

from gotg.indexer.classify import (
    HANDLER_EXCLUDED,
    HANDLER_MANUAL,
    HANDLER_NO_INTRO_SET,
    HANDLER_SCENE_ARCHIVE,
    HANDLER_SINGLE_ARCHIVE,
    HANDLER_SINGLE_FILE,
    HANDLER_WIIU_DECRYPTED,
    HANDLER_WIIU_NUS,
    classify,
)
from gotg.indexer.rules import defaults, load
from gotg.indexer.scan import Source

RULES = defaults()


def src(name, *, is_dir=True, files=(), dirs=(), code_files=(), archive_members=()):
    return Source(
        path=Path("/data/Torrents") / name,
        is_dir=is_dir,
        files=tuple(files),
        dirs=tuple(dirs),
        code_files=tuple(code_files),
        archive_members=tuple(archive_members),
    )


# --- WiiU: decrypted output present vs raw NUS download ----------------------
# Both shapes carry .app/.h3/title.tmd; only the decrypted one also has code/*.rpx.

WIIU_NUS_FILES = ("00000002.app", "00000004.app", "00000004.h3", "title.tik", "title.tmd", "tmd.64")


def test_wiiu_decrypted_wins_over_nus():
    source = src(
        "Legend of Zelda, The - Twilight Princess HD (USA) (En,Fr,Es)",
        files=WIIU_NUS_FILES,
        dirs=("code", "content", "meta"),
        code_files=("app.xml", "cos.xml", "Zelda.rpx"),
    )
    verdict = classify(source, RULES)
    assert verdict.handler == HANDLER_WIIU_DECRYPTED
    assert verdict.platform == "wiiu"


def test_wiiu_nus_without_decrypted_output():
    source = src("Some WiiU Title (USA)", files=WIIU_NUS_FILES)
    verdict = classify(source, RULES)
    assert verdict.handler == HANDLER_WIIU_NUS
    assert verdict.platform == "wiiu"


def test_wiiu_marker_dirs_without_executable_are_not_decrypted():
    # code/ exists but holds no .rpx -> not importable as decrypted.
    source = src(
        "Half Extracted Title (USA)", files=WIIU_NUS_FILES, dirs=("code", "content", "meta"), code_files=("app.xml",)
    )
    assert classify(source, RULES).handler == HANDLER_WIIU_NUS


# --- Scene archive -----------------------------------------------------------


def test_scene_archive_with_extracted_rom():
    source = src(
        "The_Legend_of_Zelda_Skyward_Sword_HD_NSW-VENOM",
        files=(
            "v-lozsshd.nfo",
            "v-the_legend_of_zelda_skyward_sword_hd.nsp",
            "v-the_legend_of_zelda_skyward_sword_hd.r00",
            "v-the_legend_of_zelda_skyward_sword_hd.rar",
            "v-the_legend_of_zelda_skyward_sword_hd.sfv",
        ),
    )
    verdict = classify(source, RULES)
    assert verdict.handler == HANDLER_SCENE_ARCHIVE
    assert verdict.platform == "switch"


def test_scene_archive_platform_comes_from_the_archive_listing():
    # The real server copy has no unpacked ROM — only the .rar set — so the
    # platform has to come from the archive's own header.
    source = src(
        "The_Legend_of_Zelda_Skyward_Sword_HD_NSW-VENOM",
        files=("v-lozsshd.nfo", "v-loz.rar", "v-loz.r00", "v-loz.r01", "v-loz.sfv"),
        archive_members=("v-the_legend_of_zelda_skyward_sword_hd.nsp",),
    )
    verdict = classify(source, RULES)
    assert verdict.handler == HANDLER_SCENE_ARCHIVE
    assert verdict.platform == "switch"


def test_rar_volume_parts_are_never_mistaken_for_roms():
    # .r00 is packaging. Treating it as a ROM produced a bogus ".r00" entry.
    source = src("Some_Release-GRP", files=("x.rar", "x.r00", "x.r01", "x.sfv", "x.nfo"))
    verdict = classify(source, RULES)
    assert verdict.handler == HANDLER_MANUAL
    assert verdict.platform == ""


# --- No-Intro sets and single files ------------------------------------------


def test_dat_dir_name_maps_to_platform():
    verdict = classify(src("Nintendo - Nintendo 64 (BigEndian)", files=("a.zip", "b.zip")), RULES)
    assert (verdict.handler, verdict.platform) == (HANDLER_NO_INTRO_SET, "n64")


def test_excluded_set_is_honored():
    name = "Nintendo - Nintendo Entertainment System (Headered) (Aftermarket)"
    assert classify(src(name, files=("a.zip",)), RULES).handler == HANDLER_EXCLUDED


def test_loose_rom_directory_is_a_set():
    files = tuple(f"Game {i} (USA).z64" for i in range(8))
    verdict = classify(src("random n64 dump", files=files), RULES)
    assert (verdict.handler, verdict.platform) == (HANDLER_NO_INTRO_SET, "n64")


def test_bare_file_is_single_file():
    verdict = classify(src("Legend of Zelda, The - Majora's Mask (USA).z64", is_dir=False), RULES)
    assert (verdict.handler, verdict.platform) == (HANDLER_SINGLE_FILE, "n64")


def test_directory_with_one_rom_is_single_file():
    verdict = classify(src("Majora torrent", files=("Majora's Mask (USA).z64", "readme.txt")), RULES)
    assert (verdict.handler, verdict.platform) == (HANDLER_SINGLE_FILE, "n64")


# --- Never guess -------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        src("mystery.bin", is_dir=False),
        src("empty dir"),
        src("unmapped set", files=tuple(f"g{i}.zip" for i in range(20))),
    ],
)
def test_unidentifiable_payloads_go_to_manual(source):
    verdict = classify(source, RULES)
    assert verdict.handler == HANDLER_MANUAL
    assert verdict.reason, "manual verdicts must explain themselves"


def test_lone_archive_is_judged_by_what_is_inside():
    # load(), not defaults(): `.iso` is mapped in rules.yaml on purpose.
    rules = load()
    """A .7z says nothing about a platform; the image inside says everything."""
    source = Source(
        path=Path("/t/Super Mario Sunshine (USA).7z"),
        is_dir=False,
        archive_members=("Super Mario Sunshine (USA).nkit.iso",),
    )
    verdict = classify(source, rules)
    assert verdict.handler == HANDLER_SINGLE_ARCHIVE
    assert verdict.platform == "gamecube"


def test_lone_archive_of_nothing_recognisable_is_flagged_not_guessed():
    rules = defaults()
    source = Source(
        path=Path("/t/Some Release.7z"),
        is_dir=False,
        archive_members=("readme.txt", "cover.jpg"),
    )
    verdict = classify(source, rules)
    assert verdict.handler == HANDLER_MANUAL
    assert "no known platform" in verdict.reason


def test_an_archive_that_could_not_be_listed_is_not_a_game():
    rules = defaults()
    """No members means 7z was unavailable or the file is broken — say so
    rather than inventing a platform from the wrapper's extension."""
    source = Source(path=Path("/t/Thing.7z"), is_dir=False)
    assert classify(source, rules).handler == HANDLER_MANUAL


# --- a DAT set of one archive per game -----------------------------------------


def test_a_mapped_archive_set_is_an_archive_set():
    from gotg.indexer.classify import HANDLER_ARCHIVE_SET

    games = tuple(f"Game {i} (USA).7z" for i in range(20))
    verdict = classify(src("Nintendo - GameCube", files=games), RULES)
    assert verdict.handler == HANDLER_ARCHIVE_SET
    assert verdict.platform == "gamecube"


def test_an_unmapped_7z_set_is_reported_like_a_zipped_one():
    games = tuple(f"Game {i} (USA).7z" for i in range(20))
    verdict = classify(src("Some - Console", files=games), RULES)
    assert verdict.handler == HANDLER_MANUAL
    assert "unmapped" in verdict.reason and "dat_dirs" in verdict.reason
