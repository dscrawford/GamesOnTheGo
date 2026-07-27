"""Golden fixtures for the slugifier, including GOTG's own spec examples."""

import pytest

from gotg_importer.slugify import ENTRY_RE, parse, select_1g1r, title_slug

# --- GOTG spec examples (must match verbatim) --------------------------------


@pytest.mark.parametrize(
    "filename, region, slug, ext",
    [
        ("Legend of Zelda, The - Majora's Mask (USA).z64", "usa", "legend_of_zelda_majoras_mask", "z64"),
        (
            "Legend of Zelda, The - Twilight Princess HD (USA) (En,Fr,Es)",
            "usa",
            "legend_of_zelda_twilight_princess_hd",
            "",
        ),
    ],
)
def test_gotg_examples(filename, region, slug, ext):
    p = parse(filename)
    assert p.region == region
    assert p.slug == slug
    assert p.ext == ext
    assert p.entry_id == f"{region}.{slug}"
    assert p.valid


# --- Article handling (leading only) -----------------------------------------


def test_trailing_comma_article_dropped():
    assert parse("7th Saga, The (USA).zip").entry_id == "usa.7th_saga"
    assert parse("Addams Family, The (USA).zip").entry_id == "usa.addams_family"


def test_article_dropped_before_subtitle():
    p = parse("Addams Family, The - Pugsley's Scavenger Hunt (USA).zip")
    assert p.entry_id == "usa.addams_family_pugsleys_scavenger_hunt"


def test_mid_title_the_kept():
    # Leading token is a number, not an article -> keep mid-title "The".
    p = parse("007 - The World Is Not Enough (Europe) (En,Fr,De).zip")
    assert p.entry_id == "eur.007_the_world_is_not_enough"


def test_wind_waker_leading_only():
    p = parse("Legend of Zelda, The - The Wind Waker HD (USA) (En,Fr,Es)")
    assert p.entry_id == "usa.legend_of_zelda_the_wind_waker_hd"


# --- Punctuation / charset ---------------------------------------------------


def test_ampersand_becomes_and():
    p = parse("Advanced Dungeons & Dragons - Eye of the Beholder (USA).zip")
    assert p.entry_id == "usa.advanced_dungeons_and_dragons_eye_of_the_beholder"


def test_apostrophe_removed():
    assert title_slug("Majora's Mask") == "majoras_mask"


def test_all_ids_match_regex():
    for fn in [
        "64 Oozumou (Japan).zip",
        "AeroGauge (Japan) (Rev 1).zip",
        "Action Replay Pro 64 (Europe) (v3.0) (Unl).zip",
        "Bulls vs Blazers and the NBA Playoffs (Europe) (Rev 1).zip",
    ]:
        assert ENTRY_RE.match(parse(fn).entry_id), fn


# --- Regions -----------------------------------------------------------------


@pytest.mark.parametrize(
    "tag, expected",
    [
        ("(USA)", "usa"),
        ("(Europe)", "eur"),
        ("(Japan)", "jpn"),
        ("(World)", "world"),
        ("(Germany)", "eur"),
        ("(USA, Europe)", "world"),
    ],
)
def test_region_mapping(tag, expected):
    assert parse(f"Some Game {tag}.zip").region == expected


def test_missing_region_low_confidence():
    p = parse("Homebrew Thing.zip")
    assert p.region == "world"
    assert p.confident is False


# --- Revisions ---------------------------------------------------------------


@pytest.mark.parametrize(
    "tag, rev",
    [
        ("(Rev 1)", "rev1"),
        ("(v2)", "v2"),
        ("(v1.05)", "v1_05"),
        ("(v3.3)", "v3_3"),
    ],
)
def test_revision_suffix(tag, rev):
    p = parse(f"AeroGauge (Japan) {tag}.zip")
    assert p.revision == rev
    assert p.entry_id == f"jpn.aerogauge_{rev}"


# --- Retail / non-retail -----------------------------------------------------


@pytest.mark.parametrize(
    "filename, retail",
    [
        ("40 Winks (USA) (Proto) (2000-01-10).zip", False),
        ("AeroGauge (Japan) (Demo) (Kiosk).zip", False),
        ("007 - The World Is Not Enough (USA) (v2) (Beta).zip", False),
        ("Action Replay Pro 64 (Europe) (v3.0) (Unl).zip", False),
        ("AeroGauge (Europe) (En,Fr,De).zip", True),
    ],
)
def test_retail_flag(filename, retail):
    assert parse(filename).is_retail is retail


# --- 1G1R selection ----------------------------------------------------------


def test_1g1r_prefers_usa_retail():
    cands = [
        parse("Game X (Europe).zip"),
        parse("Game X (USA).zip"),
        parse("Game X (Japan).zip"),
        parse("Game X (USA) (Beta).zip"),
    ]
    winner = select_1g1r(cands)
    assert winner is not None
    assert winner.region == "usa"
    assert winner.is_retail


def test_1g1r_falls_back_when_no_usa():
    # All USA dumps are betas; only Europe/Japan retail exist -> Europe wins.
    cands = [
        parse("Aidyn Chronicles - The First Mage (Europe).zip"),
        parse("Aidyn Chronicles - The First Mage (USA) (Beta) (2000-02-10).zip"),
    ]
    winner = select_1g1r(cands)
    assert winner is not None
    assert winner.region == "eur"


def test_1g1r_returns_none_when_all_nonretail():
    cands = [parse("Thing (USA) (Proto).zip"), parse("Thing (Japan) (Beta).zip")]
    assert select_1g1r(cands) is None


# --- Variant tags and numbered statuses --------------------------------------
# Both of these came out of the real library, where filename order was silently
# deciding which dump of 135 titles got imported.


@pytest.mark.parametrize(
    "tag",
    ["(Beta 1)", "(Beta 2)", "(Proto 1)", "(Demo 2)", "(Sample 1)"],
)
def test_numbered_status_tags_are_still_non_retail(tag):
    # An exact-match lookup let every numbered dump through as a retail game.
    assert parse(f"Some Game (USA) {tag}.zip").is_retail is False


@pytest.mark.parametrize(
    "filename, expected",
    [
        ("Game (USA).zip", []),
        ("Game (USA) (En,Fr,De).zip", []),
        ("Game (USA) (Rev 1).zip", []),
        ("Game (USA) (Proto) (2000-01-10).zip", ["Proto"]),
        ("Game (USA) (LodgeNet).zip", ["LodgeNet"]),
        ("Game (USA) (En,Fr,Es) (GameCube).zip", ["GameCube"]),
        ("Game (USA, Europe) (Arcade).zip", ["Arcade"]),
    ],
)
def test_variant_tags_exclude_region_language_revision_and_dates(filename, expected):
    assert parse(filename).variants == expected


def test_1g1r_prefers_the_standard_release_over_a_reissue():
    # The real case: Majora's Mask has USA cartridge, GameCube bonus-disc and
    # LodgeNet hotel-rental dumps, all retail and all equally ranked until now.
    cands = [
        parse("Legend of Zelda, The - Majora's Mask (USA) (GameCube).zip"),
        parse("Legend of Zelda, The - Majora's Mask (USA) (LodgeNet).zip"),
        parse("Legend of Zelda, The - Majora's Mask (USA).zip"),
    ]
    winner = select_1g1r(cands)
    assert winner is not None
    assert winner.variants == []


def test_variant_tie_break_does_not_outrank_region_or_revision():
    # A plain European dump must not beat a tagged USA one, and a base revision
    # must not beat a higher one.
    assert select_1g1r([parse("Game (Europe).zip"), parse("Game (USA) (Arcade).zip")]).region == "usa"
    assert select_1g1r([parse("Game (USA).zip"), parse("Game (USA) (Rev 1).zip")]).revision == "rev1"


def test_a_title_with_only_tagged_dumps_still_imports():
    winner = select_1g1r([parse("Game (USA) (Arcade).zip")])
    assert winner is not None
    assert winner.variants == ["Arcade"]
