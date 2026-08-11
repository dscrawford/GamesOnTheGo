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
import threading
import urllib.error
import urllib.request

import pytest

from gotg_proxy.app import Config, make_server
from gotg_proxy.saves import SavesStore
from test_proxy import free_port


@pytest.fixture
def store(tmp_path):
    return SavesStore(root=tmp_path / "saves", keep=3, max_bytes=100_000)


@pytest.fixture
def service(store):
    config = Config(token="client-token")
    server = make_server("127.0.0.1", free_port(), config, store)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def call(
    url: str,
    *,
    method: str = "GET",
    token: str | None = "client-token",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
):
    request = urllib.request.Request(url, data=body, method=method)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as error:
        return error.code, error.read(), dict(error.headers or {})


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
    server = make_server("127.0.0.1", free_port(), Config(token="client-token"), None)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        status, body, _ = call(f"http://127.0.0.1:{server.server_port}/saves/env-n64")
        assert status == 503
        assert b"no saves store" in body
    finally:
        server.shutdown()


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
    assert len(list((store.root / "env-n64" / "gen").iterdir())) == 1


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
    assert len(list((store.root / "env-n64" / "gen").iterdir())) == 1


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
    assert len(list((store.root / "env-n64" / "gen").iterdir())) == 2


# --- retention --------------------------------------------------------------


def test_only_the_newest_three_generations_survive(service, store):
    parent = ""
    for n in range(5):
        _, meta, _ = push(service, f"save {n}".encode(), parent=parent)
        parent = json.loads(meta)["hash"]

    names = sorted(p.name for p in (store.root / "env-n64" / "gen").iterdir())
    assert len(names) == 3
    assert names[0].startswith("000003-")
    # The pointer names the newest, which is untouched.
    status, body, _ = call(f"{service}/saves/env-n64")
    assert status == 200
    assert body == b"save 4"


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
    attr_dir = store.root / "env-n64"
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
