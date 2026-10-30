"""What the proxy holds, and the two constants every upstream call shares.

`Config` is the whole deployment as one value: credentials, directories, the
urls clients are told about, the pace. `USER_AGENT` and `TIMEOUT` live here so
the token cache need not import the handler it serves.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..tokens import NAME_RE

USER_AGENT = "gotg-proxy/0.5.5"

# Bounded, because a request that never returns holds a thread open and enough
# of them stop the proxy answering anybody.
TIMEOUT = 20


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
    # The write credential for the catalog. Lives in one CronJob Secret where
    # the client token lives on every laptop; catalog writes without it answer
    # 503 rather than ever falling back to the client token.
    index_token: str = ""
    # Mints and revokes per-person tokens, valid only under /admin — an admin
    # token that could also read saves would be one more shared secret.
    admin_token: str = ""
    # Reads everything a client can, and the catalog with server paths: for a
    # pod that has the library mounted and copies members from disk instead
    # of streaming them. Read-only, so a game running in that pod cannot use
    # it to rewrite the catalog.
    library_token: str = ""
    # Where /steamgriddb answers are kept, so a fleet of clients costs one
    # upstream question per asset against the shared key's quota. Empty means
    # no cache, which is every deployment before the volume existed.
    upstream_cache_dir: str = ""
    # The library pod's pointer at the pod holding the token store: a bearer
    # no local check recognizes is asked about at {auth_url}/auth/whoami.
    auth_url: str = ""
    # Which user's saves the break-glass legacy token reads and writes. On a
    # deployment whose history predates users it is the person whose saves
    # those already were; unset, legacy gets a namespace of its own.
    legacy_user: str = "legacy"
    # Where clients should fetch /games and /files from, reported in the
    # catalog reply. Set when the control plane sits behind a proxy the byte
    # streams must bypass; empty means bytes come from the same url.
    files_url: str = ""
    # A VPN-fronted byte host has no fixed port: the tunnel's NAT-PMP lease
    # assigns one and reassigns it on reconnect. gluetun writes the current
    # port here, and it is read per catalog GET — never cached — so a client's
    # re-read after a failed download gets wherever the bytes live now.
    files_port_file: str = ""
    # A byte host only some clients can reach — the library's NodePort on a
    # node's tailscale address — advertised ahead of files_url in files_urls.
    # A client tries it first and falls back; one outside the tailnet never
    # loses the universal host.
    files_preferred_url: str = ""
    # Where the fleet's tile pictures live — see artcache.py. Empty means this
    # deployment serves no art, which /art says with a 503.
    art_dir: str = ""
    # Where administration lives when it has a listener of its own (the
    # tailnet's): the public listener's 404 for /admin names it, so a CLI
    # pointed at the old url is told rather than left guessing.
    admin_url: str = ""
    # The url people reach the service on, which a claim link is built from:
    # the admin page is on another host and cannot know it otherwise.
    public_url: str = ""
    # Origins other than the service's own that a browser may call it from,
    # exactly as the browser sends them ("https://play.example.net"), or "*".
    # Empty is same-origin only: a SPA served by this service needs none, and
    # no CORS header is ever sent. /admin is never given CORS headers.
    cors_origins: tuple[str, ...] = ()
    # How hard anybody may lean on an upstream through this proxy, in requests
    # per second, with a burst for the ordinary case of one client opening a
    # grid. Cache hits are not counted: they cost the upstream nothing.
    # How hard anybody may lean on an upstream through this proxy, in requests
    # per second, with a burst for the ordinary case of one client opening a
    # grid. Cache hits are not counted: they cost the upstream nothing.
    upstream_rate: float = 2.0
    upstream_burst: float = 10.0
    # The longest a paced request will sit on a thread before being told 429
    # instead. Long enough that a warmer simply runs slower, short enough that
    # a burst cannot pin every thread in the pool.
    upstream_wait: float = 5.0
    # --- Web sign-in. All optional; every one unset is a service with no web
    # login, exactly as before. Set any and `validate` demands the rest. The
    # flow itself lives in the oauth module; this is only what it is told.
    # "github" (OAuth app) or "oidc" (needs oidc_issuer_url).
    oidc_provider: str = ""
    oidc_issuer_url: str = ""
    oidc_client_id: str = ""
    # Read from GOTG_OIDC_CLIENT_SECRET_FILE in a deployment, so it is a
    # mounted Secret and never in the environment, the store or the manifest.
    oidc_client_secret: str = ""
    # The callback registered with the provider: {public}/auth/callback.
    oidc_redirect_url: str = ""
    # Who may sign in: provider logins (GitHub usernames, or emails for oidc),
    # lower-cased. Empty with sign-in on is refused, not "everybody".
    oidc_allowlist: tuple[str, ...] = ()
    # Signs the session cookie. Also from a file (GOTG_SESSION_SECRET_FILE).
    session_secret: str = ""
    cookie_name: str = "gotg_session"
    # Secure by default: the cookie is for https behind a TLS-terminating
    # proxy. Turn it off only for http://localhost development.
    cookie_secure: bool = True
    cookie_samesite: str = "Lax"
    session_ttl: int = 604800
    # Explicit endpoints (override discovery / the GitHub defaults) and scopes.
    # A directory served at /ui/ instead of the files packaged with the service
    # (the deployment image's GOTG_WEB_DIR). Same layout as service/static.
    web_dir: str = ""
    # Token-store users whose signed-in cookie session may use /admin (never a
    # bearer, which is what the admin token is for). Needs sign-in on.
    admin_users: tuple[str, ...] = ()
    oidc_authorize_url: str = ""
    oidc_token_url: str = ""
    oidc_userinfo_url: str = ""
    oidc_scopes: str = ""

    @property
    def oidc_enabled(self) -> bool:
        return bool(self.oidc_provider)

    def validate(self) -> Config:
        if self.files_url and not self.files_url.startswith(("http://", "https://")):
            raise ValueError(f"GOTG_FILES_URL is not an http(s) url: {self.files_url!r}")
        if self.files_preferred_url and not self.files_preferred_url.startswith(("http://", "https://")):
            raise ValueError(f"GOTG_FILES_PREFERRED_URL is not an http(s) url: {self.files_preferred_url!r}")
        for origin in self.cors_origins:
            if origin != "*" and not (origin.startswith(("http://", "https://")) and not origin.endswith("/")):
                raise ValueError(f"GOTG_CORS_ORIGINS wants origins like https://host[:port], not {origin!r}")
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
        if self.library_token and self.library_token in (self.token, self.index_token, self.admin_token):
            raise ValueError(
                "the library token equals another credential. Refusing to start: "
                "seeing server paths must not come with any other power."
            )
        if self.web_dir and not Path(self.web_dir).is_dir():
            raise ValueError(f"GOTG_WEB_DIR is not a directory: {self.web_dir!r}")
        self._validate_oidc()
        if self.admin_users:
            if not self.oidc_enabled:
                raise ValueError("GOTG_ADMIN_USERS needs web sign-in; without it nobody has a session")
            bad = [u for u in self.admin_users if not NAME_RE.match(u)]
            if bad:
                raise ValueError(f"GOTG_ADMIN_USERS has names no user can have: {', '.join(bad)}")
        return self

    def _validate_oidc(self) -> None:
        untouched = Config(token=self.token)
        names = [f for f in _OIDC_FIELDS if getattr(self, f) != getattr(untouched, f)]
        if not names:
            return
        if self.oidc_provider not in ("github", "oidc"):
            raise ValueError(f"GOTG_OIDC_PROVIDER must be github or oidc, not {self.oidc_provider!r}")
        needed = {
            "GOTG_OIDC_CLIENT_ID": self.oidc_client_id,
            "GOTG_OIDC_CLIENT_SECRET[_FILE]": self.oidc_client_secret,
            "GOTG_OIDC_REDIRECT_URL": self.oidc_redirect_url,
            "GOTG_SESSION_SECRET[_FILE]": self.session_secret,
            "GOTG_OIDC_ALLOWLIST": self.oidc_allowlist,
        }
        explicit = self.oidc_authorize_url and self.oidc_token_url and self.oidc_userinfo_url
        if self.oidc_provider == "oidc" and not explicit:
            needed["GOTG_OIDC_ISSUER_URL (or all three endpoint urls)"] = self.oidc_issuer_url
        missing = [k for k, v in needed.items() if not v]
        if missing:
            raise ValueError(f"web sign-in is half configured; missing {', '.join(missing)}")
        for name, url in (
            ("GOTG_OIDC_REDIRECT_URL", self.oidc_redirect_url),
            ("GOTG_OIDC_ISSUER_URL", self.oidc_issuer_url),
        ):
            if url and not url.startswith(("http://", "https://")):
                raise ValueError(f"{name} is not an http(s) url: {url!r}")
        if self.cookie_secure and self.oidc_redirect_url.startswith("http://"):
            raise ValueError(
                "GOTG_OIDC_REDIRECT_URL is http but cookies are Secure, so a browser would drop the "
                "session. Use https, or set GOTG_COOKIE_SECURE=0 for local development."
            )
        if len(self.session_secret) < 32:
            raise ValueError("the session secret is shorter than 32 characters")
        if self.session_secret in (self.token, self.index_token, self.admin_token, self.library_token):
            raise ValueError("the session secret equals another credential. Refusing to start.")
        if self.cookie_samesite not in ("Lax", "Strict", "None"):
            raise ValueError(f"GOTG_COOKIE_SAMESITE must be Lax, Strict or None, not {self.cookie_samesite!r}")
        if self.cookie_samesite == "None" and not self.cookie_secure:
            raise ValueError("SameSite=None needs Secure cookies")
        if self.session_ttl <= 0:
            raise ValueError("GOTG_SESSION_TTL must be positive")


_OIDC_FIELDS = (
    "oidc_provider",
    "oidc_issuer_url",
    "oidc_client_id",
    "oidc_client_secret",
    "oidc_redirect_url",
    "oidc_allowlist",
    "session_secret",
)
