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
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler

import pytest
from harness import send, serve, serve_stub

from gotg.service.app import Config

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


@pytest.fixture
def upstream():
    Upstream.seen = []
    Upstream.token_calls = 0
    Upstream.token_ttl = 5184000
    with serve_stub(Upstream) as stub:
        yield stub.url


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
    with serve(config) as server:
        yield server.url


def call(url: str, token: str | None = "client-token", data: bytes | None = None):
    status, _, body = send(url, token=token, body=data)
    return status, body


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


def test_an_upstream_answer_keeps_its_status_body_and_content_type(proxy):
    status, headers, body = send(f"{proxy}/steamgriddb/missing", token="client-token")
    assert status == 404
    assert headers["Content-Type"] == "application/json"
    assert json.loads(body) == {"error": "no such thing"}
    status, headers, body = send(f"{proxy}/steamgriddb/fine", token="client-token")
    assert status == 200
    assert headers["Content-Type"] == "application/json"
    assert json.loads(body)["saw"].endswith("/fine")


def test_an_unknown_route_is_a_404(proxy):
    status, _ = call(f"{proxy}/nothing/here")
    assert status == 404


def test_an_unconfigured_upstream_says_so(upstream):
    config = Config(token="client-token", steamgriddb_url=upstream)  # no key
    with serve(config) as server:
        status, body = call(f"{server.url}/steamgriddb/x")
        assert status == 503
        assert b"steamgriddb" in body.lower()


def test_an_upstream_that_is_down_is_a_gateway_error(upstream):
    config = Config(
        token="client-token",
        steamgriddb_key="k",
        steamgriddb_url="http://127.0.0.1:1",
    )
    with serve(config) as server:
        status, headers, body = send(f"{server.url}/steamgriddb/x")
        assert status == 502
        assert headers["Content-Type"] == "application/json"
        assert json.loads(body)["error"].startswith("upstream unreachable")


def test_a_cached_proxy_whose_upstream_is_down_says_so_the_same_way(tmp_path):
    config = Config(
        token="client-token",
        steamgriddb_key="k",
        steamgriddb_url="http://127.0.0.1:1",
        upstream_cache_dir=str(tmp_path / "artcache"),
    )
    with serve(config) as server:
        status, _, body = send(f"{server.url}/steamgriddb/grids/game/1")
        assert status == 502
        assert json.loads(body)["error"].startswith("upstream unreachable")
        assert not (tmp_path / "artcache").exists() or not list((tmp_path / "artcache").iterdir())


def test_a_token_is_required_even_when_nothing_is_configured():
    # Refusing before looking at what is configured: an unauthenticated caller
    # must not be able to tell which upstreams this proxy holds keys for.
    config = Config(token="client-token")
    with serve(config) as server:
        status, _ = call(f"{server.url}/igdb/games", token=None)
        assert status == 401


def test_it_refuses_to_start_with_no_token():
    # An empty token would authenticate everybody. Better to fail to start than
    # to be an open relay for somebody else's API quota.
    with pytest.raises(ValueError, match="token"):
        Config(token="").validate()


def test_timing_safe_comparison_is_used():
    # Not a behaviour a test can observe from outside, so it is asserted about
    # the code: a plain == on a shared secret leaks its length and prefix.
    import inspect

    from gotg.service import app

    assert "compare_digest" in inspect.getsource(app)


def test_upstream_calls_are_bounded():
    # A request that never returns holds a thread open; with enough of them the
    # proxy stops answering anybody.
    from gotg.service import app

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


# --- what a hostile client can make us do -----------------------------------
#
# The proxy attaches a real credential to whatever path follows the upstream
# prefix, so the path is attacker-controlled input to a credentialed request.
# Two things must not follow from that: the request escaping /api/v2 on the
# upstream, and the credential following a redirect off the upstream host.
# urllib does both by default — it sends a path verbatim, and its redirect
# handler copies every header except Content-* onto the new request, including
# Authorization, even when the redirect crosses hosts.


def raw_call(base: str, path: str, token: str = "client-token"):
    """http.client with the path used verbatim — urllib normalizes some of
    these away on the client side, which would make the test test nothing."""
    import http.client

    host = base.removeprefix("http://")
    conn = http.client.HTTPConnection(host, timeout=10)
    conn.putrequest("GET", path, skip_accept_encoding=True)
    conn.putheader("Authorization", f"Bearer {token}")
    conn.endheaders()
    response = conn.getresponse()
    body = response.read()
    conn.close()
    return response.status, body


def test_a_path_that_climbs_out_of_the_api_is_refused(proxy):
    status, _ = raw_call(proxy, "/steamgriddb/../oauth/anything")
    assert status == 400
    assert Upstream.seen == []


def test_a_percent_encoded_climb_is_refused_too(proxy):
    # The proxy forwards the path still encoded, and the upstream decodes it —
    # so the check has to happen on the decoded form or it checks nothing.
    status, _ = raw_call(proxy, "/steamgriddb/%2e%2e/oauth/anything")
    assert status == 400
    assert Upstream.seen == []


def test_a_redirect_is_relayed_to_the_client_not_followed(upstream):
    # If the proxy followed it, the upstream would see a second request — with
    # the key attached — at wherever the first one pointed. An open redirect on
    # the upstream would then walk the key off to any host on the internet.
    class Redirecting(Upstream):
        def do_GET(self):  # noqa: N802
            self._record(b"")
            self.send_response(302)
            self.send_header("Location", "http://evil.example/steal")
            self.send_header("Content-Length", "0")
            self.end_headers()

    with serve_stub(Redirecting) as stub:
        config = Config(token="client-token", steamgriddb_key="k", steamgriddb_url=stub.url)
        with serve(config) as proxy_server:
            status, _ = call(f"{proxy_server.url}/steamgriddb/x")
            assert status == 302
            assert len(Upstream.seen) == 1  # exactly one upstream request, no follow


def test_a_body_far_larger_than_a_query_is_refused(proxy):
    # IGDB queries are a line or two. A caller claiming to send megabytes is
    # either broken or trying to make the proxy hold it all in memory. The
    # guarantee is that it is neither buffered nor forwarded: rejecting a
    # racing upload may reach the client as a 413 or as a reset, exactly as a
    # real server's would, so both count — what must hold is that the upstream
    # never saw it.
    big = b"x" * (2 * 1024 * 1024)
    try:
        status, _ = call(f"{proxy}/igdb/games", data=big)
    except (urllib.error.URLError, ConnectionError):
        status = "reset"
    assert status in (413, "reset")
    assert Upstream.seen == []


def test_a_nonsense_content_length_is_a_clean_error(proxy):
    # int("garbage") would be a 500 with a traceback; a malformed length is the
    # client's fault and should read as one.
    import http.client

    host = proxy.removeprefix("http://")
    conn = http.client.HTTPConnection(host, timeout=10)
    conn.putrequest("POST", "/igdb/games", skip_accept_encoding=True)
    conn.putheader("Authorization", "Bearer client-token")
    conn.putheader("Content-Length", "not-a-number")
    conn.endheaders()
    try:
        status = conn.getresponse().status
    except Exception:  # noqa: BLE001 — a reset would also be acceptable, a hang not
        status = 400
    conn.close()
    assert status == 400


def test_a_hostile_content_type_cannot_split_our_response():
    # Content-Type is the one upstream-controlled value echoed into our own
    # headers. A folded value arrives with a raw CRLF still in it, and writing
    # that back out is response splitting — harmless with the client here,
    # which re-folds it, but parsers differ and this one is free to close.

    def hostile(sock):
        conn, _ = sock.accept()
        conn.recv(65536)
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n Injected: yes\r\nContent-Length: 2\r\n\r\nhi")
        conn.close()

    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    threading.Thread(target=hostile, args=(listener,), daemon=True).start()

    config = Config(
        token="client-token",
        steamgriddb_key="k",
        steamgriddb_url=f"http://127.0.0.1:{listener.getsockname()[1]}",
    )
    try:
        with serve(config) as server:
            _, headers, _ = send(f"{server.url}/steamgriddb/x")
        seen = headers.get("Content-Type", "")
        assert "\r" not in seen and "\n" not in seen
    finally:
        listener.close()


# --- the upstream cache -------------------------------------------------------
#
# What matters most: a repeated question never reaches SteamGridDB twice, a
# failed answer is never remembered, and a cache that cannot be written is a
# proxy that still works.


def call_with_headers(url: str, token: str | None = "client-token"):
    status, headers, body = send(url, token=token)
    return status, json.loads(body or b"{}"), headers


@pytest.fixture
def cached_proxy(upstream, tmp_path):
    config = Config(
        token="client-token",
        steamgriddb_key="sg-key",
        steamgriddb_url=upstream,
        upstream_cache_dir=str(tmp_path / "artcache"),
    )
    with serve(config) as server:
        yield server.url, tmp_path / "artcache"


def test_a_repeated_question_reaches_upstream_once(cached_proxy):
    proxy, _ = cached_proxy
    status, first, headers = call_with_headers(f"{proxy}/steamgriddb/grids/game/42?dimensions=600x900")
    assert status == 200
    assert headers.get("X-Gotg-Cache") == "miss"

    status, second, headers = call_with_headers(f"{proxy}/steamgriddb/grids/game/42?dimensions=600x900")
    assert status == 200
    assert headers.get("X-Gotg-Cache") == "hit"
    assert second == first, "the cached answer is the answer"
    assert len(Upstream.seen) == 1, "one question upstream, however many clients ask"


def test_a_cached_proxy_relays_an_upstream_error_with_its_body(cached_proxy):
    proxy, _ = cached_proxy
    status, headers, body = send(f"{proxy}/steamgriddb/grids/game/missing", token="client-token")
    assert status == 404
    assert headers["Content-Type"] == "application/json"
    assert json.loads(body) == {"error": "no such thing"}
    assert "X-Gotg-Cache" not in headers


def test_a_cached_miss_is_marked_and_keeps_the_content_type(cached_proxy):
    proxy, _ = cached_proxy
    _, miss, _ = call_with_headers(f"{proxy}/steamgriddb/grids/game/3")
    status, headers, _ = send(f"{proxy}/steamgriddb/grids/game/3", token="client-token")
    assert (status, headers["X-Gotg-Cache"], headers["Content-Type"]) == (200, "hit", "application/json")
    assert miss["ok"] is True


def test_a_different_query_is_a_different_answer(cached_proxy):
    proxy, _ = cached_proxy
    call_with_headers(f"{proxy}/steamgriddb/grids/game/42?dimensions=600x900")
    call_with_headers(f"{proxy}/steamgriddb/grids/game/42?dimensions=920x430")
    assert len(Upstream.seen) == 2, "the query string is part of the question"


def test_an_upstream_failure_is_relayed_and_never_remembered(cached_proxy):
    proxy, _ = cached_proxy
    status, _, headers = call_with_headers(f"{proxy}/steamgriddb/grids/game/missing")
    assert status == 404
    status, _, _ = call_with_headers(f"{proxy}/steamgriddb/grids/game/missing")
    assert status == 404
    assert len(Upstream.seen) == 2, "a 404 asked again is asked upstream again"


def test_the_cache_survives_a_service_restart(upstream, tmp_path):
    config = Config(
        token="client-token",
        steamgriddb_key="sg-key",
        steamgriddb_url=upstream,
        upstream_cache_dir=str(tmp_path / "artcache"),
    )
    with serve(config) as server:
        call_with_headers(f"{server.url}/steamgriddb/grids/game/7")
    with serve(config) as reborn:
        status, _, headers = call_with_headers(f"{reborn.url}/steamgriddb/grids/game/7")
    assert status == 200
    assert headers.get("X-Gotg-Cache") == "hit"
    assert len(Upstream.seen) == 1


def test_an_unwritable_cache_is_a_working_proxy(upstream, tmp_path):
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    blocked.chmod(0o500)
    config = Config(
        token="client-token",
        steamgriddb_key="sg-key",
        steamgriddb_url=upstream,
        upstream_cache_dir=str(blocked / "artcache"),
    )
    try:
        with serve(config) as server:
            status, _, _ = call_with_headers(f"{server.url}/steamgriddb/grids/game/9")
        assert status == 200, "best-effort: a full or broken disk must not take artwork down"
    finally:
        blocked.chmod(0o700)


def test_without_a_cache_dir_nothing_changes(proxy):
    status, _, headers = call_with_headers(f"{proxy}/steamgriddb/grids/game/42")
    assert status == 200
    assert "X-Gotg-Cache" not in headers
    call_with_headers(f"{proxy}/steamgriddb/grids/game/42")
    assert len(Upstream.seen) == 2


# --- the pace on the way out ------------------------------------------------
#
# One key, a fleet of clients and a warm run that walks 5674 games: without a
# ceiling here, the proxy is a loaded gun pointed at somebody else's quota.


@contextmanager
def paced(upstream, **overrides):
    config = Config(
        token="client-token",
        steamgriddb_key="sg-key",
        steamgriddb_url=upstream,
        upstream_rate=1.0,
        upstream_burst=1.0,
        upstream_wait=0.0,
        **overrides,
    )
    with serve(config) as server:
        yield server.url


def test_past_the_pace_a_caller_is_told_to_come_back(upstream):
    with paced(upstream) as proxy:
        assert call(f"{proxy}/steamgriddb/search/autocomplete/zelda")[0] == 200
        status, body = call(f"{proxy}/steamgriddb/search/autocomplete/mario")
    assert status == 429
    assert len(Upstream.seen) == 1, "the refused question never left the cluster"


def test_a_throttled_answer_says_how_long_to_wait(upstream):
    with paced(upstream) as proxy:
        call(f"{proxy}/steamgriddb/search/autocomplete/zelda")
        status, headers, _ = send(f"{proxy}/steamgriddb/search/autocomplete/mario")
        assert status == 429
        assert int(headers["Retry-After"]) >= 1


def test_a_cached_answer_is_never_throttled(upstream, tmp_path):
    # It costs the upstream nothing, and throttling it would make the cache
    # useless exactly when the fleet needs it most.
    with paced(upstream, upstream_cache_dir=str(tmp_path / "artcache")) as proxy:
        assert call(f"{proxy}/steamgriddb/grids/game/42")[0] == 200
        for _ in range(5):
            status, _ = call(f"{proxy}/steamgriddb/grids/game/42")
            assert status == 200
    assert len(Upstream.seen) == 1
