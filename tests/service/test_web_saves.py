"""`GET /saves`: the listing a browser draws, with conflict information."""

from __future__ import annotations

import hashlib
import json

import pytest
from harness import serve

from gotg.saves import SavesStore
from gotg.service.app import Config


@pytest.fixture
def service(tmp_path):
    store = SavesStore(root=tmp_path / "saves", keep=5, max_bytes=100_000)
    with serve(Config(token="client-token"), store) as server:
        yield server


def digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def push(service, body, parent="", force=False, attr="env-n64", device="deck"):
    status, _, answer = service.call(
        "PUT",
        f"/saves/{attr}" + ("?force=1" if force else ""),
        body=body,
        headers={"X-Gotg-Parent": parent, "X-Gotg-Device": device},
    )
    return status, json.loads(answer)


def listing(service, query=""):
    status, _, body = service.call("GET", f"/saves{query}")
    assert status == 200
    return {s["attr"]: s for s in json.loads(body)["saves"]}


def test_empty_listing(service):
    status, _, body = service.call("GET", "/saves")
    assert status == 200 and json.loads(body) == {"version": 1, "saves": []}


def test_listing_shows_head_devices_and_generations(service):
    push(service, b"one", device="deck")
    push(service, b"two", parent=digest(b"one"), device="desktop")
    push(service, b"s", attr="env-snes")
    saves = listing(service)
    assert sorted(saves) == ["env-n64", "env-snes"]
    n64 = saves["env-n64"]
    assert n64["head"]["generation"] == 2 and n64["head"]["hash"] == digest(b"two")
    assert n64["generations"] == 2 and n64["devices"] == ["deck", "desktop"]
    assert n64["overwritten_generations"] == [] and n64["has_conflict"] is False
    assert n64["sync"] is None


def test_forced_push_marks_the_overwritten_generation(service):
    push(service, b"one")
    status, _ = push(service, b"fork", parent="", force=True, device="desktop")
    assert status == 200
    entry = listing(service)["env-n64"]
    assert entry["overwritten_generations"] == [1] and entry["has_conflict"] is True


def test_known_hash_says_where_the_caller_stands(service):
    push(service, b"one")
    push(service, b"two", parent=digest(b"one"))
    known = lambda h: listing(service, f"?known=env-n64:{h}")["env-n64"]  # noqa: E731
    assert known(digest(b"two"))["sync"] == "in_sync"
    behind = known(digest(b"one"))
    assert behind["sync"] == "behind" and behind["has_conflict"] is False
    diverged = known(digest(b"local-only"))
    assert diverged["sync"] == "diverged" and diverged["has_conflict"] is True


def test_listing_is_per_user_and_needs_auth(service):
    push(service, b"one")
    assert service.call("GET", "/saves", token=None)[0] == 401
    assert service.call("POST", "/saves", body=b"x")[0] == 405


def test_listing_without_store_is_503(tmp_path):
    with serve(Config(token="client-token")) as server:
        assert server.call("GET", "/saves")[0] == 503
