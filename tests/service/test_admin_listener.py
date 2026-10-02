"""Administration on a listener of its own: the tailnet's, never the public one.

The admin API was reachable wherever the service was -- gotg-api.dcraw.net
answers from the home IP, not through Cloudflare -- guarded by one bearer
alone. Now a deployment that names an admin port serves /admin there, with a
page to press, and the public listener answers /admin with a 404 that says
where it went. A listener that is neither (a test, a laptop) keeps both, as
before.
"""

from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request

import pytest
from test_proxy import free_port

from gotg.service import admin_page
from gotg.service.app import Config, make_server
from gotg.tokens import TokenStore

LEGACY = "legacy-token"
ADMIN = "s3cret-admin-bearer-9f2c"
INDEX = "index-token"
ADMIN_URL = "http://100.64.0.1:30781"
PUBLIC_URL = "https://gotg.example"


@pytest.fixture
def token_store(tmp_path):
    return TokenStore(tmp_path / "tokens.db")


def _serve(config, token_store, listener):
    server = make_server("127.0.0.1", free_port(), config, token_store=token_store, listener=listener)
    threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


@pytest.fixture
def pair(token_store):
    config = Config(token=LEGACY, admin_token=ADMIN, index_token=INDEX, admin_url=ADMIN_URL, public_url=PUBLIC_URL)
    public, public_url = _serve(config, token_store, "public")
    admin, admin_url = _serve(config, token_store, "admin")
    yield public_url, admin_url
    public.shutdown()
    admin.shutdown()


def call(url, method="GET", token=None, body=None):
    request = urllib.request.Request(url, data=body, method=method)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read(), response.headers
    except urllib.error.HTTPError as error:
        return error.code, error.read(), error.headers


# --- the public listener -------------------------------------------------------


@pytest.mark.parametrize("path", ["admin/tokens", "admin/invites", "admin/scan", "admin/", "admin"])
def test_the_public_listener_has_no_admin_even_for_the_admin_token(pair, path):
    public, _ = pair
    status, body, _ = call(f"{public}/{path}", token=ADMIN)
    assert status == 404
    # It says where administration went, so a CLI pointed at the old url is
    # told rather than left guessing.
    assert ADMIN_URL in json.loads(body)["error"]


def test_the_public_listener_cannot_mint(pair, token_store):
    public, _ = pair
    status, _, _ = call(f"{public}/admin/invites", method="POST", token=ADMIN, body=b'{"name": "eve"}')
    assert status == 404
    assert token_store.invites() == []


def test_the_public_listener_still_serves_everything_else(pair):
    public, _ = pair
    assert call(f"{public}/healthz")[0] == 200
    assert call(f"{public}/auth/whoami", token=LEGACY)[0] == 200


# --- the admin listener --------------------------------------------------------


def test_the_admin_listener_mints_lists_and_revokes(pair, token_store):
    _, admin = pair
    status, body, _ = call(
        f"{admin}/admin/invites", method="POST", token=ADMIN, body=b'{"name": "erin-deck", "ttl_days": 2}'
    )
    assert status == 200
    assert json.loads(body)["code"].startswith("gotgi_")
    status, body, _ = call(f"{admin}/admin/invites", token=ADMIN)
    assert status == 200
    [invite] = json.loads(body)["invites"]
    assert invite["name"] == "erin-deck"
    assert invite["user"] == "erin"
    assert "code" not in invite and "code_hash" not in invite
    assert call(f"{admin}/admin/tokens/erin-deck", method="DELETE", token=ADMIN)[0] == 200
    assert json.loads(call(f"{admin}/admin/invites", token=ADMIN)[1])["invites"] == []


def test_the_admin_listener_still_wants_the_admin_token(pair):
    _, admin = pair
    assert call(f"{admin}/admin/tokens")[0] == 401
    assert call(f"{admin}/admin/tokens", token=LEGACY)[0] == 403


@pytest.mark.parametrize("path", ["catalog", "auth/whoami", "saves/env-n64", "steamgriddb/x"])
def test_the_admin_listener_serves_nothing_but_admin(pair, path):
    # One more door to the saves and the upstream keys is not what a second
    # listener is for.
    _, admin = pair
    assert call(f"{admin}/{path}", token=LEGACY)[0] == 404


def test_the_admin_listener_answers_health(pair):
    _, admin = pair
    assert call(f"{admin}/healthz")[0] == 200


def test_info_names_the_public_url_a_claim_link_is_on(pair):
    _, admin = pair
    status, body, _ = call(f"{admin}/admin/info", token=ADMIN)
    assert status == 200
    assert json.loads(body)["public_url"] == PUBLIC_URL


# --- the page ------------------------------------------------------------------


@pytest.mark.parametrize("path", ["admin", "admin/"])
def test_the_page_is_served_without_a_token_and_holds_none(pair, path):
    _, admin = pair
    status, body, headers = call(f"{admin}/{path}")
    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert ADMIN.encode() not in body


@pytest.mark.parametrize("path", ["admin/", "admin/app.js", "admin/app.css"])
def test_the_page_and_its_parts_are_locked_down(pair, path):
    _, admin = pair
    status, _, headers = call(f"{admin}/{path}")
    assert status == 200
    csp = headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp
    assert "script-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "unsafe-inline" not in csp
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Cache-Control"] == "no-store"
    assert headers["Referrer-Policy"] == "no-referrer"


def test_the_parts_have_their_types(pair):
    _, admin = pair
    assert call(f"{admin}/admin/app.js")[2]["Content-Type"].startswith("text/javascript")
    assert call(f"{admin}/admin/app.css")[2]["Content-Type"].startswith("text/css")


def test_the_page_never_writes_what_it_is_given_as_html():
    # Names and users come from people who were invited; the page puts them in
    # the DOM as text, never as markup.
    assert "innerHTML" not in admin_page.SCRIPT
    assert "outerHTML" not in admin_page.SCRIPT
    assert "insertAdjacentHTML" not in admin_page.SCRIPT
    assert "document.write" not in admin_page.SCRIPT
    assert not re.search(r"\beval\(", admin_page.SCRIPT)


def test_the_page_keeps_the_token_for_the_tab_only():
    assert "sessionStorage" in admin_page.SCRIPT
    assert "localStorage" not in admin_page.SCRIPT


def test_the_page_uses_no_inline_script_or_style():
    # The policy forbids both; one slipping in would be a blank page.
    assert "<script>" not in admin_page.PAGE
    assert "style=" not in admin_page.PAGE
    assert not re.search(r"<[^>]+\son[a-z]+=", admin_page.PAGE)


# --- one listener for both, as before --------------------------------------------


def test_a_server_with_no_listener_named_serves_both_as_before(token_store):
    config = Config(token=LEGACY, admin_token=ADMIN, index_token=INDEX)
    server, url = _serve(config, token_store, "both")
    try:
        assert call(f"{url}/admin/tokens", token=ADMIN)[0] == 200
        assert call(f"{url}/auth/whoami", token=LEGACY)[0] == 200
        assert call(f"{url}/admin/")[0] == 200
    finally:
        server.shutdown()


# --- the environment -------------------------------------------------------------


def test_the_admin_port_and_urls_come_from_the_environment():
    from gotg.service.cli import admin_port_from_env, config_from_env

    env = {
        "GOTG_PROXY_TOKEN": "t",
        "GOTG_ADMIN_PORT": "8081",
        "GOTG_ADMIN_URL": ADMIN_URL + "/",
        "GOTG_PUBLIC_URL": PUBLIC_URL,
    }
    assert admin_port_from_env(env) == 8081
    config = config_from_env(env)
    assert config.admin_url == ADMIN_URL
    assert config.public_url == PUBLIC_URL
    assert admin_port_from_env({}) is None
    with pytest.raises(ValueError):
        admin_port_from_env({"GOTG_ADMIN_PORT": "eighty"})


def test_the_listing_of_invites_is_only_the_open_ones(token_store, monkeypatch):
    token_store.mint_invite("open-deck")
    claimed = token_store.mint_invite("used-deck")
    token_store.claim(claimed)
    token_store.mint_invite("gone-deck", ttl=1)
    token_store.mint_invite("cancelled-deck")
    token_store.revoke("cancelled-deck")
    import time as real_time

    later = real_time.time() + 5
    monkeypatch.setattr("gotg.tokens.time.time", lambda: later)
    assert [i["name"] for i in token_store.invites()] == ["open-deck"]


def test_a_deployment_with_no_admin_token_serves_no_page(token_store):
    config = Config(token=LEGACY, index_token=INDEX)
    server, url = _serve(config, token_store, "both")
    try:
        assert call(f"{url}/admin/")[0] == 401
        assert call(f"{url}/admin/app.js")[0] == 401
    finally:
        server.shutdown()
