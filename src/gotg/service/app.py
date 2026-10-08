"""The GOTG service: one endpoint holding the credentials and the saves.

Two jobs, one bearer token, one deployment.

The first is a reverse proxy for the artwork APIs. Every machine that runs
`gotg steam art` otherwise needs its own SteamGridDB key, and IGDB is worse:
its access token is minted from a client id and secret and expires in about
sixty days, so each client would have to hold two secrets and implement a
refresh. Putting one service in front of both turns that into a single
credential to rotate, in a Secret, in one place. It is a reverse proxy rather
than a forward one: a client does not configure it and then reach arbitrary
destinations through it, it simply *is* the API as far as the client is
concerned — it sends its own token, and this swaps in the real one.

Which makes the important property this: **the client's token must never reach
an upstream, and an upstream's key must never reach a client.** Most of the
proxy half is in service of that.

The second is the saves store — see saves.py. It lives here rather than on a
separate WebDAV endpoint because a store with one process in front of it can
decide conflicts atomically, which a dumb blob store never could, and because
one service means a client is configured once: the same url and token that
fetch artwork carry saves.

Authentication is not optional. This sits on the public internet behind an
ingress, and an open relay would be somebody else's free SteamGridDB quota,
somebody else's save hosting and, eventually, our banned key.

Only the standard library. It is a few hundred requests a week from a handful of
machines; a framework and a WSGI server would be more moving parts than the job
has.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .._http import NO_REDIRECT_OPENER
from ..catalog import CatalogStore
from ..saves import SavesStore
from ..tokens import TOKEN_RE, TokenStore
from . import admin_page
from .artcache import ArtCache
from .config import TIMEOUT, USER_AGENT, Config
from .ratelimit import RateLimiter
from .routes_admin import AdminRoutes
from .routes_art import ArtRoutes
from .routes_catalog import CatalogRoutes
from .routes_files import FileRoutes
from .routes_saves import SavesRoutes
from .token_cache import TokenCache

# Introspection is an in-cluster hop on every cache miss; a wedged api pod
# must not pin library threads for the full upstream TIMEOUT.
AUTH_TIMEOUT = 3

# The largest request body worth accepting. An IGDB query is a line or two; a
# caller claiming megabytes is broken or trying to make the proxy hold it all
# in memory. nginx caps this too, but the proxy also runs without it — under a
# port-forward, or if the ingress annotation is ever dropped.
MAX_BODY = 64 * 1024

# A catalog entry lists every member file of a game; decrypted WiiU trees run
# to five figures of rows.
CATALOG_MAX_BODY = 8 * 1024 * 1024

# A tile picture. SteamGridDB's 600x900 grids run to a few hundred kilobytes
# and the thumbnails the warmer prefers are a tenth of that; a megabyte is
# already an upload that is not a tile.
ART_MAX_BODY = 4 * 1024 * 1024


def safe_content_type(value: str) -> str:
    """An upstream's Content-Type, made safe to write into our own headers.

    It is the one upstream-controlled value that is echoed back, and a folded
    header arrives with the CRLF still in it — writing that out again is
    response splitting. Truncated at the first control character rather than
    stripped, because everything after one in a folded value was a separate
    header the upstream chose, not part of the type.
    """
    cut = min((i for i, c in enumerate(value) if c in "\r\n"), default=len(value))
    return value[:cut].strip() or "application/octet-stream"


def _cache_get(directory: str, key: str) -> tuple[bytes, str] | None:
    """A remembered answer, or None. A missing kind file is a half-written
    entry from a crash mid-put — treated as absent, rewritten on the miss."""
    try:
        payload = Path(directory, key).read_bytes()
        kind = Path(directory, key + ".kind").read_text().strip()
    except OSError:
        return None
    return (payload, kind or "application/json")


def _cache_put(directory: str, key: str, payload: bytes, kind: str) -> None:
    """Best-effort, atomically: a full or broken disk must cost the cache,
    never the answer. The kind lands first so a reader who can see the body
    can always see its type."""
    try:
        base = Path(directory)
        base.mkdir(parents=True, exist_ok=True)
        for name, data in ((key + ".kind", kind.encode()), (key, payload)):
            tmp = base / (name + ".part")
            tmp.write_bytes(data)
            os.replace(tmp, base / name)
    except OSError:
        pass


def path_climbs(rest: str) -> bool:
    """Whether a path tries to escape the API prefix it will be appended to.

    The path is attacker-controlled input to a request that carries a real
    credential, and it is forwarded still percent-encoded — the upstream is the
    one that decodes it. So the check is on the decoded form, or `%2e%2e`
    climbs exactly as well as `..` while the check watches nothing happen.
    """
    decoded = urllib.parse.unquote(rest.split("?")[0])
    return any(segment == ".." for segment in decoded.split("/"))


def strip_prefix(rest: str, prefix: str) -> str:
    """Allow a caller to include the upstream's own path prefix, or not.

    `gotg steam art --base-url <proxy>/steamgriddb` builds `/api/v2/...` itself,
    because that is what it builds for the real service — so without this the
    proxy would need a client change to be usable at all, which is the one thing
    it was supposed to avoid.
    """
    return rest[len(prefix) :] if rest.startswith(prefix) else rest


def split_route(path: str) -> tuple[str, str]:
    """`(prefix, rest)` of a path with its leading slash removed."""
    prefix, _, rest = path.partition("/")
    # A query on a bare prefix — /catalog?full=1 — otherwise rides along
    # in the prefix and matches no route.
    if "?" in prefix:
        prefix, _, query = prefix.partition("?")
        rest = f"?{query}"
    return prefix, rest


def body_cap(prefix: str, command: str, store: SavesStore | None) -> int:
    """The largest request body a route may send.

    A save bundle and a catalog entry are the two bodies allowed to be
    big — a retail WiiU tree runs to ~10k file rows at ~350 bytes each.
    Everything else keeps the small cap, because an IGDB query is a line
    or two.
    """
    if prefix == "saves" and command == "PUT" and store is not None:
        return store.max_bytes
    if prefix == "catalog" and command in ("PUT", "POST"):
        # PUT: a decrypted WiiU tree runs to five figures of file rows.
        # POST: /catalog/seen names every unchanged entry in one request,
        # which for this library is a few hundred kilobytes of ids.
        return CATALOG_MAX_BODY
    if prefix == "art" and command == "PUT":
        return ART_MAX_BODY
    return MAX_BODY


class Handler(ArtRoutes, SavesRoutes, CatalogRoutes, FileRoutes, AdminRoutes, BaseHTTPRequestHandler):
    config: Config
    tokens: TokenCache
    store: SavesStore | None
    catalog: CatalogStore | None
    files_dir: Path | None
    streams: threading.BoundedSemaphore
    scans: threading.BoundedSemaphore
    token_store: TokenStore | None
    art: ArtCache | None
    limits: dict[str, RateLimiter]
    auth_cache: dict
    auth_cache_lock: threading.Lock
    listener: str = "both"
    auth_cache_ttl: float
    auth_neg_ttl: float

    server_version = USER_AGENT
    # A stalled stream must not hold a slot forever: without this the socket
    # blocks indefinitely on a client that stopped reading, and a handful of
    # half-dead downloads would pin every stream slot until a restart.
    timeout = 300
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):  # noqa: A002
        # One line per request, without the client token ever being one of the
        # things logged. The path is raw attacker input, so control characters
        # are replaced — a crafted request-target must not write live escape
        # sequences into whoever is tailing the pod logs.
        path = self.path.split("?")[0]
        # A claim code is a live credential riding the URL — a link-preview
        # GET must not park it in the pod log past its single use. (The
        # ingress access log still sees the full path; single-use-atomic
        # claiming is the mitigation there.)
        if path.startswith("/claim/"):
            path = f"/claim/…{path[-4:]}"
        line = f"{self.command} {path} {args[1] if len(args) > 1 else ''}".strip()
        print("".join(c if c.isprintable() else "�" for c in line))

    # --- replies ------------------------------------------------------------

    def _send(self, code: int, body: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        # A HEAD response is bodiless whatever the code: writing the JSON of a
        # 404 here would sit on the kept-alive connection and be parsed as the
        # start of the next response.
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, obj: object, extra: dict[str, str] | None = None) -> None:
        """Reply with `obj` as JSON.

        Replaces twenty-four hand-written `self._send(code, json.dumps(obj)
        .encode(), "application/json")` calls, each of which had to remember
        the encode and the content type; one place now decides how this
        service spells a JSON answer. `extra` is for the few that add a header
        (Retry-After, X-Gotg-Art). The few literal `b'{"ok":true}'` replies are
        left as they are: their compact spelling is what clients and probes see.
        """
        self._send(code, json.dumps(obj).encode(), "application/json", extra)

    def _problem(self, code: int, message: str, *, close: bool = False) -> None:
        # close: for rejections raised before the request body is read. On a
        # kept-alive HTTP/1.1 connection the unread body would otherwise frame
        # the next request, so the client sees a reset instead of this reply.
        if close:
            self.close_connection = True
        self._json(code, {"error": message})

    # --- who is asking ------------------------------------------------------

    def _bearer(self) -> bytes:
        # Bytes, because compare_digest on str raises on non-ASCII — and the
        # header arrives latin-1 decoded, so a stray \xff from an
        # unauthenticated caller would otherwise crash the thread instead of
        # earning its 401.
        header = self.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            return header[len("Bearer ") :].encode("latin-1", "replace")
        if header.startswith("Basic ") and self._basic_allowed():
            return self._basic_password(header[len("Basic ") :])
        return b""

    def _basic_allowed(self) -> bool:
        """The catalog's client read, and nothing else. A library flake pins
        the catalog as an input, and Nix authenticates a fetch only through
        its netrc-file -- HTTP Basic. Everywhere else a token is a bearer."""
        parts = urllib.parse.urlsplit(self.path)
        return self.command == "GET" and parts.path == "/catalog" and not parts.query

    @staticmethod
    def _basic_password(encoded: str) -> bytes:
        """The password of `user:password`, which is where netrc puts the
        token; the user is not read. Anything malformed is no token at all."""
        try:
            decoded = base64.b64decode(encoded.strip(), validate=True)
        except (ValueError, binascii.Error):
            return b""
        _, sep, password = decoded.partition(b":")
        return password if sep else b""

    def _principal(self) -> str | None:
        """Who this bearer is: "legacy" (the shared env token), "indexer",
        "admin", "library", a per-person token's name, or None. The names the
        store can mint exclude the sentinels by RESERVED_NAMES, so they cannot
        collide.
        """
        # compare_digest rather than ==: a plain comparison returns early on the
        # first wrong byte, which leaks the secret a character at a time.
        bearer = self._bearer()
        if not bearer:
            return None
        if hmac.compare_digest(bearer, self.config.token.encode()):
            return "legacy"
        if self.config.index_token and hmac.compare_digest(bearer, self.config.index_token.encode()):
            return "indexer"
        if self.config.admin_token and hmac.compare_digest(bearer, self.config.admin_token.encode()):
            return "admin"
        if self.config.library_token and hmac.compare_digest(bearer, self.config.library_token.encode()):
            return "library"
        if self.token_store is not None:
            name = self.token_store.verify(bearer.decode("latin-1"))
            if name:
                return name
        # Only a plausibly-real personal token earns the network hop: during
        # an api-pod outage the failure path is uncached, and a 3s stall per
        # junk bearer would be a library-thread famine.
        if self.config.auth_url and TOKEN_RE.match(bearer.decode("latin-1")):
            return self._introspect(bearer)
        return None

    def _introspect(self, bearer: bytes) -> str | None:
        """Ask the pod that holds the token store. Cached by token hash — a
        revocation lands here one positive TTL late, which is the accepted
        cost of not building cross-pod invalidation. Network failure is
        unauthenticated and uncached, so the break-glass legacy token (checked
        before this) keeps working through an api-pod outage."""
        key = hashlib.sha256(bearer).hexdigest()
        now = time.monotonic()
        with self.auth_cache_lock:
            hit = self.auth_cache.get(key)
            if hit is not None and hit[1] > now:
                return hit[0]

        request = urllib.request.Request(f"{self.config.auth_url}/auth/whoami")
        request.add_header("Authorization", f"Bearer {bearer.decode('latin-1')}")
        try:
            with urllib.request.urlopen(request, timeout=AUTH_TIMEOUT) as response:
                name = json.loads(response.read()).get("name")
        except urllib.error.HTTPError as error:
            error.close()
            name = None
        except (urllib.error.URLError, OSError, ValueError):
            return None

        ttl = self.auth_cache_ttl if name else self.auth_neg_ttl
        with self.auth_cache_lock:
            # Keys are attacker-supplied hashes; a bound beats reasoning about
            # growth under the ingress rate limit.
            if len(self.auth_cache) > 1024:
                self.auth_cache.clear()
            self.auth_cache[key] = (name, time.monotonic() + ttl)
        return name

    def _is_index(self) -> bool:
        return bool(self.config.index_token) and hmac.compare_digest(self._bearer(), self.config.index_token.encode())

    # --- forwarding ---------------------------------------------------------

    def _fetch(self, url: str, headers: dict[str, str], body: bytes | None) -> tuple[int, bytes, str] | None:
        """Ask the upstream and return `(status, payload, content_type)`, or None once a 502 has been sent.

        `_forward` and the cached SteamGridDB branch each carried their own
        copy of this request, error relay and 502; they differed only in what
        they did with the answer, which is the part that stays with them. Every
        deliberate property lives here once: our User-Agent, the caller's
        headers (the credential), the no-redirect opener, the one timeout, and
        an upstream Content-Type made safe before it is echoed.

        An upstream error status is an answer, not a failure: a 404 from
        SteamGridDB means the game is not there, which the client needs to
        hear as a 404 -- so it comes back here with its own body, and only an
        upstream that could not be reached is turned into our 502. A 3xx is
        also an error status here (see `gotg._http`).
        """
        request = urllib.request.Request(url, data=body, method=self.command)
        request.add_header("User-Agent", USER_AGENT)
        for name, value in headers.items():
            request.add_header(name, value)

        try:
            with NO_REDIRECT_OPENER.open(request, timeout=TIMEOUT) as response:  # noqa: S310
                return (
                    response.status,
                    response.read(),
                    safe_content_type(response.headers.get("Content-Type", "")),
                )
        except urllib.error.HTTPError as error:
            kind = safe_content_type(error.headers.get("Content-Type", "")) if error.headers else "application/json"
            return error.code, error.read(), kind
        except (urllib.error.URLError, OSError, ValueError) as error:
            self._problem(502, f"upstream unreachable: {error}")
            return None

    def _forward(self, url: str, headers: dict[str, str], body: bytes | None) -> None:
        answer = self._fetch(url, headers, body)
        if answer is not None:
            self._send(*answer)

    def _paced(self, upstream: str) -> bool:
        """Wait for this upstream's turn, or answer 429 and say so.

        Every call that actually leaves the cluster passes through here; a
        cache hit does not, because it costs the upstream nothing and
        throttling it would only make the cache useless.
        """
        limiter = self.limits.get(upstream)
        if limiter is None:
            return True
        allowed, delay = limiter.reserve(self.config.upstream_wait)
        if not allowed:
            self._json(
                429,
                {"error": f"{upstream} is being asked too fast; retry shortly"},
                {"Retry-After": str(max(1, int(delay + 0.999)))},
            )
            return False
        if delay:
            time.sleep(delay)
        return True

    def _steamgriddb(self, rest: str) -> None:
        if not self.config.steamgriddb_key:
            self._problem(503, "this proxy holds no steamgriddb key")
            return
        url = f"{self.config.steamgriddb_url}/api/v2/{strip_prefix(rest, 'api/v2/')}"
        headers = {"Authorization": f"Bearer {self.config.steamgriddb_key}"}
        if not self.config.upstream_cache_dir or self.command != "GET":
            if not self._paced("steamgriddb"):
                return
            self._forward(url, headers, None)
            return

        # The cache is permanent and keyed on the whole question — path and
        # query — because the answer is art that does not change and quota
        # that does not come back. Only a 200 is remembered: a 404 today may
        # be somebody uploading the grid tomorrow, and an error never earns
        # permanence. Every client already caches its own hits forever; this
        # is the fleet-wide copy, so N machines cost one upstream question.
        key = hashlib.sha256(rest.encode()).hexdigest()
        cached = _cache_get(self.config.upstream_cache_dir, key)
        if cached is not None:
            payload, kind = cached
            self._send(200, payload, kind, {"X-Gotg-Cache": "hit"})
            return

        if not self._paced("steamgriddb"):
            return
        answer = self._fetch(url, headers, None)
        if answer is None:
            return
        status, payload, kind = answer
        if status >= 300:
            # urllib raised for these: an error (or an unfollowed redirect)
            # is relayed and never remembered.
            self._send(status, payload, kind)
            return
        _cache_put(self.config.upstream_cache_dir, key, payload, kind)
        self._send(200, payload, kind, {"X-Gotg-Cache": "miss"})

    def _igdb(self, rest: str, body: bytes | None) -> None:
        if not (self.config.igdb_client_id and self.config.igdb_client_secret):
            self._problem(503, "this proxy holds no igdb credentials")
            return
        try:
            token = self.tokens.get(self.config)
        except (urllib.error.URLError, OSError, ValueError, KeyError) as error:
            self._problem(502, f"could not mint an igdb token: {error}")
            return
        if not self._paced("igdb"):
            return
        self._forward(
            f"{self.config.igdb_url}/v4/{strip_prefix(rest, 'v4/')}",
            {"Client-ID": self.config.igdb_client_id, "Authorization": f"Bearer {token}"},
            body,
        )

    # --- routing ------------------------------------------------------------

    def _handle(self) -> None:
        """Only routes: each decision below is a method or a pure function, and
        the order is the contract -- listener, then the pre-auth routes, then
        authentication, then the body, then the route."""
        path = self.path.lstrip("/")
        clean = path.split("?")[0]

        if self._listener_refuses(clean.split("/")[0]) or self._serve_pre_auth(clean):
            return

        # Checked before the route is looked at, so an unauthenticated caller
        # cannot learn which upstreams this proxy holds keys for by watching
        # which paths answer 404 and which answer 503.
        principal = self._principal()
        if principal is None:
            self._problem(401, "a bearer token is required", close=True)
            return

        prefix, rest = split_route(path)

        # The admin token opens /admin and nothing else: outside its prefix it
        # is indistinguishable from a wrong token, so it cannot be used to
        # read saves — and nobody else reaches /admin (the 403 lives there).
        if principal == "admin" and prefix != "admin":
            self._problem(401, "a bearer token is required", close=True)
            return

        sent, body = self._read_body(body_cap(prefix, self.command, self.store))
        if not sent:
            return

        if path_climbs(rest):
            self._problem(400, "the path climbs out of the API it is proxied to")
            return
        self._dispatch(prefix, rest, body, principal)

    def _listener_refuses(self, top: str) -> bool:
        """Which half of the service this listener is. "public" never has
        /admin -- gotg-api.dcraw.net reaches it straight from the internet,
        past anything Cloudflare could put in front -- and "admin" has
        nothing else: one more door to the saves and the upstream keys is not
        what a second listener is for. Decided before authentication, so
        neither answers differently for a token it should not take."""
        if self.listener == "public" and top == "admin":
            where = f": it is at {self.config.admin_url}/admin/" if self.config.admin_url else ""
            self._problem(404, f"administration is not on this listener{where}", close=True)
            return True
        if self.listener == "admin" and top not in ("admin", "healthz"):
            self._problem(404, "this listener serves administration only", close=True)
            return True
        return False

    def _serve_pre_auth(self, clean: str) -> bool:
        """The three routes answered before anybody is authenticated: the admin
        page, healthz and a claim. Whether one answered."""
        # The page and its parts: static, and holding no secret -- the token
        # is typed into it -- so before authentication, as healthz is. Not on
        # a deployment that holds no admin token (gotg-library): a page with
        # nothing behind it is only a question.
        if (
            self.listener != "public"
            and self.config.admin_token
            and self.command in ("GET", "HEAD")
            and clean in admin_page.ROUTES
        ):
            self.close_connection = True
            body, content_type = admin_page.ROUTES[clean]
            self._send_page(body, content_type)
            return True

        # Before authentication, and the only thing that is: kubelet has no
        # token, and a health check is not a credentialed operation.
        # Both replies below go out before the body is read, so they close the
        # connection: on kept-alive HTTP/1.1 the unread body would frame the
        # next request — behind an ingress that shares upstream connections,
        # that is request smuggling, not just the caller's own confusion.
        if clean == "healthz":
            self.close_connection = True
            self._send(200, b'{"ok":true}', "application/json")
            return True

        # The other pre-auth route: the claim code IS the credential, and only
        # as a POST — any other verb falls through to the 401 below. Same
        # close-before-body rule as healthz.
        if self.command == "POST" and clean.split("/")[0] == "claim":
            self.close_connection = True
            if clean == "claim":
                self._claim_from_body()
            else:
                # Kept for clients that predate the body form: it puts a live
                # credential in the request line, which access logs keep.
                self._claim(clean[len("claim/") :])
            return True
        return False

    def _read_body(self, max_body: int) -> tuple[bool, bytes | None]:
        """`(proceed, body)`. When not proceeding, the refusal has been sent
        (and closes the connection: the unread body would frame the next
        request). Auth has already passed by now, so the bigger caps are spent
        only on credentialed callers."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._problem(400, "malformed Content-Length", close=True)
            return False, None
        if length < 0:
            self._problem(400, "negative Content-Length", close=True)
            return False, None
        if length > max_body:
            self._problem(413, "request body too large", close=True)
            return False, None
        return True, self.rfile.read(length) if length else None

    def _dispatch(self, prefix: str, rest: str, body: bytes | None, principal: str) -> None:
        if prefix == "steamgriddb":
            self._steamgriddb(rest)
        elif prefix == "igdb":
            self._igdb(rest, body)
        elif prefix == "saves":
            self._saves(rest, body, self._saves_user(principal))
        elif prefix == "catalog":
            self._catalog(rest, body)
        elif prefix == "art":
            self._art(rest, body)
        elif prefix == "games":
            self._games(rest)
        elif prefix == "files":
            self._files(rest)
        elif prefix == "auth":
            self._auth(rest, principal)
        elif prefix == "admin":
            self._admin(rest, body, principal)
        else:
            self._problem(404, f"nothing is proxied at /{prefix}")

    def do_GET(self) -> None:  # noqa: N802
        self._handle()

    def do_POST(self) -> None:  # noqa: N802
        self._handle()

    def do_PUT(self) -> None:  # noqa: N802
        self._handle()

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle()

    def do_HEAD(self) -> None:  # noqa: N802
        self._handle()


def make_server(
    host: str,
    port: int,
    config: Config,
    store: SavesStore | None = None,
    catalog: CatalogStore | None = None,
    files_dir: Path | None = None,
    stream_slots: int = 4,
    token_store: TokenStore | None = None,
    art: ArtCache | None = None,
    auth_cache_ttl: float = 60.0,
    auth_neg_ttl: float = 5.0,
    listener: str = "both",
) -> ThreadingHTTPServer:
    """Threading, because one slow upstream must not block every other client.

    `listener` is which half of the service this socket is: "public" (no
    /admin), "admin" (only /admin), or "both" -- one socket, as every
    deployment was before administration moved to the tailnet."""
    if listener not in ("public", "admin", "both"):
        raise ValueError(f"not a listener: {listener!r}")
    handler = type(
        "BoundHandler",
        (Handler,),
        {
            "config": config,
            "tokens": TokenCache(),
            "store": store,
            "catalog": catalog,
            "files_dir": files_dir,
            "streams": threading.BoundedSemaphore(stream_slots),
            "scans": threading.BoundedSemaphore(1),
            "token_store": token_store,
            "art": art,
            # One bucket per upstream: they are different quotas, and a warm
            # run against SteamGridDB must not throttle an IGDB lookup.
            "limits": {
                name: RateLimiter(config.upstream_rate, config.upstream_burst) for name in ("steamgriddb", "igdb")
            },
            "auth_cache": {},
            "auth_cache_lock": threading.Lock(),
            "auth_cache_ttl": auth_cache_ttl,
            "auth_neg_ttl": auth_neg_ttl,
            "listener": listener,
        },
    )
    return ThreadingHTTPServer((host, port), handler)
