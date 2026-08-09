"""The credential-holding reverse proxy.

Everything here runs against a stub upstream rather than the real services, so
what is asserted is what we *send* — which is the whole job. The point of the
proxy is that a credential exists in exactly one place, so the tests that matter
most are the ones about which key reaches whom.
"""

from __future__ import annotations

import json
import socket
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from gotg_proxy.app import Config, make_server

# --- a stub for whatever sits upstream --------------------------------------


class Upstream(BaseHTTPRequestHandler):
    """Records what it was sent, and answers whatever the test arranged."""

    seen: list[dict] = []
    token_calls = 0
    token_ttl = 5184000

    def log_message(self, format, *args):  # noqa: A002
        pass

    def _record(self, body: bytes) -> None:
        Upstream.seen.append(
            {
                "path": self.path,
                "method": self.command,
                "authorization": self.headers.get("Authorization"),
                "client_id": self.headers.get("Client-ID"),
                "user_agent": self.headers.get("User-Agent"),
                "body": body.decode() if body else "",
            }
        )

    def _reply(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)

        # Twitch's token endpoint, which is what makes IGDB more than a header.
        if self.path.startswith("/oauth2/token"):
            Upstream.token_calls += 1
            self._reply(
                200,
                {
                    "access_token": f"issued-{Upstream.token_calls}",
                    "expires_in": Upstream.token_ttl,
                },
            )
            return

        self._record(body)
        self._reply(200, {"ok": True, "saw": self.path})

    def do_GET(self) -> None:  # noqa: N802
        self._record(b"")
        if self.path.endswith("/missing"):
            self._reply(404, {"error": "no such thing"})
            return
        self._reply(200, {"ok": True, "saw": self.path})


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve(handler) -> tuple[HTTPServer, str]:
    server = HTTPServer(("127.0.0.1", free_port()), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


@pytest.fixture
def upstream():
    Upstream.seen = []
    Upstream.token_calls = 0
    Upstream.token_ttl = 5184000
    server, url = serve(Upstream)
    yield url
    server.shutdown()


@pytest.fixture
def proxy(upstream):
    config = Config(
        token="client-token",
        steamgriddb_key="the-real-sgdb-key",
        steamgriddb_url=upstream,
        igdb_client_id="the-client-id",
        igdb_client_secret="the-secret",
        igdb_url=upstream,
        igdb_token_url=f"{upstream}/oauth2/token",
    )
    server = make_server("127.0.0.1", free_port(), config)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def call(url: str, token: str | None = "client-token", data: bytes | None = None):
    request = urllib.request.Request(url, data=data)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


# --- who is allowed in ------------------------------------------------------


def test_an_unauthenticated_request_is_refused(proxy):
    status, _ = call(f"{proxy}/steamgriddb/search/autocomplete/zelda", token=None)
    assert status == 401


def test_the_wrong_token_is_refused(proxy):
    status, _ = call(f"{proxy}/steamgriddb/search/autocomplete/zelda", token="guess")
    assert status == 401


def test_a_refused_request_never_reaches_upstream(proxy):
    call(f"{proxy}/steamgriddb/search/autocomplete/zelda", token="guess")
    assert Upstream.seen == []


def test_health_needs_no_token(proxy):
    status, _ = call(f"{proxy}/healthz", token=None)
    assert status == 200


# --- the credential swap, which is the entire point -------------------------


def test_steamgriddb_gets_the_real_key(proxy):
    call(f"{proxy}/steamgriddb/search/autocomplete/zelda")
    assert Upstream.seen[0]["authorization"] == "Bearer the-real-sgdb-key"


def test_the_client_token_never_leaves_the_cluster(proxy):
    call(f"{proxy}/steamgriddb/search/autocomplete/zelda")
    assert "client-token" not in json.dumps(Upstream.seen)


def test_steamgriddb_paths_land_on_the_v2_api(proxy):
    call(f"{proxy}/steamgriddb/search/autocomplete/zelda")
    assert Upstream.seen[0]["path"] == "/api/v2/search/autocomplete/zelda"


def test_a_query_string_survives(proxy):
    call(f"{proxy}/steamgriddb/grids/game/42?dimensions=600x900")
    assert Upstream.seen[0]["path"] == "/api/v2/grids/game/42?dimensions=600x900"


def test_it_identifies_itself(proxy):
    # Cloudflare answers the default python agent with a 403, whatever the key
    # says. That bug cost a working SteamGridDB source once already.
    call(f"{proxy}/steamgriddb/search/autocomplete/zelda")
    assert not Upstream.seen[0]["user_agent"].startswith("Python-urllib")


# --- igdb, which needs a token rather than a key ----------------------------


def test_igdb_gets_both_headers(proxy):
    call(f"{proxy}/igdb/games", data=b"fields name;")
    sent = Upstream.seen[0]
    assert sent["client_id"] == "the-client-id"
    assert sent["authorization"] == "Bearer issued-1"


def test_igdb_forwards_the_query_body(proxy):
    call(f"{proxy}/igdb/games", data=b'search "Luigi"; fields name;')
    assert Upstream.seen[0]["body"] == 'search "Luigi"; fields name;'


def test_igdb_paths_land_on_v4(proxy):
    call(f"{proxy}/igdb/games", data=b"fields name;")
    assert Upstream.seen[0]["path"] == "/v4/games"


def test_the_igdb_token_is_fetched_once_and_kept(proxy):
    call(f"{proxy}/igdb/games", data=b"fields name;")
    call(f"{proxy}/igdb/games", data=b"fields name;")
    call(f"{proxy}/igdb/games", data=b"fields name;")
    # Holding the token is why this service exists rather than each client
    # doing its own exchange.
    assert Upstream.token_calls == 1


def test_a_token_near_expiry_is_replaced(proxy):
    Upstream.token_ttl = 10  # already inside the refresh margin
    call(f"{proxy}/igdb/games", data=b"fields name;")
    call(f"{proxy}/igdb/games", data=b"fields name;")
    assert Upstream.token_calls == 2
    assert Upstream.seen[-1]["authorization"] == "Bearer issued-2"


# --- passing the upstream through honestly ----------------------------------


def test_an_upstream_error_is_relayed_not_swallowed(proxy):
    status, body = call(f"{proxy}/steamgriddb/missing")
    assert status == 404
    assert b"no such thing" in body


def test_an_unknown_route_is_a_404(proxy):
    status, _ = call(f"{proxy}/nothing/here")
    assert status == 404


def test_an_unconfigured_upstream_says_so(upstream):
    config = Config(token="client-token", steamgriddb_url=upstream)  # no key
    server = make_server("127.0.0.1", free_port(), config)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        status, body = call(f"http://127.0.0.1:{server.server_port}/steamgriddb/x")
        assert status == 503
        assert b"steamgriddb" in body.lower()
    finally:
        server.shutdown()


def test_an_upstream_that_is_down_is_a_gateway_error(upstream):
    config = Config(
        token="client-token",
        steamgriddb_key="k",
        steamgriddb_url="http://127.0.0.1:1",
    )
    server = make_server("127.0.0.1", free_port(), config)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        status, _ = call(f"http://127.0.0.1:{server.server_port}/steamgriddb/x")
        assert status == 502
    finally:
        server.shutdown()


def test_a_token_is_required_even_when_nothing_is_configured():
    # Refusing before looking at what is configured: an unauthenticated caller
    # must not be able to tell which upstreams this proxy holds keys for.
    config = Config(token="client-token")
    server = make_server("127.0.0.1", free_port(), config)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        status, _ = call(f"http://127.0.0.1:{server.server_port}/igdb/games", token=None)
        assert status == 401
    finally:
        server.shutdown()


def test_it_refuses_to_start_with_no_token():
    # An empty token would authenticate everybody. Better to fail to start than
    # to be an open relay for somebody else's API quota.
    with pytest.raises(ValueError, match="token"):
        Config(token="").validate()


def test_timing_safe_comparison_is_used():
    # Not a behaviour a test can observe from outside, so it is asserted about
    # the code: a plain == on a shared secret leaks its length and prefix.
    import inspect

    from gotg_proxy import app

    assert "compare_digest" in inspect.getsource(app)


def test_upstream_calls_are_bounded():
    # A request that never returns holds a thread open; with enough of them the
    # proxy stops answering anybody.
    from gotg_proxy import app

    assert 0 < app.TIMEOUT <= 30


# --- accepting the upstream's own path shape --------------------------------


def test_the_upstream_prefix_may_be_included(proxy):
    # `gotg steam art --base-url <proxy>/steamgriddb` builds /api/v2/... itself,
    # because that is what it builds for the real service. Doubling it up is the
    # obvious way for this to be unusable without a client change, so both
    # shapes land in the same place.
    call(f"{proxy}/steamgriddb/api/v2/search/autocomplete/zelda")
    assert Upstream.seen[0]["path"] == "/api/v2/search/autocomplete/zelda"


def test_the_igdb_prefix_may_be_included_too(proxy):
    call(f"{proxy}/igdb/v4/games", data=b"fields name;")
    assert Upstream.seen[0]["path"] == "/v4/games"
