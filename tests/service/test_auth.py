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


@pytest.fixture
def token_store(tmp_path):
    return TokenStore(db=tmp_path / "state" / "tokens.db")


@pytest.fixture
def service(tmp_path, token_store):
    import threading

    config = Config(token=LEGACY, admin_token=ADMIN)
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


def test_whoami_names_every_principal(service):
    status, body = call(f"{service}/auth/whoami", token=LEGACY)
    assert status == 200
    assert json.loads(body)["name"] == "legacy"

    status, _ = call(f"{service}/auth/whoami")
    assert status == 401

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


def test_a_bad_invite_name_is_a_400(service):
    for bad in ["Iris", "", "a b", "legacy"]:
        status, _ = call(
            f"{service}/admin/invites",
            method="POST",
            token=ADMIN,
            body=json.dumps({"name": bad}).encode(),
        )
        assert status == 400


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
    # Uncached (never seen): the library cannot vouch for it.
    assert call(f"{library_url}/auth/whoami", token=token)[0] == 401
    # The break-glass path stays open.
    assert call(f"{library_url}/auth/whoami", token=LEGACY)[0] == 200


def test_a_wrong_token_is_negative_cached_briefly(two_pods, token_store):
    _, _, library_url = two_pods
    assert call(f"{library_url}/auth/whoami", token="gotg_nope")[0] == 401
    assert call(f"{library_url}/auth/whoami", token="gotg_nope")[0] == 401
