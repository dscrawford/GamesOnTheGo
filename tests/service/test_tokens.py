"""The token store: minted once, hashed at rest, claimed exactly once.

The properties that matter are the ones an attacker or a crash would test:
a claim code works exactly once even under a race, the plaintext token never
touches the disk, and a revocation is a row — immediate, no cache to wait
out on this side.
"""

import re
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


def test_a_claim_code_works_exactly_once(store):
    code = store.mint_invite("bob")
    store.claim(code)
    assert store.claim(code) is Claimed


def test_an_unknown_code_is_absent_not_claimed(store):
    assert store.claim("gotgi_nonsense") is Absent


def test_an_expired_invite_is_claimed_never_a_token(store):
    code = store.mint_invite("carol", ttl=-1)
    assert store.claim(code) is Claimed


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


def test_revocation_is_immediate(store):
    code = store.mint_invite("frank")
    _, token = store.claim(code)
    assert store.verify(token) == "frank"
    assert store.revoke("frank") is True
    assert store.verify(token) is None
    assert store.revoke("frank") is False


def test_an_expired_token_stops_verifying(store):
    code = store.mint_invite("grace", token_ttl=-1)
    _, token = store.claim(code)
    assert store.verify(token) is None


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


def test_a_bad_name_is_refused_at_invite_time(store):
    for bad in ["", "Iris", "a b", "../x", "legacy", "indexer", "admin", "x" * 33]:
        with pytest.raises(ValueError):
            store.mint_invite(bad)


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
