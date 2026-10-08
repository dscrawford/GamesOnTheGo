"""The logs store on its own: ids, the zstd check, quota order, modes."""

from __future__ import annotations

import itertools
import json
import stat

import pytest

from gotg.logs import LogsStore, TooLarge, is_zstd, pick_victims, valid_session_id

ZSTD = b"\x28\xb5\x2f\xfd" + b"x" * 100
ID = "20261008T165221Z-env-n64-usa_paper_mario-paperboat"


def sid(n: int) -> str:
    return f"2026100{n}T000000Z-env-n64-game"


@pytest.fixture
def store(tmp_path):
    ticks = itertools.count(1_700_000_000)
    return LogsStore(tmp_path / "logs", quota_bytes=1000, max_upload_bytes=600, clock=lambda: next(ticks))


def blob(size: int) -> bytes:
    return ZSTD + b"y" * (size - len(ZSTD))


@pytest.mark.parametrize("good", [ID, "20261008T165221Z-env-a", "20261008T165221Z-env-x_y-z9"])
def test_valid_ids(good):
    assert valid_session_id(good)


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "../x",
        ID + "/",
        "20261008T165221Z-n64",
        "2026100T165221Z-env-a",
        "20261008T165221Z-env-A",
        ID + "\n",
        "x" * 300,
    ],
)
def test_invalid_ids(bad):
    assert not valid_session_id(bad)


def test_zstd_magic():
    assert is_zstd(ZSTD)
    assert not is_zstd(b"\x1f\x8b\x08\x00")
    assert not is_zstd(b"")


def test_victims_are_oldest_first_and_stop_under_quota():
    entries = [("b", "t2", 400), ("a", "t1", 400), ("c", "t3", 400)]
    assert pick_victims(entries, quota=1000, keep="c") == ["a"]


def test_victims_never_include_the_new_one():
    entries = [("old", "t1", 100), ("new", "t0", 950)]
    assert pick_victims(entries, quota=1000, keep="new") == ["old"]


def test_nothing_to_drop_under_quota():
    assert pick_victims([("a", "t1", 10)], quota=1000, keep="a") == []


def test_put_then_read_back(store):
    kept = store.put("daniel", sid(1), blob(200), "env-n64-game", "deck")
    assert (kept.bytes, kept.sessions, kept.dropped) == (200, 1, [])
    assert store.path("daniel", sid(1)).read_bytes() == blob(200)
    (entry,) = store.list_user("daniel")
    assert entry["id"] == sid(1) and entry["bytes"] == 200
    assert entry["attr"] == "env-n64-game" and entry["device"] == "deck"


def test_modes_are_private(store):
    store.put("daniel", sid(1), blob(200), "a", "d")
    user_dir = store.root / "daniel"
    assert stat.S_IMODE(user_dir.stat().st_mode) == 0o700
    for path in user_dir.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not [p for p in user_dir.iterdir() if p.suffix == ".part"]


def test_metadata_sits_beside_the_bundle(store):
    store.put("daniel", sid(1), blob(200), "env-a", "deck")
    meta = json.loads((store.root / "daniel" / f"{sid(1)}.json").read_text())
    assert set(meta) == {"at", "bytes", "attr", "device"}


def test_quota_drops_the_oldest(store):
    for n in (1, 2, 3):
        kept = store.put("daniel", sid(n), blob(400), "a", "d")
    assert kept.dropped == [sid(1)]
    assert kept.sessions == 2
    assert [e["id"] for e in store.list_user("daniel")] == [sid(2), sid(3)]
    assert store.path("daniel", sid(1)) is None


def test_a_session_over_the_upload_cap_is_refused(store):
    with pytest.raises(TooLarge):
        store.put("daniel", sid(1), blob(601), "a", "d")


def test_a_session_over_the_quota_alone_is_refused(tmp_path):
    small = LogsStore(tmp_path / "l", quota_bytes=300, max_upload_bytes=600)
    with pytest.raises(TooLarge):
        small.put("daniel", sid(1), blob(400), "a", "d")
    assert small.list_user("daniel") == []


def test_refusals_leave_older_sessions_alone(store):
    store.put("daniel", sid(1), blob(400), "a", "d")
    with pytest.raises(TooLarge):
        store.put("daniel", sid(2), blob(601), "a", "d")
    assert [e["id"] for e in store.list_user("daniel")] == [sid(1)]


def test_bad_id_and_non_zstd_are_value_errors(store):
    with pytest.raises(ValueError):
        store.put("daniel", "../escape", blob(200), "a", "d")
    with pytest.raises(ValueError):
        store.put("daniel", sid(1), b"plain text, not zstd", "a", "d")
    with pytest.raises(ValueError):
        store.put("../daniel", sid(1), blob(200), "a", "d")


def test_the_same_id_again_replaces_it(store):
    store.put("daniel", sid(1), blob(200), "a", "d")
    kept = store.put("daniel", sid(1), blob(300), "a", "d")
    assert kept.sessions == 1 and kept.bytes == 300


def test_users_are_separate(store):
    store.put("daniel", sid(1), blob(400), "a", "d")
    store.put("ann", sid(1), blob(400), "a", "d")
    assert store.path("ann", sid(1)) != store.path("daniel", sid(1))
    assert {u["user"]: u["bytes"] for u in store.list_all()} == {"daniel": 400, "ann": 400}


def test_delete(store):
    store.put("daniel", sid(1), blob(200), "a", "d")
    assert store.delete("daniel", sid(1)) is True
    assert store.delete("daniel", sid(1)) is False
    assert store.list_user("daniel") == []


def test_unknown_user_lists_empty_and_path_validates(store):
    assert store.list_user("nobody") == []
    assert store.path("nobody", sid(1)) is None
    assert store.path("daniel", "../../etc/passwd") is None
