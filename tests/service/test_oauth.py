"""Sign-in against a stub provider: browser flow, device flow, sessions, allowlist."""

import base64
import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler

import pytest
from harness import serve, serve_stub

from gotg.service.app import Config
from gotg.service.cli import config_from_env
from gotg.service.oauth import Identity, allowed, safe_next, user_slug
from gotg.tokens import TokenStore

LEGACY = "legacy-token"
ADMIN = "admin-token"


class NoFollow(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(NoFollow())


def raw(url, method="GET", headers=None, body=None):
    """(status, headers, body) without following redirects or raising."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with OPENER.open(req, timeout=10) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def cookies(headers):
    out = {}
    for line in headers.get_all("Set-Cookie") or []:
        name, _, rest = line.partition("=")
        out[name] = rest
    return out


def make_provider(profile, record):
    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _reply(self, obj, code=200):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            form = urllib.parse.parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
            record["token_form"] = {k: v[0] for k, v in form.items()}
            if form["code"][0] != "good-code":
                return self._reply({"error": "bad_verification_code"})
            self._reply({"access_token": "provider-access"})

        def do_GET(self):
            if self.path == "/.well-known/openid-configuration":
                base = f"http://127.0.0.1:{self.server.server_port}"
                return self._reply(
                    {
                        "authorization_endpoint": f"{base}/authorize",
                        "token_endpoint": f"{base}/token",
                        "userinfo_endpoint": f"{base}/userinfo",
                    }
                )
            if self.headers.get("Authorization") != "Bearer provider-access":
                return self._reply({}, 401)
            if self.path == "/user/emails":
                return self._reply(profile.get("emails", []))
            self._reply(profile["userinfo"])

    return Provider


def config_for(base, **extra):
    fields = dict(
        token=LEGACY,
        admin_token=ADMIN,
        public_url="https://gotg.example",
        oidc_provider="github",
        oidc_client_id="cid",
        oidc_client_secret="sec",
        oidc_redirect_url="https://gotg.example/auth/callback",
        session_secret="s" * 40,
        oidc_authorize_url=f"{base}/authorize",
        oidc_token_url=f"{base}/token",
        oidc_userinfo_url=f"{base}/user",
        oidc_allowlist=("alice",),
    )
    fields.update(extra)
    return Config(**fields)


@pytest.fixture
def github():
    record = {}
    profile = {"userinfo": {"login": "Alice", "id": 7}, "emails": []}
    with serve_stub(make_provider(profile, record)) as provider:
        yield provider.url, profile, record


@pytest.fixture
def app(tmp_path, github):
    base, _, _ = github
    store = TokenStore(db=tmp_path / "tokens.db")
    with serve(config_for(base), token_store=store) as server:
        yield server.url, store


def sign_in(url, next_="/library"):
    """Run login + callback with a good code; returns the callback's reply."""
    status, headers, _ = raw(f"{url}/auth/login?next={urllib.parse.quote(next_)}")
    assert status == 302
    target = urllib.parse.urlsplit(headers["Location"])
    query = dict(urllib.parse.parse_qsl(target.query))
    state_cookie = "gotg_oauth_state=" + cookies(headers)["gotg_oauth_state"].split(";")[0]
    reply = raw(
        f"{url}/auth/callback?code=good-code&state={query['state']}",
        headers={"Cookie": state_cookie},
    )
    return query, reply


def session_of(headers):
    return "gotg_session=" + cookies(headers)["gotg_session"].split(";")[0]


def test_login_redirects_with_pkce_state_and_a_state_cookie(app, github):
    url, _ = app
    base, _, _ = github
    status, headers, _ = raw(f"{url}/auth/login")
    assert status == 302
    target = urllib.parse.urlsplit(headers["Location"])
    assert f"{target.scheme}://{target.netloc}{target.path}" == f"{base}/authorize"
    q = dict(urllib.parse.parse_qsl(target.query))
    assert q["response_type"] == "code" and q["client_id"] == "cid"
    assert q["code_challenge_method"] == "S256" and q["redirect_uri"] == "https://gotg.example/auth/callback"
    cookie = headers["Set-Cookie"]
    assert q["state"] in cookie and "HttpOnly" in cookie and "SameSite=Lax" in cookie and "Secure" in cookie


def test_callback_sets_a_session_that_is_a_principal_and_a_token(app, github):
    url, store = app
    _, profile, record = github
    query, (status, headers, _) = sign_in(url)
    assert status == 302 and headers["Location"] == "/library"
    # PKCE: the verifier sent to the token endpoint hashes to the challenge.
    digest = hashlib.sha256(record["token_form"]["code_verifier"].encode()).digest()
    assert base64.urlsafe_b64encode(digest).rstrip(b"=").decode() == query["code_challenge"]
    assert record["token_form"]["client_secret"] == "sec"

    cookie = cookies(headers)["gotg_session"]
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie
    session = session_of(headers)
    status, _, body = raw(f"{url}/auth/whoami", headers={"Cookie": session})
    who = json.loads(body)
    assert status == 200 and who["user"] == "alice" and who["name"].startswith("alice-web-")
    # The same token, as a bearer, is the same principal.
    token = session.split("=", 1)[1]
    status, _, body = raw(f"{url}/auth/whoami", headers={"Authorization": f"Bearer {token}"})
    assert json.loads(body) == who
    assert {t["name"] for t in store.tokens()} == {who["name"]}


def test_admin_revoke_ends_the_session(app):
    url, store = app
    _, (_, headers, _) = sign_in(url)
    session = session_of(headers)
    name = json.loads(raw(f"{url}/auth/whoami", headers={"Cookie": session})[2])["name"]
    store.revoke(name)
    assert raw(f"{url}/auth/whoami", headers={"Cookie": session})[0] == 401


def test_two_logins_are_two_sessions(app):
    url, _ = app
    first = session_of(sign_in(url)[1][1])
    second = session_of(sign_in(url)[1][1])
    for session in (first, second):
        assert raw(f"{url}/auth/whoami", headers={"Cookie": session})[0] == 200


def test_logout_revokes_and_clears(app):
    url, _ = app
    session = session_of(sign_in(url)[1][1])
    status, headers, _ = raw(f"{url}/auth/logout", method="POST", headers={"Cookie": session})
    assert status == 200 and "Max-Age=0" in headers["Set-Cookie"]
    assert raw(f"{url}/auth/whoami", headers={"Cookie": session})[0] == 401


def test_logout_from_another_origin_is_refused(app):
    url, _ = app
    session = session_of(sign_in(url)[1][1])
    status, _, _ = raw(f"{url}/auth/logout", method="POST", headers={"Cookie": session, "Origin": "https://evil.test"})
    assert status == 403
    assert raw(f"{url}/auth/whoami", headers={"Cookie": session})[0] == 200


def test_cookie_writes_from_another_origin_are_refused(app):
    url, _ = app
    session = session_of(sign_in(url)[1][1])
    headers = {"Cookie": session, "Origin": "https://evil.test"}
    assert raw(f"{url}/saves/x", method="PUT", headers=headers, body=b"x")[0] == 403


def test_callback_without_the_matching_state_cookie_is_refused(app):
    url, _ = app
    _, headers, _ = raw(f"{url}/auth/login")
    state = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(headers["Location"]).query))["state"]
    assert raw(f"{url}/auth/callback?code=good-code&state={state}")[0] == 400
    assert (
        raw(f"{url}/auth/callback?code=good-code&state={state}", headers={"Cookie": "gotg_oauth_state=nope"})[0] == 400
    )


def test_state_is_single_use(app):
    url, _ = app
    _, headers, _ = raw(f"{url}/auth/login")
    state = dict(parse for parse in urllib.parse.parse_qsl(urllib.parse.urlsplit(headers["Location"]).query))["state"]
    cookie = {"Cookie": f"gotg_oauth_state={state}"}
    assert raw(f"{url}/auth/callback?code=good-code&state={state}", headers=cookie)[0] == 302
    assert raw(f"{url}/auth/callback?code=good-code&state={state}", headers=cookie)[0] == 400


def test_a_bad_code_is_a_502_and_mints_nothing(app):
    url, store = app
    _, headers, _ = raw(f"{url}/auth/login")
    state = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(headers["Location"]).query))["state"]
    status, _, _ = raw(f"{url}/auth/callback?code=bad&state={state}", headers={"Cookie": f"gotg_oauth_state={state}"})
    assert status == 502 and store.tokens() == []


def test_someone_off_the_allowlist_gets_nothing(app, github):
    url, store = app
    _, profile, _ = github
    profile["userinfo"] = {"login": "mallory", "id": 9}
    _, (status, headers, _) = sign_in(url)
    assert status == 403 and "gotg_session" not in cookies(headers) and store.tokens() == []


def test_next_cannot_leave_the_site(app):
    url, _ = app
    for hostile in ("https://evil.test/", "//evil.test", "/\\evil.test"):
        _, (_, headers, _) = sign_in(url, next_=hostile)
        assert headers["Location"] == "/"


def test_a_session_cookie_cannot_be_a_shared_secret(app):
    url, _ = app
    assert raw(f"{url}/auth/whoami", headers={"Cookie": f"gotg_session={LEGACY}"})[0] == 401


def test_a_junk_bearer_does_not_fall_back_to_the_cookie(app):
    url, _ = app
    session = session_of(sign_in(url)[1][1])
    headers = {"Cookie": session, "Authorization": "Bearer wrong"}
    assert raw(f"{url}/auth/whoami", headers=headers)[0] == 401


def test_session_cookie_reaches_ordinary_routes(app):
    url, _ = app
    session = session_of(sign_in(url)[1][1])
    status, _, _ = raw(f"{url}/saves/anything/meta", headers={"Cookie": session})
    assert status != 401


# --- device flow -------------------------------------------------------------


def start_device(url):
    status, _, body = raw(f"{url}/auth/device", method="POST")
    assert status == 200
    return json.loads(body)


def poll(url, device_code):
    status, _, body = raw(
        f"{url}/auth/device/token",
        method="POST",
        headers={"Content-Type": "application/json"},
        body=json.dumps({"device_code": device_code}).encode(),
    )
    return status, json.loads(body)


def test_device_flow_end_to_end(app):
    url, store = app
    started = start_device(url)
    assert started["verification_uri"] == "https://gotg.example/auth/device/verify"
    assert started["user_code"] in started["verification_uri_complete"].replace("%2D", "-")
    assert poll(url, started["device_code"]) == (400, {"error": "authorization_pending"})
    assert poll(url, started["device_code"]) == (400, {"error": "slow_down"})

    session = session_of(sign_in(url)[1][1])
    # Not signed in: the verify page sends you to log in, then back.
    status, headers, _ = raw(f"{url}/auth/device/verify?user_code={started['user_code']}")
    assert status == 302 and headers["Location"].startswith("/auth/login?next=")
    status, _, page = raw(f"{url}/auth/device/verify?user_code={started['user_code']}", headers={"Cookie": session})
    assert status == 200 and started["user_code"].encode() in page

    form = urllib.parse.urlencode({"user_code": started["user_code"].lower()}).encode()
    assert raw(f"{url}/auth/device/approve", method="POST", body=form)[0] == 401
    assert raw(f"{url}/auth/device/approve", method="POST", body=form, headers={"Cookie": session})[0] == 200

    status, got = poll(url, started["device_code"])
    assert status == 200 and got["user"] == "alice" and got["name"].startswith("alice-cli-")
    status, _, body = raw(f"{url}/auth/whoami", headers={"Authorization": f"Bearer {got['token']}"})
    assert json.loads(body)["name"] == got["name"]
    # Single use; and revoking the CLI leaves the browser alone.
    assert poll(url, started["device_code"])[1] == {"error": "expired_token"}
    store.revoke(got["name"])
    assert raw(f"{url}/auth/whoami", headers={"Authorization": f"Bearer {got['token']}"})[0] == 401
    assert raw(f"{url}/auth/whoami", headers={"Cookie": session})[0] == 200


def test_device_approval_from_another_origin_is_refused(app):
    url, _ = app
    started = start_device(url)
    session = session_of(sign_in(url)[1][1])
    form = urllib.parse.urlencode({"user_code": started["user_code"]}).encode()
    headers = {"Cookie": session, "Origin": "https://evil.test"}
    assert raw(f"{url}/auth/device/approve", method="POST", body=form, headers=headers)[0] == 403


def test_unknown_device_code_is_expired(app):
    url, _ = app
    assert poll(url, "nonsense") == (400, {"error": "expired_token"})


# --- providers & config ------------------------------------------------------


def test_github_verified_primary_email_matches_the_allowlist(tmp_path, github):
    base, profile, _ = github
    profile["userinfo"] = {"login": "someone", "id": 3}
    profile["emails"] = [{"email": "a@corp.test", "primary": True, "verified": True}]
    config = config_for(base, oidc_allowlist=("@corp.test",))
    with serve(config, token_store=TokenStore(db=tmp_path / "t.db")) as server:
        assert sign_in(server.url)[1][0] == 302


def test_unverified_github_email_does_not_count(tmp_path, github):
    base, profile, _ = github
    profile["userinfo"] = {"login": "someone", "id": 3}
    profile["emails"] = [{"email": "a@corp.test", "primary": True, "verified": False}]
    with serve(config_for(base, oidc_allowlist=("a@corp.test",)), token_store=TokenStore(db=tmp_path / "t.db")) as server:
        assert sign_in(server.url)[1][0] == 403


def test_oidc_by_issuer_discovery(tmp_path):
    profile = {"userinfo": {"sub": "u1", "preferred_username": "bob", "email": "bob@x.test", "email_verified": True}}
    with serve_stub(make_provider(profile, {})) as provider:
        config = Config(
            token=LEGACY,
            public_url="http://gotg.test",
            cookie_secure=False,
            oidc_provider="oidc",
            oidc_client_id="cid",
            oidc_issuer_url=provider.url,
            oidc_allowlist=("bob@x.test",),
        )
        with serve(config, token_store=TokenStore(db=tmp_path / "t.db")) as server:
            query, (status, headers, _) = sign_in(server.url)
            assert status == 302 and "Secure" not in cookies(headers)["gotg_session"]
            assert query["scope"] == "openid profile email"
            who = json.loads(raw(f"{server.url}/auth/whoami", headers={"Cookie": session_of(headers)})[2])
            assert who["user"] == "bob"


def test_oidc_unverified_email_is_not_identity(tmp_path):
    profile = {"userinfo": {"sub": "u1", "email": "bob@x.test"}}
    with serve_stub(make_provider(profile, {})) as provider:
        config = Config(
            token=LEGACY,
            public_url="http://gotg.test",
            cookie_secure=False,
            oidc_provider="oidc",
            oidc_client_id="cid",
            oidc_issuer_url=provider.url,
            oidc_allowlist=("bob@x.test",),
        )
        with serve(config, token_store=TokenStore(db=tmp_path / "t.db")) as server:
            assert sign_in(server.url)[1][0] == 403


def test_not_configured_answers_503_and_leaves_bearers_alone(tmp_path):
    with serve(Config(token=LEGACY), token_store=TokenStore(db=tmp_path / "t.db")) as server:
        assert raw(f"{server.url}/auth/login")[0] == 503
        assert raw(f"{server.url}/auth/whoami", headers={"Authorization": f"Bearer {LEGACY}"})[0] == 200


def test_empty_allowlist_admits_nobody():
    assert not allowed(Identity("alice", "a@x.test", "1"), ())


def test_slug_is_name_shaped_and_collision_safe():
    assert user_slug(Identity("Alice", "", "1")) == "alice"
    odd = user_slug(Identity("", "a.b@x.test", "1"))
    other = user_slug(Identity("", "a-b@x.test", "2"))
    assert odd != other
    assert user_slug(Identity("admin", "", "1")) != "admin"


def test_safe_next():
    assert safe_next("/a?b=1") == "/a?b=1"
    assert safe_next(None) == "/" and safe_next("http://x") == "/" and safe_next("/a\r\nb") == "/"


SECRET = "x" * 40


def test_config_reads_the_deploy_names():
    config = config_from_env(
        {
            "GOTG_PROXY_TOKEN": "t",
            "GOTG_OIDC_PROVIDER": "github",
            "GOTG_OIDC_CLIENT_ID": "cid",
            "GOTG_OIDC_CLIENT_SECRET": "sec",
            "GOTG_OIDC_REDIRECT_URL": "https://g.test/auth/callback",
            "GOTG_OIDC_ALLOWLIST": "Alice, @corp.test",
            "GOTG_SESSION_SECRET": SECRET,
        }
    )
    assert config.oidc_enabled and config.oidc_allowlist == ("@corp.test", "alice")


def test_oauth_names_are_accepted_aliases():
    env = {
        "GOTG_PROXY_TOKEN": "t",
        "GOTG_PUBLIC_URL": "https://g.test/",
        "GOTG_OAUTH_CLIENT_ID": "cid",
        "GOTG_OAUTH_CLIENT_SECRET": "sec",
        "GOTG_OAUTH_ALLOW": "Alice, @corp.test",
        "GOTG_OAUTH_SESSION_DAYS": "2",
        "GOTG_OAUTH_AUTHORIZE_URL": "https://p.test/a",
        "GOTG_SESSION_SECRET": SECRET,
    }
    config = config_from_env(env)
    assert config.oidc_provider == "github" and config.oidc_client_id == "cid"
    assert config.oidc_client_secret == "sec" and config.oidc_authorize_url == "https://p.test/a"
    assert config.oidc_allowlist == ("@corp.test", "alice")
    assert config.oidc_redirect_url == "https://g.test/auth/callback"
    assert config.session_ttl == 2 * 86400
    # The deploy name wins when both are set.
    both = config_from_env({**env, "GOTG_OIDC_CLIENT_ID": "new", "GOTG_SESSION_TTL": "60"})
    assert both.oidc_client_id == "new" and both.session_ttl == 60


def test_validation_still_applies_through_aliases():
    base = {"GOTG_PROXY_TOKEN": "t", "GOTG_OAUTH_CLIENT_ID": "cid", "GOTG_OAUTH_PROVIDER": "oidc"}
    with pytest.raises(ValueError, match="half configured"):
        config_from_env(base)
    with pytest.raises(ValueError, match="ISSUER"):
        config_from_env(
            {
                **base,
                "GOTG_OAUTH_CLIENT_SECRET": "s",
                "GOTG_OAUTH_ALLOW": "a",
                "GOTG_OAUTH_REDIRECT_URI": "https://g.test/auth/callback",
                "GOTG_SESSION_SECRET": SECRET,
            }
        )


def test_session_cookie_uses_configured_name_and_flags(tmp_path, github):
    base, _, _ = github
    config = config_for(base, cookie_name="my_sess", cookie_samesite="Strict")
    with serve(config, token_store=TokenStore(db=tmp_path / "t.db")) as server:
        _, (status, headers, _) = sign_in(server.url)
        assert status == 302
        cookie = cookies(headers)["my_sess"]
        assert "SameSite=Strict" in cookie and "Secure" in cookie and "Max-Age=604800" in cookie
        who = raw(f"{server.url}/auth/whoami", headers={"Cookie": "my_sess=" + cookie.split(";")[0]})
        assert who[0] == 200
