"""The tile pictures, and remembering which games have none.

What matters most is the negative half. Most of 5674 games have no art
anywhere, and a cache that only remembers successes asks the network about
every one of them on every launch — which is the difference between a grid
that draws and a grid that hangs.
"""

from __future__ import annotations

import pytest

from gotg_ui.art import PNG, ArtStore, extension_for
from gotg_ui.catalog import Game

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32
WEBP_BYTES = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 32


def game(id="usa.zelda", platform="n64"):
    return Game(id=id, platform=platform, title="A Game", handler="single_file")


@pytest.fixture
def store(tmp_path):
    return ArtStore(tmp_path / "art")


# --- what a picture is --------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "ext"),
    [(PNG_BYTES, ".png"), (JPEG_BYTES, ".jpg"), (WEBP_BYTES, ".webp")],
    ids=["png", "jpeg", "webp"],
)
def test_the_extension_comes_from_the_bytes(body, ext):
    # SteamGridDB serves whichever it has and the URL does not always say, so
    # the magic decides — and a cache directory you can open in an image
    # viewer is worth the six lines.
    assert extension_for(body) == ext


def test_something_that_is_not_a_picture_is_refused(store):
    assert extension_for(b"<html>404</html>") is None
    with pytest.raises(ValueError, match="not an image"):
        store.put(game(), b"<html>404</html>")


# --- the cache ----------------------------------------------------------------


def test_a_stored_picture_is_found_again(store):
    path = store.put(game(), PNG_BYTES)
    assert path.read_bytes() == PNG_BYTES
    assert store.get(game()) == path


def test_nothing_stored_is_simply_nothing(store):
    assert store.get(game()) is None
    assert store.is_miss(game()) is False


def test_platform_and_id_together_name_the_file(store, tmp_path):
    # 222 ids in a real library are on more than one platform, so an id alone
    # would have gb's Asterix overwrite snes' and vice versa on every launch.
    on_gb = store.put(game("eur.asterix", "gb"), PNG_BYTES)
    on_snes = store.put(game("eur.asterix", "snes"), JPEG_BYTES)
    assert on_gb != on_snes
    assert store.get(game("eur.asterix", "gb")).read_bytes() == PNG_BYTES
    assert store.get(game("eur.asterix", "snes")).read_bytes() == JPEG_BYTES


def test_the_cache_survives_a_new_store_over_the_same_directory(tmp_path):
    # "Permanently" means across launches, which is what a state directory
    # outside the nix store is for.
    ArtStore(tmp_path / "art").put(game(), PNG_BYTES)
    assert ArtStore(tmp_path / "art").get(game()) is not None


def test_a_second_put_replaces_rather_than_accumulating(store):
    store.put(game(), PNG_BYTES)
    store.put(game(), JPEG_BYTES)
    assert store.get(game()).read_bytes() == JPEG_BYTES
    found = list((store.root / "n64").iterdir())
    assert len(found) == 1, f"one picture per game, got {found}"


# --- the half that makes it usable --------------------------------------------


def test_a_miss_is_remembered(store):
    store.put_miss(game())
    assert store.is_miss(game()) is True
    assert store.get(game()) is None


def test_a_remembered_miss_survives_a_relaunch(tmp_path):
    ArtStore(tmp_path / "art").put_miss(game())
    assert ArtStore(tmp_path / "art").is_miss(game()) is True


def test_a_miss_records_when_it_was_looked_for(store):
    store.put_miss(game())
    written = store.miss_path(game()).read_text().strip()
    assert written.startswith("20") and written.endswith("Z"), written


def test_finding_art_later_clears_the_miss(store):
    # --refresh found one after all; the negative record must not outlive the
    # thing it was recording.
    store.put_miss(game())
    store.put(game(), PNG_BYTES)
    assert store.is_miss(game()) is False
    assert store.get(game()) is not None


def test_forget_drops_both_halves(store):
    store.put(game(), PNG_BYTES)
    store.forget(game())
    assert store.get(game()) is None
    assert store.is_miss(game()) is False

    store.put_miss(game())
    store.forget(game())
    assert store.is_miss(game()) is False


def test_forgetting_something_absent_is_not_an_error(store):
    store.forget(game())


# --- names that arrive from a catalog -----------------------------------------


@pytest.mark.parametrize("bad", ["../escape", "a/b", "", ".", "..", "with space"])
def test_a_name_that_could_leave_the_cache_is_refused(store, bad):
    # Ids and platforms come off the wire, and these become paths that later
    # meet unlink(). The service validates them too; this does not depend on
    # that having happened.
    with pytest.raises(ValueError):
        store.path_for(Game(id=bad, platform="n64", title="t", handler="single_file"))
    with pytest.raises(ValueError):
        store.path_for(Game(id="usa.zelda", platform=bad, title="t", handler="single_file"))


def test_the_png_signature_is_what_it_claims():
    assert PNG_BYTES.startswith(PNG)


# --- names the contract allows, at their real sizes ---------------------------

# The longest id actually in the library today — 142 characters. The cache's
# first fence invented a 64-character cap the entry-id contract does not have,
# and 127 real games crashed the grid the moment they scrolled into view.
LONGEST_REAL_ID = (
    "world.mani_4_in_1_genki_bakuhatsu_gambaruger_zettai_muteki_raijin_oh_"
    "zoids_densetsu_miracle_adventure_of_esparks_ushinawareta_seiseki_perivron"
)


def test_a_long_id_from_the_real_catalog_is_a_name_not_an_attack(store):
    g = Game(id=LONGEST_REAL_ID, platform="gba", title="t", handler="single_file")
    path = store.put(g, PNG_BYTES)
    assert path.exists()
    assert store.get(g) == path
    store.put_miss(g)  # the sidecar name fits the filesystem too


def test_the_length_ceiling_is_the_filesystem_not_the_contract(store):
    # ext4 caps a filename at 255 bytes; id + ".webp.part" must fit. The
    # contract itself has no cap, so the fence's is set by where the bytes go.
    over = Game(id="usa." + "a" * 250, platform="gba", title="t", handler="single_file")
    with pytest.raises(ValueError):
        store.path_for(over)


def test_an_id_shaped_like_the_contract_but_hostile_is_still_refused(store):
    # The contract requires ^[a-z]{3,5}\. — these never match it.
    for bad in ["usa.../escape", "x.y", "USA.zelda", "usa.zel da", "world..dots"]:
        with pytest.raises(ValueError):
            store.path_for(Game(id=bad, platform="gba", title="t", handler="single_file"))
