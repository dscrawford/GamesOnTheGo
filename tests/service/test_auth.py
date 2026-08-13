"""Per-person tokens on the wire: claim, whoami, admin, introspection.

Two servers stand in for the two pods where it matters: the api one holds
the token store, the library one holds only a legacy token and an auth_url —
the introspection path is exercised against a real second server, not a
stub, because the cache and its failure modes are the feature.
"""

import json
import socket
import time
import urllib.error
import urllib.request

import pytest
from test_proxy import free_port

from gotg.saves import SavesStore
from gotg.service.app import Config, make_server
from gotg.tokens import TokenStore

LEGACY = "legacy-token"
ADMIN = "admin-token"
INDEX = "index-token"


@pytest.fixture
def token_store(tmp_path):
    return TokenStore(db=tmp_path / "state" / "tokens.db")


@pytest.fixture
def service(tmp_path, token_store):
    import threading

    config = Config(token=LEGACY, admin_token=ADMIN, index_token=INDEX)
    store = SavesStore(root=tmp_path / "saves", keep=3, max_bytes=100_000)
    server = make_server("127.0.0.1", free_port(), config, store, token_store=token_store)
    threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def call(url, method="GET", token=None, body=None):
    request = urllib.request.Request(url, method=method, data=body)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def claim(url, code):
    return call(f"{url}/claim/{code}", method="POST")


# --- the claim route ---------------------------------------------------------


def test_an_invite_claims_into_a_token_that_authenticates(service, token_store):
    code = token_store.mint_invite("alice-deck")
    status, body = claim(service, code)
    assert status == 200
    got = json.loads(body)
    assert got["name"] == "alice-deck"
    assert got["token"].startswith("gotg_")

    status, body = call(f"{service}/auth/whoami", token=got["token"])
    assert status == 200
    assert json.loads(body)["name"] == "alice-deck"

    status, _ = call(f"{service}/saves/env-snes/meta", token=got["token"])
    assert status == 404  # authenticated; nothing pushed yet


def test_a_claim_code_answers_410_when_reused(service, token_store):
    code = token_store.mint_invite("bob")
    assert claim(service, code)[0] == 200
    assert claim(service, code)[0] == 410


def test_an_unknown_claim_code_is_404(service):
    assert claim(service, "gotgi_never_minted")[0] == 404


@pytest.mark.parametrize(
    ("suffix", "expected"),
    [
        ("{code}", 200),
        ("{code}?utm_source=chat", 200),  # query junk is stripped before hashing
        ("{code}/", 404),  # a slash is part of no code; the client strips it, the service must not 500
        ("{code}/extra", 404),
        ("", 404),
        ("%2e%2e/admin", 404),
    ],
    ids=["plain", "query-junk", "trailing-slash", "slash-extra", "empty-code", "encoded-climb"],
)
def test_the_claim_route_survives_url_junk(service, token_store, suffix, expected):
    code = token_store.mint_invite("zoe")
    assert call(f"{service}/claim/" + suffix.format(code=code), method="POST")[0] == expected


def test_claim_is_post_only(service, token_store):
    code = token_store.mint_invite("carol")
    status, _ = call(f"{service}/claim/{code}")
    assert status == 401


def test_claim_never_reads_the_body_it_is_promised(service, token_store):
    # A pre-auth route must not be made to buffer an attacker-sized body: the
    # reply comes before the read, and the connection closes.
    code = token_store.mint_invite("dave")
    host, port = service.removeprefix("http://").split(":")
    with socket.create_connection((host, int(port)), timeout=5) as sock:
        sock.sendall(f"POST /claim/{code} HTTP/1.1\r\nHost: {host}\r\nContent-Length: 1000000000\r\n\r\n".encode())
        sock.settimeout(5)
        # The exemption closes the connection, so EOF bounds the read — which
        # is itself the assertion: a server waiting on the promised gigabyte
        # would time out here instead.
        reply = b""
        while chunk := sock.recv(4096):
            reply += chunk
    assert b"200" in reply.split(b"\r\n", 1)[0]
    assert json.loads(reply.split(b"\r\n\r\n", 1)[1])["name"] == "dave"


def test_a_deployment_without_a_token_store_says_so(tmp_path):
    import threading

    config = Config(token=LEGACY)
    server = make_server("127.0.0.1", free_port(), config)
    threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        status, _ = claim(url, "gotgi_whatever")
        assert status == 503
    finally:
        server.shutdown()


# --- whoami ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("token", "status", "name"),
    [
        (LEGACY, 200, "legacy"),
        (INDEX, 200, "indexer"),
        (None, 401, None),
        ("gotg_wrong", 401, None),
    ],
    ids=["legacy", "indexer", "missing", "wrong"],
)
def test_whoami_names_every_principal(service, token, status, name):
    got_status, body = call(f"{service}/auth/whoami", token=token)
    assert got_status == status
    if name:
        assert json.loads(body)["name"] == name


def test_whoami_answers_head_with_status_and_no_body(service):
    status, body = call(f"{service}/auth/whoami", method="HEAD", token=LEGACY)
    assert status == 200
    assert body == b""

    status, _ = call(f"{service}/auth/whoami", method="POST", token=LEGACY, body=b"{}")
    assert status == 405


# --- admin -------------------------------------------------------------------


def test_admin_mints_an_invite_and_lists_and_revokes(service):
    status, body = call(
        f"{service}/admin/invites",
        method="POST",
        token=ADMIN,
        body=json.dumps({"name": "erin"}).encode(),
    )
    assert status == 200
    code = json.loads(body)["code"]
    assert code.startswith("gotgi_")

    status, body = claim(service, code)
    token = json.loads(body)["token"]

    status, body = call(f"{service}/admin/tokens", token=ADMIN)
    assert status == 200
    rows = json.loads(body)["tokens"]
    assert [r["name"] for r in rows] == ["erin"]
    assert token not in body.decode()

    status, _ = call(f"{service}/admin/tokens/erin", method="DELETE", token=ADMIN)
    assert status == 200
    assert call(f"{service}/auth/whoami", token=token)[0] == 401
    status, _ = call(f"{service}/admin/tokens/erin", method="DELETE", token=ADMIN)
    assert status == 404


def test_revoking_over_the_wire_kills_an_outstanding_invite_too(service):
    def invite(name):
        _, body = call(
            f"{service}/admin/invites",
            method="POST",
            token=ADMIN,
            body=json.dumps({"name": name}).encode(),
        )
        return json.loads(body)["code"]

    token = json.loads(claim(service, invite("frank"))[1])["token"]
    leaked = invite("frank")

    assert call(f"{service}/admin/tokens/frank", method="DELETE", token=ADMIN)[0] == 200
    assert claim(service, leaked)[0] == 410
    assert call(f"{service}/auth/whoami", token=token)[0] == 401


def test_admin_routes_refuse_the_client_token(service):
    status, _ = call(f"{service}/admin/invites", method="POST", token=LEGACY, body=b'{"name": "x"}')
    assert status == 403


def test_admin_is_503_when_no_admin_token_is_configured(tmp_path, token_store):
    import threading

    config = Config(token=LEGACY)
    server = make_server("127.0.0.1", free_port(), config, token_store=token_store)
    threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        status, _ = call(f"{url}/admin/tokens", token=LEGACY)
        assert status == 503
    finally:
        server.shutdown()


def test_the_admin_token_is_no_good_outside_admin(service):
    # An admin token that could read saves would be one more shared secret;
    # outside its own prefix it is indistinguishable from a wrong token.
    assert call(f"{service}/saves/env-snes/meta", token=ADMIN)[0] == 401
    assert call(f"{service}/auth/whoami", token=ADMIN)[0] == 401


@pytest.mark.parametrize(
    "body",
    [
        json.dumps({"name": "Iris"}).encode(),
        json.dumps({"name": ""}).encode(),
        json.dumps({"name": "a b"}).encode(),
        json.dumps({"name": "legacy"}).encode(),
        json.dumps({"name": "x" * 33}).encode(),
        b"not json",
        b"[1, 2]",
        b'"a string"',
        b"null",
        b'{"name": "x", "ttl_days": "much"}',
        b'{"name": "x", "ttl_days": NaN}',
        b'{"name": 123}',
        b'{"name": "x", "ttl_days": null}',
        b'{"name": "x", "ttl_days": 1e300}',
        b'{"name": "x", "ttl_days": Infinity}',
    ],
    ids=[
        "upper",
        "empty",
        "space",
        "reserved",
        "too-long",
        "not-json",
        "array",
        "string",
        "null",
        "ttl-word",
        "ttl-nan",
        "name-int",
        "ttl-null",
        "ttl-huge",
        "ttl-inf",
    ],
)
def test_a_bad_invite_body_is_a_400_never_a_dropped_connection(service, body):
    status, _ = call(f"{service}/admin/invites", method="POST", token=ADMIN, body=body)
    assert status == 400


def test_the_longest_allowed_name_mints_over_the_wire(service):
    status, _ = call(
        f"{service}/admin/invites",
        method="POST",
        token=ADMIN,
        body=json.dumps({"name": "a" * 32}).encode(),
    )
    assert status == 200


def test_an_oversized_admin_body_is_413(service):
    status, _ = call(f"{service}/admin/invites", method="POST", token=ADMIN, body=b"x" * (64 * 1024 + 1))
    assert status == 413


def test_validate_refuses_an_admin_token_that_matches(token_store):
    with pytest.raises(ValueError, match="admin"):
        Config(token="same", admin_token="same").validate()
    with pytest.raises(ValueError, match="admin"):
        Config(token="a", index_token="b", admin_token="b").validate()


# --- introspection: the library pod ------------------------------------------


@pytest.fixture
def two_pods(tmp_path, token_store):
    import threading

    api_config = Config(token=LEGACY, admin_token=ADMIN)
    api = make_server("127.0.0.1", free_port(), api_config, token_store=token_store)
    threading.Thread(target=lambda: api.serve_forever(poll_interval=0.05), daemon=True).start()
    api_url = f"http://127.0.0.1:{api.server_port}"

    library_config = Config(token=LEGACY, auth_url=api_url)
    library = make_server(
        "127.0.0.1",
        free_port(),
        library_config,
        auth_cache_ttl=0.4,
        auth_neg_ttl=0.2,
    )
    threading.Thread(target=lambda: library.serve_forever(poll_interval=0.05), daemon=True).start()
    library_url = f"http://127.0.0.1:{library.server_port}"

    yield api, api_url, library_url
    library.shutdown()
    api.shutdown()


def test_a_personal_token_authenticates_on_the_library_pod(two_pods, token_store):
    _, api_url, library_url = two_pods
    code = token_store.mint_invite("frank")
    _, body = claim(api_url, code)
    token = json.loads(body)["token"]

    status, body = call(f"{library_url}/auth/whoami", token=token)
    assert status == 200
    assert json.loads(body)["name"] == "frank"


def test_revocation_reaches_the_library_after_the_cache_expires(two_pods, token_store):
    _, api_url, library_url = two_pods
    code = token_store.mint_invite("grace")
    _, body = claim(api_url, code)
    token = json.loads(body)["token"]

    assert call(f"{library_url}/auth/whoami", token=token)[0] == 200
    token_store.revoke("grace")
    # Cached: still good inside the TTL, refused after it.
    assert call(f"{library_url}/auth/whoami", token=token)[0] == 200
    time.sleep(0.5)
    assert call(f"{library_url}/auth/whoami", token=token)[0] == 401


def test_a_dead_api_pod_fails_personal_tokens_but_not_legacy(two_pods, token_store):
    api, api_url, library_url = two_pods
    code = token_store.mint_invite("henry")
    _, body = claim(api_url, code)
    token = json.loads(body)["token"]

    api.shutdown()
    api.server_close()  # refuse instantly instead of hanging AUTH_TIMEOUT
    # Uncached (never seen): the library cannot vouch for it.
    assert call(f"{library_url}/auth/whoami", token=token)[0] == 401
    # The break-glass path stays open.
    assert call(f"{library_url}/auth/whoami", token=LEGACY)[0] == 200


def test_a_wrong_token_is_negative_cached_briefly(two_pods, token_store):
    _, _, library_url = two_pods
    assert call(f"{library_url}/auth/whoami", token="gotg_nope")[0] == 401
    assert call(f"{library_url}/auth/whoami", token="gotg_nope")[0] == 401


def test_a_non_ascii_bearer_earns_401_through_introspection_not_a_crash(two_pods):
    # The header arrives latin-1 decoded; a stray high byte must survive the
    # round trip into the forwarded Authorization header.
    _, _, library_url = two_pods
    host, port = library_url.removeprefix("http://").split(":")
    with socket.create_connection((host, int(port)), timeout=5) as sock:
        sock.sendall(
            b"GET /auth/whoami HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer gotg_\x80\xff\r\nConnection: close\r\n\r\n"
        )
        reply = b""
        while chunk := sock.recv(4096):
            reply += chunk
    assert b"401" in reply.split(b"\r\n", 1)[0]


def test_the_auth_cache_clears_at_its_bound_instead_of_growing(tmp_path, token_store):
    import threading

    api = make_server("127.0.0.1", free_port(), Config(token=LEGACY), token_store=token_store)
    threading.Thread(target=lambda: api.serve_forever(poll_interval=0.05), daemon=True).start()
    api_url = f"http://127.0.0.1:{api.server_port}"
    library = make_server("127.0.0.1", free_port(), Config(token=LEGACY, auth_url=api_url))
    threading.Thread(target=lambda: library.serve_forever(poll_interval=0.05), daemon=True).start()
    try:
        # Keys are attacker-supplied hashes; the bound is the whole defence
        # against a memory-growth denial on the internet-facing pod.
        handler = library.RequestHandlerClass
        for i in range(1025):
            handler.auth_cache[f"stale-{i}"] = (None, 0.0)

        code = token_store.mint_invite("nina")
        _, body = claim(api_url, code)
        token = json.loads(body)["token"]
        status, _ = call(f"http://127.0.0.1:{library.server_port}/auth/whoami", token=token)
        assert status == 200
        assert len(handler.auth_cache) == 1
    finally:
        library.shutdown()
        api.shutdown()
