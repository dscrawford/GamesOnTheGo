"""The saves store, over the wire.

The property that matters most is the one the old WebDAV remote could not
have: two machines cannot both advance the head. A push carries the hash of
the generation it descends from, and the store answers 409 — with what is
actually there — when that is not the head any more. Everything else is
bookkeeping: retention, idempotence, and refusing shapes that should never
become paths.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from harness import send, serve

from gotg.saves import SavesStore
from gotg.service.app import Config
from gotg.tokens import TokenStore


@pytest.fixture
def store(tmp_path):
    return SavesStore(root=tmp_path / "saves", keep=3, max_bytes=100_000)


@pytest.fixture
def service(store):
    config = Config(token="client-token")
    with serve(config, store) as server:
        yield server.url


def call(
    url: str,
    *,
    method: str = "GET",
    token: str | None = "client-token",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
):
    status, response_headers, response_body = send(url, method=method, token=token, body=body, headers=headers)
    return status, response_body, response_headers


def push(service, body: bytes, parent: str = "", force: bool = False, attr: str = "env-n64"):
    query = "?force=1" if force else ""
    return call(
        f"{service}/saves/{attr}{query}",
        method="PUT",
        body=body,
        headers={"X-Gotg-Parent": parent, "X-Gotg-Device": "aaaa1111"},
    )


# --- who is allowed in ------------------------------------------------------


def test_the_store_requires_the_same_token_as_everything_else(service):
    status, _, _ = call(f"{service}/saves/env-n64", token=None)
    assert status == 401
    status, _, _ = push(service, b"bundle")
    assert status == 200


def test_a_service_with_no_store_says_so():
    with serve(Config(token="client-token"), None) as server:
        status, body, _ = call(f"{server.url}/saves/env-n64")
        assert status == 503
        assert b"no saves store" in body


# --- the two verbs ----------------------------------------------------------


def test_what_is_saved_is_what_is_retrieved(service):
    bundle = b"the save set, whole"
    status, meta, _ = push(service, bundle)
    assert status == 200
    published = json.loads(meta)
    assert published["generation"] == 1
    assert published["hash"] == hashlib.sha256(bundle).hexdigest()
    assert published["device"] == "aaaa1111"

    status, body, headers = call(f"{service}/saves/env-n64")
    assert status == 200
    assert body == bundle
    assert headers["X-Gotg-Generation"] == "1"
    assert headers["X-Gotg-Hash"] == published["hash"]


def test_every_way_to_read_a_bundle_says_what_it_is_and_how_long(service):
    # The head and a kept generation are two routes to the same reply shape:
    # zstd bytes, their exact length, and the generation and hash they are.
    metas = push_chain(service, 2)
    for path, meta in ((f"{service}/saves/env-n64", metas[1]), (f"{service}/saves/env-n64/gen/1", metas[0])):
        status, body, headers = call(path)
        assert status == 200
        assert headers["Content-Type"] == "application/zstd"
        assert headers["Content-Length"] == str(len(body))
        assert headers["X-Gotg-Generation"] == str(meta["generation"])
        assert headers["X-Gotg-Hash"] == meta["hash"]


def test_the_json_routes_say_so_and_the_404s_carry_an_error(service):
    push_chain(service, 1)
    for suffix in ("/meta", "/history"):
        status, body, headers = call(f"{service}/saves/env-n64{suffix}")
        assert status == 200
        assert headers["Content-Type"] == "application/json"
        assert headers["Content-Length"] == str(len(body))
        json.loads(body)
    status, body, headers = call(f"{service}/saves/env-none")
    assert status == 404
    assert headers["Content-Type"] == "application/json"
    assert "env-none" in json.loads(body)["error"]


def test_retrieving_before_any_push_is_a_404_not_an_error(service):
    status, _, _ = call(f"{service}/saves/env-n64")
    assert status == 404
    status, _, _ = call(f"{service}/saves/env-n64/meta")
    assert status == 404


def test_meta_says_what_is_current_without_moving_the_bytes(service):
    push(service, b"bundle")
    status, body, _ = call(f"{service}/saves/env-n64/meta")
    assert status == 200
    meta = json.loads(body)
    assert meta["generation"] == 1
    assert meta["size"] == len(b"bundle")


def test_pushing_the_same_bytes_twice_changes_nothing(service, store):
    push(service, b"bundle")
    status, meta, _ = push(service, b"bundle", parent="anything-at-all")
    # A success, not a conflict: the store already holds exactly this.
    assert status == 200
    assert json.loads(meta)["generation"] == 1
    assert len(list((store.root / "legacy" / "env-n64" / "gen").iterdir())) == 1


# --- which side wins --------------------------------------------------------


def test_a_push_from_a_stale_parent_is_refused_with_the_head(service, store):
    _, first, _ = push(service, b"from machine a")
    head = json.loads(first)["hash"]

    status, body, _ = push(service, b"from machine b", parent="not-the-head")
    assert status == 409
    # The refusal carries what is actually there, which is everything a client
    # needs to explain the choice to a person.
    assert json.loads(body)["hash"] == head
    # And nothing was written.
    assert len(list((store.root / "legacy" / "env-n64" / "gen").iterdir())) == 1


def test_a_push_from_the_head_advances_it(service):
    _, first, _ = push(service, b"one")
    status, second, _ = push(service, b"two", parent=json.loads(first)["hash"])
    assert status == 200
    meta = json.loads(second)
    assert meta["generation"] == 2
    assert meta["parent"] == json.loads(first)["hash"]


def test_a_forced_push_wins_and_the_loser_stays_on_disk(service, store):
    push(service, b"from machine a")
    status, meta, _ = push(service, b"from machine b", parent="stale", force=True)
    assert status == 200
    assert json.loads(meta)["generation"] == 2
    # Both generations are there: the loser waits for retention, not deletion.
    assert len(list((store.root / "legacy" / "env-n64" / "gen").iterdir())) == 2


# --- retention --------------------------------------------------------------


def test_only_the_newest_three_generations_survive(service, store):
    parent = ""
    for n in range(5):
        _, meta, _ = push(service, f"save {n}".encode(), parent=parent)
        parent = json.loads(meta)["hash"]

    names = sorted(p.name for p in (store.root / "legacy" / "env-n64" / "gen").iterdir())
    assert len(names) == 3
    assert names[0].startswith("000003-")
    # The pointer names the newest, which is untouched.
    status, body, _ = call(f"{service}/saves/env-n64")
    assert status == 200
    assert body == b"save 4"


# --- history ----------------------------------------------------------------
#
# A person picking a save to go back to needs the generations that are kept,
# who wrote each and when, and the bytes of the one they pick. The head is
# only the newest of them.


def push_chain(service, count: int, attr: str = "env-n64") -> list[dict]:
    metas, parent = [], ""
    for n in range(count):
        _, meta, _ = push(service, f"save {n}".encode(), parent=parent, attr=attr)
        metas.append(json.loads(meta))
        parent = metas[-1]["hash"]
    return metas


def test_history_lists_the_kept_generations_newest_first(service):
    metas = push_chain(service, 2)
    status, body, _ = call(f"{service}/saves/env-n64/history")
    assert status == 200
    history = json.loads(body)["generations"]
    assert [g["generation"] for g in history] == [2, 1]
    assert history[0]["hash"] == metas[1]["hash"]
    assert history[0]["device"] == "aaaa1111"
    assert history[0]["written_at"] == metas[1]["written_at"]
    assert history[0]["size"] == len(b"save 1")
    assert [g["current"] for g in history] == [True, False]


def test_history_before_any_push_is_empty_not_an_error(service):
    status, body, _ = call(f"{service}/saves/env-n64/history")
    assert status == 200
    assert json.loads(body)["generations"] == []


def test_history_drops_what_retention_dropped(service):
    push_chain(service, 5)
    history = json.loads(call(f"{service}/saves/env-n64/history")[1])["generations"]
    assert [g["generation"] for g in history] == [5, 4, 3]


def test_an_older_generation_is_fetched_by_number(service):
    metas = push_chain(service, 3)
    status, body, headers = call(f"{service}/saves/env-n64/gen/2")
    assert status == 200
    assert body == b"save 1"
    assert headers["X-Gotg-Generation"] == "2"
    assert headers["X-Gotg-Hash"] == metas[1]["hash"]


def test_a_generation_retention_dropped_is_a_404(service):
    push_chain(service, 5)
    assert call(f"{service}/saves/env-n64/gen/1")[0] == 404
    assert call(f"{service}/saves/env-n64/gen/9")[0] == 404


def test_a_generation_that_is_not_a_number_is_refused(service):
    push_chain(service, 1)
    for gen in ("x", "-1", "1.5", "000001-abc"):
        assert call(f"{service}/saves/env-n64/gen/{gen}")[0] in (400, 404), gen


def test_a_generation_pushed_before_history_existed_is_still_listed(service, store):
    # Bundles from before the store kept a record of each one: their number
    # and hash are in their name and their size on disk, and who wrote them
    # is not known -- which is said rather than guessed.
    push_chain(service, 2)
    for record in (store.root / "legacy" / "env-n64" / "history").iterdir():
        record.unlink()
    history = json.loads(call(f"{service}/saves/env-n64/history")[1])["generations"]
    assert [g["generation"] for g in history] == [2, 1]
    assert history[1]["device"] == ""
    assert history[1]["hash"] == hashlib.sha256(b"save 0").hexdigest()
    assert history[1]["size"] == len(b"save 0")
    assert history[1]["written_at"].endswith("Z")


def test_history_is_namespaced_by_user(tmp_path, store):
    tokens = TokenStore(db=tmp_path / "tokens.db")
    daniel = tokens.claim(tokens.mint_invite("daniel-desktop"))[1]
    john = tokens.claim(tokens.mint_invite("john-deck"))[1]
    with serve(Config(token="client-token"), store, token_store=tokens) as server:
        base = server.url
        call(f"{base}/saves/env-n64", method="PUT", body=b"d", token=daniel, headers={"X-Gotg-Parent": ""})
        assert json.loads(call(f"{base}/saves/env-n64/history", token=john)[1])["generations"] == []
        assert call(f"{base}/saves/env-n64/gen/1", token=john)[0] == 404


def test_ten_generations_are_kept_by_default(tmp_path):
    # Three was enough to recover from a bad sync; it is not enough to pick a
    # save to go back to.
    assert SavesStore(root=tmp_path).keep == 10


# --- refusing shapes --------------------------------------------------------


def test_an_attr_that_is_not_an_environment_name_is_refused(service):
    for attr in ("env-", "notanenv", "env-UPPER", "env-a%2Fb"):
        status, _, _ = call(f"{service}/saves/{attr}", method="PUT", body=b"x")
        assert status in (400, 404), attr


def test_a_body_over_the_cap_is_refused_before_it_is_held(service):
    status, body, _ = push(service, b"x" * 200_000)
    assert status == 413
    assert b"too large" in body


def test_a_push_with_no_body_is_refused(service):
    status, _, _ = call(f"{service}/saves/env-n64", method="PUT", body=b"")
    assert status == 400


def test_a_tampered_pointer_cannot_read_outside_the_store(service, store):
    # What an edited disk would mean: a pointer naming a path instead of a
    # generation. The store must refuse to follow it anywhere.
    attr_dir = store.root / "legacy" / "env-n64"
    attr_dir.mkdir(parents=True)
    (attr_dir / "current.json").write_text(
        json.dumps({"version": 1, "generation": 1, "hash": "x", "bundle": "../../../etc/passwd"})
    )
    status, body, _ = call(f"{service}/saves/env-n64")
    assert status == 500
    assert b"passwd" not in body or b"bundle it should not" in body


def test_the_device_name_is_made_printable_and_short(service):
    # \r\n cannot even be sent — urllib refuses to build the header — so what
    # is left to sanitise is the control characters that are legal on the wire,
    # and length, since the value is stored and echoed back in metadata.
    status, meta, _ = call(
        f"{service}/saves/env-n64",
        method="PUT",
        body=b"bundle",
        headers={"X-Gotg-Parent": "", "X-Gotg-Device": "evil\tdevice\x7f" + "x" * 100},
    )
    assert status == 200
    device = json.loads(meta)["device"]
    assert "\t" not in device
    assert "\x7f" not in device
    assert len(device) <= 32


def test_saves_are_namespaced_by_user_not_shared(tmp_path, store):
    # Two people, one attr: each sees only their own head. The user comes
    # from the token, never the path, so isolation needs no client change.
    tokens = TokenStore(db=tmp_path / "tokens.db")
    daniel = tokens.claim(tokens.mint_invite("daniel-desktop"))[1]
    john = tokens.claim(tokens.mint_invite("john-deck"))[1]
    config = Config(token="client-token")
    with serve(config, store, token_store=tokens) as server:
        base = server.url
        headers = {"X-Gotg-Parent": ""}
        assert (
            call(f"{base}/saves/env-n64", method="PUT", body=b"daniel bytes", token=daniel, headers=headers)[0] == 200
        )
        assert call(f"{base}/saves/env-n64/meta", token=john)[0] == 404
        assert call(f"{base}/saves/env-n64", method="PUT", body=b"john bytes", token=john, headers=headers)[0] == 200
        assert call(f"{base}/saves/env-n64", token=daniel)[1] == b"daniel bytes"
        assert call(f"{base}/saves/env-n64", token=john)[1] == b"john bytes"
        assert (store.root / "daniel" / "env-n64" / "current.json").exists()
        assert (store.root / "john" / "env-n64" / "current.json").exists()


def test_two_devices_of_one_user_share_their_saves(tmp_path, store):
    tokens = TokenStore(db=tmp_path / "tokens.db")
    desktop = tokens.claim(tokens.mint_invite("daniel-desktop"))[1]
    deck = tokens.claim(tokens.mint_invite("daniel-deck"))[1]
    config = Config(token="client-token")
    with serve(config, store, token_store=tokens) as server:
        base = server.url
        assert (
            call(
                f"{base}/saves/env-n64",
                method="PUT",
                body=b"from the desktop",
                token=desktop,
                headers={"X-Gotg-Parent": ""},
            )[0]
            == 200
        )
        assert call(f"{base}/saves/env-n64", token=deck)[1] == b"from the desktop"


def test_the_legacy_token_lands_in_the_configured_user(tmp_path):
    # GOTG_LEGACY_USER=daniel maps break-glass pushes into the saves that
    # were daniel's all along, instead of a parallel "legacy" copy.
    store = SavesStore(root=tmp_path / "saves", keep=3, max_bytes=100_000)
    config = Config(token="client-token", legacy_user="daniel")
    with serve(config, store) as server:
        base = server.url
        assert call(f"{base}/saves/env-n64", method="PUT", body=b"b", headers={"X-Gotg-Parent": ""})[0] == 200
        assert (store.root / "daniel" / "env-n64" / "current.json").exists()
