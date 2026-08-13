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

import hashlib
import hmac
import json
import os
import re
import sqlite3
import stat
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..catalog import CatalogStore, Conflict, SweepRefused
from ..saves import SavesStore
from ..tokens import TOKEN_RE, Absent, Claimed, TokenStore

USER_AGENT = "gotg-proxy/0.5.2"

# Bounded, because a request that never returns holds a thread open and enough
# of them stop the proxy answering anybody.
TIMEOUT = 20

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

# The one body read before anybody is authenticated: {"code": …} around a
# 49-character code.
CLAIM_MAX_BODY = 256

# A single byte-range request: bytes=N- or bytes=N-M. Multi-range answers 200
# with the whole file rather than a multipart body nothing here needs. The
# digit bound matters: int() on thousands of digits raises, and an absurd
# range should fall through to a plain 200, not a traceback.
RANGE_RE = re.compile(r"^bytes=([0-9]{1,18})-([0-9]{0,18})$")

# What may be served from the files directory: keys and firmware names.
# No leading dot by construction (the first class excludes it), no slash by
# split, and the directory a person curates is the real allowlist.
FILES_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def open_contained(path: Path, root: Path) -> int | None:
    """An fd for a regular file provably under root, or None.

    The check that counts is on what was actually opened: O_NOFOLLOW refuses a
    symlink as the final component, and the /proc re-check catches a
    retargeted directory on the way there — a path checked and then opened is
    two syscalls with a race between them.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError:
        return None
    real = Path(os.path.realpath(f"/proc/self/fd/{fd}"))
    if not stat.S_ISREG(os.fstat(fd).st_mode) or not real.is_relative_to(Path(os.path.realpath(root))):
        os.close(fd)
        return None
    return fd


# Refresh a token with less than this left. IGDB issues them for about sixty
# days, so a day of slack costs nothing and removes the race where a token
# expires between the check and the upstream call.
REFRESH_MARGIN = 24 * 60 * 60

STEAMGRIDDB_URL = "https://www.steamgriddb.com"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Relay a 3xx instead of following it.

    urllib's default redirect handler copies every header except Content-* onto
    the new request — Authorization included, even when the Location crosses
    hosts. Behind a credential-injecting proxy that is the whole failure: one
    open redirect on the upstream and the key walks off to any host on the
    internet. The client can follow the redirect itself, without our header.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ARG002
        return None


_OPENER = urllib.request.build_opener(_NoRedirect())


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


def path_climbs(rest: str) -> bool:
    """Whether a path tries to escape the API prefix it will be appended to.

    The path is attacker-controlled input to a request that carries a real
    credential, and it is forwarded still percent-encoded — the upstream is the
    one that decodes it. So the check is on the decoded form, or `%2e%2e`
    climbs exactly as well as `..` while the check watches nothing happen.
    """
    decoded = urllib.parse.unquote(rest.split("?")[0])
    return any(segment == ".." for segment in decoded.split("/"))


IGDB_URL = "https://api.igdb.com"
IGDB_TOKEN_URL = "https://id.twitch.tv/oauth2/token"


@dataclass
class Config:
    """What this proxy holds. Everything but the client token is optional: an
    upstream with no credentials is one this deployment does not serve."""

    token: str
    steamgriddb_key: str = ""
    steamgriddb_url: str = STEAMGRIDDB_URL
    igdb_client_id: str = ""
    igdb_client_secret: str = ""
    igdb_url: str = IGDB_URL
    igdb_token_url: str = IGDB_TOKEN_URL
    # The write credential for the catalog. Lives in one CronJob Secret where
    # the client token lives on every laptop; catalog writes without it answer
    # 503 rather than ever falling back to the client token.
    index_token: str = ""
    # Mints and revokes per-person tokens, valid only under /admin — an admin
    # token that could also read saves would be one more shared secret.
    admin_token: str = ""
    # The library pod's pointer at the pod holding the token store: a bearer
    # no local check recognizes is asked about at {auth_url}/auth/whoami.
    auth_url: str = ""
    # Where clients should fetch /games and /files from, reported in the
    # catalog reply. Set when the control plane sits behind a proxy the byte
    # streams must bypass; empty means bytes come from the same url.
    files_url: str = ""
    # A VPN-fronted byte host has no fixed port: the tunnel's NAT-PMP lease
    # assigns one and reassigns it on reconnect. gluetun writes the current
    # port here, and it is read per catalog GET — never cached — so a client's
    # re-read after a failed download gets wherever the bytes live now.
    files_port_file: str = ""

    def validate(self) -> Config:
        if self.files_url and not self.files_url.startswith(("http://", "https://")):
            raise ValueError(f"GOTG_FILES_URL is not an http(s) url: {self.files_url!r}")
        if not self.token:
            raise ValueError(
                "no client token set. Refusing to start: an empty token "
                "authenticates everybody, which makes this an open relay for "
                "somebody else's API quota."
            )
        if self.index_token and self.index_token == self.token:
            raise ValueError(
                "the index token equals the client token. Refusing to start: "
                "that would let every client rewrite the catalog."
            )
        if self.admin_token and self.admin_token in (self.token, self.index_token):
            raise ValueError(
                "the admin token equals another credential. Refusing to start: "
                "minting tokens must need more than holding one."
            )
        return self


@dataclass
class TokenCache:
    """The IGDB access token, minted on demand and kept until it is nearly out.

    Holding this is the reason the service exists rather than each client doing
    its own exchange — and the lock is the reason three simultaneous requests on
    a cold start mint one token rather than three.
    """

    value: str = ""
    expires_at: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def get(self, config: Config) -> str:
        with self.lock:
            if self.value and time.time() < self.expires_at - REFRESH_MARGIN:
                return self.value

            body = urllib.parse.urlencode(
                {
                    "client_id": config.igdb_client_id,
                    "client_secret": config.igdb_client_secret,
                    "grant_type": "client_credentials",
                }
            ).encode()
            request = urllib.request.Request(
                config.igdb_token_url,
                data=body,
                headers={"User-Agent": USER_AGENT},
            )
            with _OPENER.open(request, timeout=TIMEOUT) as response:  # noqa: S310
                payload = json.loads(response.read())

            self.value = payload["access_token"]
            self.expires_at = time.time() + float(payload.get("expires_in", 0))
            return self.value


def strip_prefix(rest: str, prefix: str) -> str:
    """Allow a caller to include the upstream's own path prefix, or not.

    `gotg steam art --base-url <proxy>/steamgriddb` builds `/api/v2/...` itself,
    because that is what it builds for the real service — so without this the
    proxy would need a client change to be usable at all, which is the one thing
    it was supposed to avoid.
    """
    return rest[len(prefix) :] if rest.startswith(prefix) else rest


class Handler(BaseHTTPRequestHandler):
    config: Config
    tokens: TokenCache
    store: SavesStore | None
    catalog: CatalogStore | None
    files_dir: Path | None
    streams: threading.BoundedSemaphore
    token_store: TokenStore | None
    auth_cache: dict
    auth_cache_lock: threading.Lock
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

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        # A HEAD response is bodiless whatever the code: writing the JSON of a
        # 404 here would sit on the kept-alive connection and be parsed as the
        # start of the next response.
        if self.command != "HEAD":
            self.wfile.write(body)

    def _problem(self, code: int, message: str, *, close: bool = False) -> None:
        # close: for rejections raised before the request body is read. On a
        # kept-alive HTTP/1.1 connection the unread body would otherwise frame
        # the next request, so the client sees a reset instead of this reply.
        if close:
            self.close_connection = True
        self._send(code, json.dumps({"error": message}).encode(), "application/json")

    # --- who is asking ------------------------------------------------------

    def _bearer(self) -> bytes:
        # Bytes, because compare_digest on str raises on non-ASCII — and the
        # header arrives latin-1 decoded, so a stray \xff from an
        # unauthenticated caller would otherwise crash the thread instead of
        # earning its 401.
        header = self.headers.get("Authorization", "")
        token = header[len("Bearer ") :] if header.startswith("Bearer ") else ""
        return token.encode("latin-1", "replace")

    def _authenticated(self) -> bool:
        return self._principal() is not None

    def _principal(self) -> str | None:
        """Who this bearer is: "legacy" (the shared env token), "indexer",
        "admin", a per-person token's name, or None. The names the store can
        mint exclude the three sentinels by NAME_RE, so they cannot collide.
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

    def _forward(self, url: str, headers: dict[str, str], body: bytes | None) -> None:
        request = urllib.request.Request(url, data=body, method=self.command)
        request.add_header("User-Agent", USER_AGENT)
        for name, value in headers.items():
            request.add_header(name, value)

        try:
            with _OPENER.open(request, timeout=TIMEOUT) as response:  # noqa: S310
                payload = response.read()
                kind = safe_content_type(response.headers.get("Content-Type", ""))
                self._send(response.status, payload, kind)
        except urllib.error.HTTPError as error:
            # Relayed rather than swallowed: a 404 from SteamGridDB means the
            # game is not there, which the client needs to hear as a 404.
            payload = error.read()
            kind = safe_content_type(error.headers.get("Content-Type", "")) if error.headers else "application/json"
            self._send(error.code, payload, kind)
        except (urllib.error.URLError, OSError, ValueError) as error:
            self._problem(502, f"upstream unreachable: {error}")

    def _steamgriddb(self, rest: str) -> None:
        if not self.config.steamgriddb_key:
            self._problem(503, "this proxy holds no steamgriddb key")
            return
        self._forward(
            f"{self.config.steamgriddb_url}/api/v2/{strip_prefix(rest, 'api/v2/')}",
            {"Authorization": f"Bearer {self.config.steamgriddb_key}"},
            None,
        )

    def _igdb(self, rest: str, body: bytes | None) -> None:
        if not (self.config.igdb_client_id and self.config.igdb_client_secret):
            self._problem(503, "this proxy holds no igdb credentials")
            return
        try:
            token = self.tokens.get(self.config)
        except (urllib.error.URLError, OSError, ValueError, KeyError) as error:
            self._problem(502, f"could not mint an igdb token: {error}")
            return
        self._forward(
            f"{self.config.igdb_url}/v4/{strip_prefix(rest, 'v4/')}",
            {"Client-ID": self.config.igdb_client_id, "Authorization": f"Bearer {token}"},
            body,
        )

    # --- the saves store ----------------------------------------------------

    def _saves(self, rest: str, body: bytes | None) -> None:
        """`/saves/<attr>` is the whole surface: PUT is `.save()`, GET is
        `.retrieve()`, and `/saves/<attr>/meta` says what is current without
        moving the bytes. Conflicts are answered here — a PUT carries the hash
        of the generation it descends from, and a parent that is not the head
        is a 409 carrying what the head actually is."""
        if self.store is None:
            self._problem(503, "this service holds no saves store")
            return

        parts = urllib.parse.urlsplit("/" + rest)
        segments = parts.path.strip("/").split("/")
        attr = segments[0]
        want_meta = segments[1:] == ["meta"]
        if segments[1:] not in ([], ["meta"]):
            self._problem(404, f"nothing lives at /saves/{parts.path.strip('/')}")
            return

        try:
            if self.command == "GET" and want_meta:
                meta = self.store.meta(attr)
                if meta is None:
                    self._problem(404, f"nothing has been pushed for {attr}")
                    return
                self._send(200, json.dumps(meta).encode(), "application/json")
            elif self.command == "GET":
                meta = self.store.meta(attr)
                path = self.store.bundle_path(attr)
                if meta is None or path is None:
                    self._problem(404, f"nothing has been pushed for {attr}")
                    return
                payload = path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/zstd")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("X-Gotg-Generation", str(meta["generation"]))
                self.send_header("X-Gotg-Hash", meta["hash"])
                self.end_headers()
                self.wfile.write(payload)
            elif self.command == "PUT":
                if not body:
                    self._problem(400, "a save must arrive with its bundle as the body")
                    return
                published = self.store.save(
                    attr,
                    body,
                    parent=self.headers.get("X-Gotg-Parent", ""),
                    # Stored and echoed back in metadata, so it is made
                    # printable and short rather than trusted.
                    device="".join(c for c in self.headers.get("X-Gotg-Device", "") if c.isprintable())[:32],
                    force="force=1" in parts.query.split("&"),
                )
                self._send(published.status, json.dumps(published.meta).encode(), "application/json")
            else:
                self._problem(405, f"{self.command} is not something the saves store answers")
        except ValueError as error:
            self._problem(400, str(error))
        except OSError as error:
            self._problem(500, str(error))

    def _files_url(self) -> str:
        """The byte host as it stands right now, or "" for none.

        With a port file configured, an unreadable or nonsense port means the
        tunnel is down — advertising a byte host that cannot answer would turn
        every download into a hang, so none is advertised and the client says
        which host it could not reach.
        """
        base = self.config.files_url
        if not base or not self.config.files_port_file:
            return base
        try:
            port = int(Path(self.config.files_port_file).read_text().strip())
        except (OSError, ValueError):
            print(f"files_url withheld: no forwarded port at {self.config.files_port_file}", file=sys.stderr)
            return ""
        if not 0 < port < 65536:
            print(f"files_url withheld: nonsense forwarded port {port}", file=sys.stderr)
            return ""
        return f"{base}:{port}"

    def _catalog(self, rest: str, body: bytes | None) -> None:
        """`/catalog` reads for everyone; writes for the index principal only.

        PUT upserts one entry, and answers 409 when the stored entry points at
        different bytes — two torrents producing the same id is a real error
        the old hardlink collision used to surface, and an upsert must not
        swallow it. The sweep reports what a completed scan did not confirm;
        it deletes nothing.
        """
        if self.catalog is None:
            self._problem(503, "this service holds no catalog")
            return

        parts = urllib.parse.urlsplit("/" + rest)
        segments = [s for s in parts.path.strip("/").split("/") if s]
        query = parts.query.split("&")

        def needs_index() -> bool:
            if not self.config.index_token:
                self._problem(503, "no index token is configured; the catalog is read-only")
                return False
            if not self._is_index():
                self._problem(403, "catalog writes need the index token")
                return False
            return True

        try:
            if self.command == "GET" and not segments:
                full = "full=1" in query
                if full and not self._is_index():
                    self._problem(403, "the full catalog view needs the index token")
                    return
                view = self.catalog.view(full=full)
                # The catalog names its own byte host, so clients need no
                # files configuration — and a moving host (a VPN-fronted one
                # changes address on reconnect) costs a re-read, not a rewrite.
                files_url = self._files_url()
                if files_url:
                    view["files_url"] = files_url
                self._send(200, json.dumps(view).encode(), "application/json")
            elif self.command == "PUT" and len(segments) == 2:
                if not needs_index():
                    return
                if not body:
                    self._problem(400, "an entry must arrive as the request body")
                    return
                entry = self.catalog.upsert(
                    segments[0],
                    segments[1],
                    json.loads(body),
                    force="force=1" in query,
                )
                self._send(200, json.dumps(entry).encode(), "application/json")
            elif self.command == "POST" and segments == ["sweep"]:
                if not needs_index():
                    return
                payload = json.loads(body) if body else {}
                if not isinstance(payload, dict):
                    self._problem(400, "the sweep body must be a JSON object")
                    return
                report = self.catalog.sweep(
                    str(payload.get("since", "")),
                    confirm="confirm=1" in query,
                )
                self._send(200, json.dumps(report).encode(), "application/json")
            elif self.command == "DELETE" and len(segments) == 2:
                if not needs_index():
                    return
                if self.catalog.delete(segments[0], segments[1]):
                    self._send(200, b'{"deleted":true}', "application/json")
                else:
                    self._problem(404, f"no entry {segments[0]}/{segments[1]}")
            else:
                self._problem(404, f"nothing lives at /catalog/{parts.path.strip('/')}")
        except Conflict as conflict:
            self._send(
                409,
                json.dumps({"error": str(conflict), "stored": conflict.stored}).encode(),
                "application/json",
            )
        except SweepRefused as refused:
            self._problem(409, str(refused))
        # JSONDecodeError is a ValueError; RecursionError is what a deeply
        # nested body raises inside json.loads, and it must earn a 400 rather
        # than a thread traceback.
        except (ValueError, RecursionError) as error:
            message = "malformed body" if isinstance(error, RecursionError) else str(error)
            self._problem(400, message)
        # Neither message reaches the client: a storage error string typically
        # embeds server paths.
        except sqlite3.Error as error:
            print(f"catalog storage error: {error}", file=sys.stderr)
            self._problem(500, "catalog storage error")
        except OSError as error:
            print(f"catalog error: {error}", file=sys.stderr)
            self._problem(500, "catalog storage error")

    def _games(self, rest: str) -> None:
        """`GET /games/<platform>/<id>/<name>` streams one member file.

        Everything about the response is resumable: Accept-Ranges, a single
        byte range honored with 206, the sha256 as an ETag so If-Range makes a
        resume against changed bytes restart cleanly instead of splicing two
        files together.
        """
        if self.catalog is None:
            self._problem(503, "this service holds no catalog")
            return
        if self.command not in ("GET", "HEAD"):
            self._problem(405, f"{self.command} is not something the library answers")
            return

        parts = urllib.parse.urlsplit("/" + rest)
        segments = [urllib.parse.unquote(s) for s in parts.path.strip("/").split("/") if s]
        if len(segments) < 3:
            self._problem(404, "a file lives at /games/<platform>/<id>/<name>")
            return
        # A member name may nest — a WiiU dump is fetched as its tree.
        platform, game_id = segments[0], segments[1]
        name = "/".join(segments[2:])

        found = self.catalog.open_member(platform, game_id, name)
        if found is None:
            self._problem(404, f"no such file: {platform}/{game_id}/{name}")
            return
        meta, fd = found
        if fd is None:
            # The row exists and the bytes do not: the index is stale, and
            # that is server news, not client news.
            print(f"catalog names a missing file: {platform}/{game_id}/{name}", file=sys.stderr)
            self._problem(404, f"no such file: {platform}/{game_id}/{name}")
            return
        try:
            self._stream_fd(fd, name, meta.get("sha256"))
        finally:
            os.close(fd)

    def _files(self, rest: str) -> None:
        """`GET /files/<platform>/<name>` — keys and firmware, hand-placed.

        The curated directory is the allowlist; this only insists the name is
        shaped like a file someone would place there, and that what opens is a
        regular file inside it. 404 for everything absent, so the client's
        missing-keys path stays a warning.
        """
        if self.files_dir is None:
            self._problem(503, "this service holds no files directory")
            return
        if self.command not in ("GET", "HEAD"):
            self._problem(405, f"{self.command} is not something the files answer")
            return

        parts = urllib.parse.urlsplit("/" + rest)
        segments = [urllib.parse.unquote(s) for s in parts.path.strip("/").split("/") if s]
        if len(segments) != 2:
            self._problem(404, "a file lives at /files/<platform>/<name>")
            return
        platform, name = segments
        if not re.match(r"^[a-z0-9][a-z0-9_-]{0,15}$", platform) or not FILES_NAME_RE.match(name):
            self._problem(404, f"no such file: {platform}/{name}")
            return

        fd = open_contained(self.files_dir / platform / name, self.files_dir)
        if fd is None:
            self._problem(404, f"no such file: {platform}/{name}")
            return
        try:
            self._stream_fd(fd, name, None)
        finally:
            os.close(fd)

    def _stream_fd(self, fd: int, name: str, sha256: str | None) -> None:
        size = os.fstat(fd).st_size
        etag = f'"{sha256}"' if sha256 else None

        start, end, status = 0, size - 1, 200
        wanted = RANGE_RE.match(self.headers.get("Range", ""))
        if wanted:
            # If-Range with a stale validator means the bytes changed since
            # the client's partial: send the whole file rather than splice.
            if_range = self.headers.get("If-Range", "")
            if not if_range or (etag is not None and if_range == etag):
                start = int(wanted.group(1))
                if wanted.group(2):
                    end = min(int(wanted.group(2)), size - 1)
                if start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if start > end:
                    # An inverted range makes the whole header invalid, and an
                    # invalid Range is ignored, not refused (RFC 9110 §14.1.1).
                    start, end = 0, size - 1
                else:
                    status = 206

        streaming = self.command == "GET"
        # The cap is what keeps the saves and artwork halves answering while
        # multi-gigabyte pulls are in flight — a thread per TCP connection has
        # no other limit.
        if streaming and not self.streams.acquire(blocking=False):
            self.send_response(503)
            self.send_header("Retry-After", "5")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        try:
            count = end - start + 1
            self.send_response(status)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(count))
            self.send_header("Accept-Ranges", "bytes")
            if etag:
                self.send_header("ETag", etag)
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header(
                "Content-Disposition",
                f"attachment; filename*=UTF-8''{urllib.parse.quote(name.rsplit('/', 1)[-1])}",
            )
            self.end_headers()
            if streaming:
                self._send_range(fd, start, count)
        finally:
            if streaming:
                self.streams.release()

    def _send_range(self, fd: int, offset: int, remaining: int) -> None:
        # socket.sendfile rather than a hand-rolled os.sendfile loop: it
        # handles partial sends, EAGAIN under the socket timeout, and falls
        # back to plain send() where sendfile is unsupported — and the
        # timeout is what stops a client that quit reading from holding a
        # stream slot forever.
        self.wfile.flush()
        if remaining <= 0:
            # socket.sendfile treats a falsy count as "the whole file".
            return
        sent = 0
        try:
            with open(fd, "rb", buffering=0, closefd=False) as src:
                sent = self.connection.sendfile(src, offset, remaining)
        except OSError:
            pass
        if sent < remaining:
            # A short body desynchronizes a kept-alive connection.
            self.close_connection = True

    # --- routing ------------------------------------------------------------

    def _handle(self) -> None:
        path = self.path.lstrip("/")

        # Before authentication, and the only thing that is: kubelet has no
        # token, and a health check is not a credentialed operation.
        # Both replies below go out before the body is read, so they close the
        # connection: on kept-alive HTTP/1.1 the unread body would frame the
        # next request — behind an ingress that shares upstream connections,
        # that is request smuggling, not just the caller's own confusion.
        if path.split("?")[0] == "healthz":
            self.close_connection = True
            self._send(200, b'{"ok":true}', "application/json")
            return

        # The other pre-auth route: the claim code IS the credential, and only
        # as a POST — any other verb falls through to the 401 below. Same
        # close-before-body rule as healthz.
        clean = path.split("?")[0]
        if self.command == "POST" and clean.split("/")[0] == "claim":
            self.close_connection = True
            if clean == "claim":
                self._claim_from_body()
            else:
                # Kept for clients that predate the body form: it puts a live
                # credential in the request line, which access logs keep.
                self._claim(clean[len("claim/") :])
            return

        # Checked before the route is looked at, so an unauthenticated caller
        # cannot learn which upstreams this proxy holds keys for by watching
        # which paths answer 404 and which answer 503.
        principal = self._principal()
        if principal is None:
            self._problem(401, "a bearer token is required", close=True)
            return

        prefix, _, rest = path.partition("/")
        # A query on a bare prefix — /catalog?full=1 — otherwise rides along
        # in the prefix and matches no route.
        if "?" in prefix:
            prefix, _, query = prefix.partition("?")
            rest = f"?{query}"

        # The admin token opens /admin and nothing else: outside its prefix it
        # is indistinguishable from a wrong token, so it cannot be used to
        # read saves — and nobody else reaches /admin (the 403 lives there).
        if principal == "admin" and prefix != "admin":
            self._problem(401, "a bearer token is required", close=True)
            return

        # A save bundle and a catalog entry are the two bodies allowed to be
        # big — a retail WiiU tree runs to ~10k file rows at ~350 bytes each.
        # Everything else keeps the small cap, because an IGDB query is a line
        # or two. Auth has already passed by the time a body is read, so the
        # bigger caps are spent only on credentialed callers.
        max_body = MAX_BODY
        if prefix == "saves" and self.command == "PUT" and self.store is not None:
            max_body = self.store.max_bytes
        elif prefix == "catalog" and self.command == "PUT":
            max_body = CATALOG_MAX_BODY

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._problem(400, "malformed Content-Length", close=True)
            return
        if length < 0:
            self._problem(400, "negative Content-Length", close=True)
            return
        if length > max_body:
            self._problem(413, "request body too large", close=True)
            return
        body = self.rfile.read(length) if length else None

        if path_climbs(rest):
            self._problem(400, "the path climbs out of the API it is proxied to")
            return
        if prefix == "steamgriddb":
            self._steamgriddb(rest)
        elif prefix == "igdb":
            self._igdb(rest, body)
        elif prefix == "saves":
            self._saves(rest, body)
        elif prefix == "catalog":
            self._catalog(rest, body)
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

    # --- per-person tokens --------------------------------------------------

    def _claim_from_body(self) -> None:
        """The code arrives as a body so that no log holds it. Read before
        authentication, so it carries its own cap rather than the post-auth
        ones."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._problem(400, "malformed Content-Length")
            return
        if not 0 < length <= CLAIM_MAX_BODY:
            self._problem(400, 'a claim is {"code": "gotgi_…"}')
            return
        try:
            code = json.loads(self.rfile.read(length)).get("code", "")
        except (ValueError, AttributeError):
            code = ""
        if not isinstance(code, str) or not code:
            self._problem(400, 'a claim is {"code": "gotgi_…"}')
            return
        self._claim(code)

    def _claim(self, code: str) -> None:
        """One POST, one token. 410 for a code that existed (reuse means the
        real holder should hear about it — logged), 404 for one that never
        did; the split leaks only to whoever already holds the code."""
        if self.token_store is None:
            self._problem(503, "this deployment holds no token store")
            return
        outcome = self.token_store.claim(code)
        if outcome is Absent:
            self._problem(404, "no such claim code")
            return
        if outcome is Claimed:
            print(f"claim code reused or expired: …{code[-4:]}", file=sys.stderr)
            self._problem(410, "this claim code was already used or has expired")
            return
        name, token = outcome
        self._send(200, json.dumps({"name": name, "token": token}).encode(), "application/json")

    def _auth(self, rest: str, principal: str) -> None:
        if rest.split("?")[0] != "whoami":
            self._problem(404, "nothing lives at /auth but whoami")
            return
        if self.command not in ("GET", "HEAD"):
            self._problem(405, "whoami is a GET")
            return
        self._send(200, json.dumps({"name": principal}).encode(), "application/json")

    def _needs_admin(self, principal: str) -> bool:
        if not self.config.admin_token:
            self._problem(503, "no admin token is configured; token administration is off")
            return True
        if principal != "admin":
            self._problem(403, "token administration needs the admin token")
            return True
        if self.token_store is None:
            self._problem(503, "this deployment holds no token store")
            return True
        return False

    def _admin(self, rest: str, body: bytes | None, principal: str) -> None:
        if self._needs_admin(principal):
            return
        segments = rest.split("?")[0].strip("/").split("/")

        if segments == ["invites"]:
            if self.command != "POST":
                self._problem(405, "minting an invite is a POST")
                return
            try:
                asked = json.loads(body or b"{}")
                name = asked.get("name", "")
                ttl_days = float(asked.get("ttl_days", 7))
            except (ValueError, AttributeError, TypeError):
                self._problem(400, 'the body is {"name": ..., "ttl_days"?: ...}')
                return
            # False for NaN, refuses Infinity: json accepts both, and either
            # overflows int() further down as an uncaught OverflowError.
            if not 0 < ttl_days <= 3650:
                self._problem(400, "ttl_days must be between 0 and 3650")
                return
            try:
                code = self.token_store.mint_invite(name, ttl=ttl_days * 86400)
            except (ValueError, TypeError, OverflowError) as error:
                self._problem(400, str(error))
                return
            self._send(200, json.dumps({"name": name, "code": code}).encode(), "application/json")
            return

        if segments == ["tokens"]:
            if self.command not in ("GET", "HEAD"):
                self._problem(405, "the token list is a GET")
                return
            self._send(200, json.dumps({"tokens": self.token_store.tokens()}).encode(), "application/json")
            return

        if len(segments) == 2 and segments[0] == "tokens":
            if self.command != "DELETE":
                self._problem(405, "revoking is a DELETE")
                return
            if self.token_store.revoke(segments[1]):
                self._send(200, json.dumps({"revoked": segments[1]}).encode(), "application/json")
            else:
                self._problem(404, f"nothing live to revoke for {segments[1]!r}")
            return

        self._problem(404, "nothing lives at /admin but invites and tokens")

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
    auth_cache_ttl: float = 60.0,
    auth_neg_ttl: float = 5.0,
) -> ThreadingHTTPServer:
    """Threading, because one slow upstream must not block every other client."""
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
            "token_store": token_store,
            "auth_cache": {},
            "auth_cache_lock": threading.Lock(),
            "auth_cache_ttl": auth_cache_ttl,
            "auth_neg_ttl": auth_neg_ttl,
        },
    )
    return ThreadingHTTPServer((host, port), handler)
