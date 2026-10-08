"""Route steps whose decision was only reached through a long path before.

`_handle` read and checked Content-Length inline, `_saves` mixed its 404 with
its 405, and `_admin`'s mint validated and minted in one block. Each of those
is now one step, and each answer is pinned here over a real socket.
"""

from __future__ import annotations

import socket

import pytest
from harness import CLIENT_TOKEN, serve

from gotg.saves import SavesStore
from gotg.service.config import Config
from gotg.tokens import TokenStore

ADMIN = "admin-token"


def raw(port: int, request: bytes) -> bytes:
    """One request, as written, and everything the service answers before closing."""
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(request)
        chunks = []
        while chunk := sock.recv(65536):
            chunks.append(chunk)
    return b"".join(chunks)


def with_length(port: int, length: str, target: str = "/igdb/games") -> bytes:
    return raw(
        port,
        (
            f"POST {target} HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {CLIENT_TOKEN}\r\n"
            f"Content-Length: {length}\r\n\r\n"
        ).encode(),
    )


@pytest.mark.parametrize(
    ("length", "status", "message"),
    [
        ("abc", b"400", b"malformed Content-Length"),
        ("-5", b"400", b"negative Content-Length"),
        (str(64 * 1024 + 1), b"413", b"request body too large"),
    ],
)
def test_a_content_length_that_cannot_be_read_is_refused_before_the_body(length, status, message):
    with serve(Config(token=CLIENT_TOKEN)) as server:
        reply = with_length(server.port, length)
    assert reply.startswith(b"HTTP/1.1 " + status)
    assert message in reply
    # `raw` only returns once the service has hung up: the request asked for
    # keep-alive, so the refusal itself closed the connection.


def test_a_save_may_be_bigger_than_the_small_cap_but_not_than_the_stores(tmp_path):
    store = SavesStore(root=tmp_path / "saves", keep=3, max_bytes=200_000)
    with serve(Config(token=CLIENT_TOKEN), store) as server:
        ok = server.call("PUT", "/saves/env-n64", body=b"x" * 100_000)
        too_big = with_length(server.port, "200001", "/saves/env-n64").split(b"\r\n")[0]
    assert ok[0] == 200
    # POST is not a PUT: it gets the small cap, however big the store allows.
    assert too_big.startswith(b"HTTP/1.1 413")


def test_a_saves_path_that_names_nothing_is_404_before_the_method_is_judged(tmp_path):
    store = SavesStore(root=tmp_path / "saves", keep=3, max_bytes=100_000)
    with serve(Config(token=CLIENT_TOKEN), store) as server:
        assert server.call("POST", "/saves/env-n64/nothing")[0] == 404
        assert server.call("POST", "/saves/env-n64/history")[0] == 405
        assert server.call("DELETE", "/saves/env-n64/gen/1")[0] == 405
        assert server.call("DELETE", "/saves/env-n64")[0] == 405


def test_a_generation_that_is_not_kept_is_named_as_it_was_asked_for(tmp_path):
    store = SavesStore(root=tmp_path / "saves", keep=3, max_bytes=100_000)
    with serve(Config(token=CLIENT_TOKEN), store) as server:
        status, _, body = server.call("GET", "/saves/env-n64/gen/007")
    assert status == 404
    assert b"generation 007 of env-n64 is not kept" in body


def test_a_device_header_is_stored_printable_and_short(tmp_path):
    store = SavesStore(root=tmp_path / "saves", keep=3, max_bytes=100_000)
    with serve(Config(token=CLIENT_TOKEN), store) as server:
        status, _, body = server.call(
            "PUT", "/saves/env-n64", body=b"bundle", headers={"X-Gotg-Device": "deck-" + "z" * 60}
        )
    assert status == 200
    assert b"deck-" in body
    assert b"z" * 28 not in body  # 32 characters in all


def test_the_pre_auth_routes_answer_without_a_token_and_close_the_connection():
    with serve(Config(token=CLIENT_TOKEN)) as server:
        health = raw(server.port, b"GET /healthz HTTP/1.1\r\nHost: x\r\n\r\n")
        refused = raw(server.port, b"GET /saves/env-n64 HTTP/1.1\r\nHost: x\r\n\r\n")
    assert health.startswith(b"HTTP/1.1 200")
    assert refused.startswith(b"HTTP/1.1 401")


@pytest.fixture
def minting(tmp_path):
    store = TokenStore(tmp_path / "tokens.db")
    with serve(Config(token=CLIENT_TOKEN, admin_token=ADMIN), None, None, None, token_store=store) as server:
        yield server


@pytest.mark.parametrize(
    ("body", "status"),
    [
        (b'{"name": "daniel"}', 200),
        (b"not json", 400),
        (b'{"name": "d", "ttl_days": 0}', 400),
        (b'{"name": "d", "ttl_days": "x"}', 400),
        (b'{"name": ""}', 400),
    ],
)
def test_minting_an_invite_validates_before_it_mints(minting, body, status):
    assert minting.call("POST", "/admin/invites", body=body, token=ADMIN)[0] == status


def test_minting_is_a_post_and_listing_is_a_get(minting):
    assert minting.call("PUT", "/admin/invites", body=b"{}", token=ADMIN)[0] == 405
    assert minting.call("GET", "/admin/invites", token=ADMIN)[0] == 200
    assert minting.call("POST", "/admin/tokens", token=ADMIN)[0] == 405
    assert minting.call("GET", "/admin/tokens/someone", token=ADMIN)[0] == 405
    assert minting.call("DELETE", "/admin/tokens/nobody", token=ADMIN)[0] == 404
    assert minting.call("GET", "/admin/elsewhere", token=ADMIN)[0] == 404


def test_a_saves_path_urlsplit_refuses_is_a_400_not_a_dropped_connection(tmp_path):
    # urlsplit raises on an unbalanced "[": the catalog caught it, the saves
    # did not, and a valid token plus /saves//[x killed the handler thread and
    # closed the socket with no status at all.
    store = SavesStore(root=tmp_path / "saves", keep=3, max_bytes=100_000)
    with serve(Config(token=CLIENT_TOKEN), store) as server:
        status, _, body = server.call("GET", "/saves//[x")
    assert status == 400
    assert b"malformed request path" in body


def test_putting_to_meta_does_not_store_a_save(tmp_path):
    # /saves/<attr>/meta is read-only: a PUT there used to fall through to
    # the head's PUT and store the body as a generation.
    store = SavesStore(root=tmp_path / "saves", keep=3, max_bytes=100_000)
    with serve(Config(token=CLIENT_TOKEN), store) as server:
        assert server.call("PUT", "/saves/env-n64/meta", body=b"bundle")[0] == 405
        assert server.call("GET", "/saves/env-n64")[0] == 404


def test_a_ttl_past_float_range_is_a_400_not_an_overflow(minting):
    body = b'{"name": "d", "ttl_days": 1' + b"0" * 400 + b"}"
    assert minting.call("POST", "/admin/invites", body=body, token=ADMIN)[0] == 400


@pytest.mark.parametrize("target", ["/games//[x", "/files//[x", "/art//[x", "/catalog//[x", "/saves//[x"])
def test_every_route_answers_a_path_urlsplit_refuses_with_a_400(tmp_path, target):
    # urlsplit raises on an unbalanced "[". The catalog and the saves caught
    # it; the games, the files and the art let it kill the handler thread and
    # close the connection with no status. One helper answers for all five.
    from gotg.catalog import CatalogStore
    from gotg.service.artcache import ArtCache

    catalog = CatalogStore(db=tmp_path / "state" / "catalog.db", roots=[tmp_path / "library"])
    store = SavesStore(root=tmp_path / "saves", keep=3, max_bytes=100_000)
    art = ArtCache(tmp_path / "art")
    config = Config(token=CLIENT_TOKEN, art_dir=str(art.root))
    with serve(config, store, catalog, files_dir=tmp_path / "library", art=art) as server:
        status, _, body = server.call("GET", target)
    assert status == 400
    assert b"malformed request path" in body
