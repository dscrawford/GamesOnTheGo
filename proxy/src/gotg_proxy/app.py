"""A reverse proxy that holds the API credentials so the clients do not.

Every machine that runs `gotg steam art` otherwise needs its own SteamGridDB
key, and IGDB is worse: its access token is minted from a client id and secret
and expires in about sixty days, so each client would have to hold two secrets
and implement a refresh. Putting one service in the cluster in front of both
turns that into a single credential to rotate, in a Secret, in one place.

It is a reverse proxy rather than a forward one: a client does not configure it
and then reach arbitrary destinations through it, it simply *is* the API as far
as the client is concerned. `gotg steam art --base-url https://.../steamgriddb`
needs no code change to use it — it sends its own token, and this swaps in the
real one.

Which makes the important property this: **the client's token must never reach
an upstream, and an upstream's key must never reach a client.** Most of what is
here is in service of that.

Authentication is not optional. This sits on the public internet behind an
ingress, and an open relay would be somebody else's free SteamGridDB quota and,
eventually, our banned key.

Only the standard library. It is a few hundred requests a week from a handful of
machines; a framework and a WSGI server would be more moving parts than the job
has.
"""

from __future__ import annotations

import hmac
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

USER_AGENT = "gotg-proxy/0.1.0"

# Bounded, because a request that never returns holds a thread open and enough
# of them stop the proxy answering anybody.
TIMEOUT = 20

# Refresh a token with less than this left. IGDB issues them for about sixty
# days, so a day of slack costs nothing and removes the race where a token
# expires between the check and the upstream call.
REFRESH_MARGIN = 24 * 60 * 60

STEAMGRIDDB_URL = "https://www.steamgriddb.com"
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

    def validate(self) -> Config:
        if not self.token:
            raise ValueError(
                "no client token set. Refusing to start: an empty token "
                "authenticates everybody, which makes this an open relay for "
                "somebody else's API quota."
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
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310
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

    server_version = USER_AGENT
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):  # noqa: A002
        # One line per request, without the client token ever being one of the
        # things logged.
        print(f"{self.command} {self.path.split('?')[0]} {args[1] if len(args) > 1 else ''}".strip())

    # --- replies ------------------------------------------------------------

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _problem(self, code: int, message: str) -> None:
        self._send(code, json.dumps({"error": message}).encode(), "application/json")

    # --- who is asking ------------------------------------------------------

    def _authenticated(self) -> bool:
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return False
        # compare_digest rather than ==: a plain comparison returns early on the
        # first wrong byte, which leaks the secret a character at a time.
        return hmac.compare_digest(header[len("Bearer ") :], self.config.token)

    # --- forwarding ---------------------------------------------------------

    def _forward(self, url: str, headers: dict[str, str], body: bytes | None) -> None:
        request = urllib.request.Request(url, data=body, method=self.command)
        request.add_header("User-Agent", USER_AGENT)
        for name, value in headers.items():
            request.add_header(name, value)

        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310
                payload = response.read()
                kind = response.headers.get("Content-Type", "application/octet-stream")
                self._send(response.status, payload, kind)
        except urllib.error.HTTPError as error:
            # Relayed rather than swallowed: a 404 from SteamGridDB means the
            # game is not there, which the client needs to hear as a 404.
            payload = error.read()
            kind = error.headers.get("Content-Type", "application/json") if error.headers else "application/json"
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

    # --- routing ------------------------------------------------------------

    def _handle(self) -> None:
        path = self.path.lstrip("/")

        # Before authentication, and the only thing that is: kubelet has no
        # token, and a health check is not a credentialed operation.
        if path.split("?")[0] == "healthz":
            self._send(200, b'{"ok":true}', "application/json")
            return

        # Checked before the route is looked at, so an unauthenticated caller
        # cannot learn which upstreams this proxy holds keys for by watching
        # which paths answer 404 and which answer 503.
        if not self._authenticated():
            self._problem(401, "a bearer token is required")
            return

        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None

        prefix, _, rest = path.partition("/")
        if prefix == "steamgriddb":
            self._steamgriddb(rest)
        elif prefix == "igdb":
            self._igdb(rest, body)
        else:
            self._problem(404, f"nothing is proxied at /{prefix}")

    def do_GET(self) -> None:  # noqa: N802
        self._handle()

    def do_POST(self) -> None:  # noqa: N802
        self._handle()


def make_server(host: str, port: int, config: Config) -> ThreadingHTTPServer:
    """Threading, because one slow upstream must not block every other client."""
    handler = type("BoundHandler", (Handler,), {"config": config, "tokens": TokenCache()})
    return ThreadingHTTPServer((host, port), handler)
