"""The token store: minted once, hashed at rest, claimed exactly once.

The properties that matter are the ones an attacker or a crash would test:
a claim code works exactly once even under a race, the plaintext token never
touches the disk, and a revocation is a row — immediate, no cache to wait
out on this side.
"""

import re
import sqlite3
import threading
import time

import pytest

from gotg.tokens import (
    TOKEN_RE,
    Absent,
    Claimed,
    TokenStore,
    mint_token,
)


@pytest.fixture
def store(tmp_path):
    return TokenStore(db=tmp_path / "state" / "tokens.db")


def live(store):
    return [r for r in store.tokens() if r["revoked_at"] is None]


def test_a_minted_token_has_the_prefix_and_the_charset():
    token, display = mint_token()
    assert token.startswith("gotg_")
    assert TOKEN_RE.match(token)
    # The client's login guard; a token outside it corrupts the curl config.
    assert re.match(r"^[A-Za-z0-9._~+/=-]+$", token)
    assert display.startswith("gotg_")
    assert display.endswith(token[-4:])
    assert "…" in display


def test_an_invite_claims_into_a_working_token(store):
    code = store.mint_invite("alice-deck")
    assert code.startswith("gotgi_")

    name, token = store.claim(code)
    assert name == "alice-deck"
    assert token.startswith("gotg_")
    assert store.verify(token) == "alice-deck"


def _never_minted(store):
    return "gotgi_never_minted"


def _empty(store):
    return ""


def _near_miss(store):
    return store.mint_invite("kim")[:-1]


def _expired(store):
    return store.mint_invite("kim", ttl=-1)


def _expiring_this_instant(store):
    return store.mint_invite("kim", ttl=0)


def _already_claimed(store):
    code = store.mint_invite("kim")
    store.claim(code)
    return code


@pytest.mark.parametrize(
    ("arrange", "expected"),
    [
        (_never_minted, Absent),
        (_empty, Absent),
        (_near_miss, Absent),
        (_expired, Claimed),
        (_expiring_this_instant, Claimed),
        (_already_claimed, Claimed),
    ],
    ids=lambda p: getattr(p, "__name__", repr(p)).lstrip("_"),
)
def test_a_code_that_cannot_claim_says_whether_it_ever_existed(store, arrange, expected):
    assert store.claim(arrange(store)) is expected


def test_concurrent_claims_yield_exactly_one_token(store):
    code = store.mint_invite("dave")
    results = []
    barrier = threading.Barrier(2)

    def go():
        barrier.wait()
        results.append(store.claim(code))

    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    wins = [r for r in results if isinstance(r, tuple)]
    assert len(wins) == 1
    assert results.count(Claimed) == 1


def test_claiming_again_rotates_rather_than_erroring(store):
    first_code = store.mint_invite("erin")
    _, first_token = store.claim(first_code)

    second_code = store.mint_invite("erin")
    _, second_token = store.claim(second_code)

    assert store.verify(second_token) == "erin"
    assert store.verify(first_token) is None
    # Retired, not erased: the replaced token's history survives the rotation.
    rows = [r for r in store.tokens() if r["name"] == "erin"]
    assert len(rows) == 2
    assert sorted(r["revoked_at"] is None for r in rows) == [False, True]


def test_two_outstanding_invites_the_later_claim_wins_regardless_of_mint_order(store):
    first = store.mint_invite("erin")
    second = store.mint_invite("erin")
    _, from_first = store.claim(first)
    _, from_second = store.claim(second)
    assert store.verify(from_first) is None
    assert store.verify(from_second) == "erin"
    assert [r["name"] for r in live(store)] == ["erin"]

    third = store.mint_invite("erin")
    fourth = store.mint_invite("erin")
    _, from_fourth = store.claim(fourth)
    _, from_third = store.claim(third)
    assert store.verify(from_fourth) is None
    assert store.verify(from_third) == "erin"
    assert len(live(store)) == 1


def test_revocation_is_immediate(store):
    code = store.mint_invite("frank")
    _, token = store.claim(code)
    assert store.verify(token) == "frank"
    assert store.revoke("frank") is True
    assert store.verify(token) is None
    assert store.revoke("frank") is False


def test_revoking_closes_the_invite_that_would_mint_a_replacement(store):
    # A claim link goes astray: revoking must not leave it able to mint a new
    # token — which would retire the legitimate holder's as it went.
    code = store.mint_invite("frank")
    _, token = store.claim(code)
    leaked = store.mint_invite("frank")

    assert store.revoke("frank") is True
    assert store.claim(leaked) is Claimed
    assert store.verify(token) is None
    assert live(store) == []


def test_revoking_a_name_with_only_an_invite_still_closes_it(store):
    leaked = store.mint_invite("grace")
    assert store.revoke("grace") is True
    assert store.claim(leaked) is Claimed
    assert store.revoke("grace") is False


def test_revoking_one_name_leaves_another_name_s_invite_alone(store):
    mine = store.mint_invite("heidi")
    store.revoke("frank")
    assert store.claim(mine)[0] == "heidi"


@pytest.mark.parametrize(
    ("token_ttl", "advance", "verifies"),
    [
        (None, 0, True),
        (None, 10**9, True),
        (100, 99, True),
        (100, 100, False),  # the bound is closed: exactly expires_at refuses
        (100, 101, False),
        (0, 0, False),
        (-1, 0, False),
    ],
    ids=["no-ttl-now", "no-ttl-forever", "before", "at-the-second", "after", "zero-ttl", "negative-ttl"],
)
def test_token_expiry_is_a_closed_bound_at_the_second(store, monkeypatch, token_ttl, advance, verifies):
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now)
    code = store.mint_invite("kate", token_ttl=token_ttl)
    _, token = store.claim(code)
    monkeypatch.setattr(time, "time", lambda: now + advance)
    assert (store.verify(token) == "kate") is verifies


@pytest.mark.parametrize(
    "bogus",
    ["", "gotg_wrong", "Bearer gotg_x", "gotg_\xff\x80"],
    ids=["empty", "wrong", "with-scheme", "non-ascii"],
)
def test_a_token_that_was_never_minted_never_verifies(store, bogus):
    assert store.verify(bogus) is None


@pytest.mark.parametrize(
    "junk",
    ["", "x", "gotgi_", "gotgi_short", "gotg_" + "a" * 43, "gotgi_" + "a" * 51, "gotgi_" + "a" * 42 + "!"],
    ids=["empty", "letter", "prefix-only", "too-short", "token-not-code", "too-long", "bad-charset"],
)
def test_a_code_shaped_wrong_is_refused_without_the_write_lock(store, junk):
    # Held here, so a claim that reached the store at all would block: the
    # unauthenticated route must not queue behind real work.
    outcome = []
    with store._write_lock:
        worker = threading.Thread(target=lambda: outcome.append(store.claim(junk)))
        worker.start()
        worker.join(timeout=5)
        assert not worker.is_alive()
    assert outcome == [Absent]


def test_a_token_belongs_to_the_person_half_of_its_name(store):
    store.claim(store.mint_invite("daniel-desktop"))
    store.claim(store.mint_invite("daniel-deck"))
    store.claim(store.mint_invite("solo"))
    assert store.user_for("daniel-desktop") == "daniel"
    assert store.user_for("daniel-deck") == "daniel"
    assert store.user_for("solo") == "solo"
    assert store.user_for("never-minted") is None


def test_an_invite_can_name_its_user_explicitly(store):
    # "mary-jane-deck" would otherwise land in user "mary".
    store.claim(store.mint_invite("mary-jane-deck", user="mary-jane"))
    assert store.user_for("mary-jane-deck") == "mary-jane"


@pytest.mark.parametrize("bad", ["admin", "legacy", "indexer", "UPPER", "a" * 33, "-lead"])
def test_a_reserved_or_misshapen_user_is_refused(store, bad):
    with pytest.raises(ValueError):
        store.mint_invite("kim-deck", user=bad)


def test_a_pre_user_store_backfills_users_from_names(tmp_path):
    db = tmp_path / "tokens.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE tokens (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
            display TEXT NOT NULL, created_at INTEGER NOT NULL,
            expires_at INTEGER, revoked_at INTEGER, last_used_at INTEGER
        );
        CREATE UNIQUE INDEX tokens_live_name ON tokens(name) WHERE revoked_at IS NULL;
        CREATE TABLE invites (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, code_hash TEXT NOT NULL UNIQUE,
            created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
            token_ttl INTEGER, claimed_at INTEGER
        );
        INSERT INTO tokens (name, token_hash, display, created_at)
            VALUES ('daniel-desktop', 'aaaa', 'gotg_…aaaa', 1);
        """
    )
    conn.commit()
    conn.close()

    store = TokenStore(db=db)
    assert store.user_for("daniel-desktop") == "daniel"
    rows = {r["name"]: r["user"] for r in store.tokens()}
    assert rows["daniel-desktop"] == "daniel"


def test_a_claim_code_is_not_itself_a_token(store):
    code = store.mint_invite("lena")
    store.claim(code)
    assert store.verify(code) is None


def test_the_plaintext_token_never_touches_the_disk(store, tmp_path):
    code = store.mint_invite("henry")
    _, token = store.claim(code)
    store.verify(token)

    on_disk = b""
    for f in (tmp_path / "state").glob("tokens.db*"):
        on_disk += f.read_bytes()
    assert token.encode() not in on_disk
    assert code.encode() not in on_disk


def test_the_listing_shows_display_never_the_hash_material(store):
    code = store.mint_invite("iris")
    _, token = store.claim(code)

    rows = store.tokens()
    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "iris"
    assert row["display"].endswith(token[-4:])
    assert token not in str(row)


@pytest.mark.parametrize(
    "bad",
    ["", "Iris", "a b", "../x", "-lead", "_lead", ".dot", "a\nb", "café", "x" * 33, "legacy", "indexer", "admin"],
    ids=[
        "empty",
        "upper",
        "space",
        "traversal",
        "dash-lead",
        "under-lead",
        "dot-lead",
        "newline",
        "unicode",
        "33-chars",
        "legacy",
        "indexer",
        "admin",
    ],
)
def test_a_bad_name_is_refused_at_invite_time(store, bad):
    with pytest.raises(ValueError):
        store.mint_invite(bad)


@pytest.mark.parametrize(
    "good",
    ["a", "9lives", "alice-deck", "a_b", "trailing-", "a" * 32],
    ids=["one-char", "digit-lead", "person-device", "underscore", "trailing-dash", "32-chars"],
)
def test_a_usable_name_mints(store, good):
    assert store.mint_invite(good).startswith("gotgi_")


def test_last_used_writes_are_throttled(store, monkeypatch):
    code = store.mint_invite("judy")
    _, token = store.claim(code)

    writes = []
    original = store._touch_last_used

    def counting(name, now):
        writes.append(now)
        return original(name, now)

    monkeypatch.setattr(store, "_touch_last_used", counting)

    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now)
    for _ in range(5):
        assert store.verify(token) == "judy"
    assert len(writes) <= 1

    monkeypatch.setattr(time, "time", lambda: now + 120)
    assert store.verify(token) == "judy"
    assert len(writes) <= 2


def test_reads_interleave_with_rotations_without_busy_errors(store):
    # WAL smoke: fresh read connections racing the single write connection
    # must be absorbed by busy_timeout, never surfaced.
    codes = [store.mint_invite(f"user-{i}") for i in range(4)]
    tokens = [store.claim(c)[1] for c in codes]
    errors: list[Exception] = []
    stop = threading.Event()

    def hammer(token):
        while not stop.is_set():
            try:
                store.verify(token)
            except Exception as error:  # noqa: BLE001 — "no exception" is the assertion
                errors.append(error)
                return

    readers = [threading.Thread(target=hammer, args=(t,)) for t in tokens]
    for r in readers:
        r.start()
    try:
        for _ in range(10):
            for i in range(4):
                store.claim(store.mint_invite(f"user-{i}"))
    finally:
        stop.set()
        for r in readers:
            r.join()
    assert errors == []


def test_a_v1_store_migrates_without_losing_rows(tmp_path):
    db = tmp_path / "state" / "tokens.db"
    db.parent.mkdir(parents=True)
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE tokens (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            token_hash TEXT NOT NULL UNIQUE,
            display TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            expires_at INTEGER,
            revoked_at INTEGER,
            last_used_at INTEGER
        );
        INSERT INTO tokens (name, token_hash, display, created_at)
        VALUES ('old-timer', 'abc123', 'gotg_…1234', 1700000000);
        """
    )
    conn.commit()
    conn.close()

    store = TokenStore(db=db)
    rows = store.tokens()
    assert [r["name"] for r in rows] == ["old-timer"]
    # And the rebuilt shape rotates without erasing.
    code = store.mint_invite("old-timer")
    store.claim(code)
    assert len([r for r in store.tokens() if r["name"] == "old-timer"]) == 2
