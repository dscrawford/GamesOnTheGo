"""What the proxy holds, and the two constants every upstream call shares.

`Config` is the whole deployment as one value: credentials, directories, the
urls clients are told about, the pace. It is its own file because it had grown
to a hundred lines of commented fields in the middle of the handler's module,
and because the token cache and the handler both need `USER_AGENT` and
`TIMEOUT` -- which, left in app.py, would have made the cache import the
handler it serves.
"""

from __future__ import annotations

from dataclasses import dataclass

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
    # How hard anybody may lean on an upstream through this proxy, in requests
    # per second, with a burst for the ordinary case of one client opening a
    # grid. Cache hits are not counted: they cost the upstream nothing.
    upstream_rate: float = 2.0
    upstream_burst: float = 10.0
    # The longest a paced request will sit on a thread before being told 429
    # instead. Long enough that a warmer simply runs slower, short enough that
    # a burst cannot pin every thread in the pool.
    upstream_wait: float = 5.0

    def validate(self) -> Config:
        if self.files_url and not self.files_url.startswith(("http://", "https://")):
            raise ValueError(f"GOTG_FILES_URL is not an http(s) url: {self.files_url!r}")
        if self.files_preferred_url and not self.files_preferred_url.startswith(("http://", "https://")):
            raise ValueError(f"GOTG_FILES_PREFERRED_URL is not an http(s) url: {self.files_preferred_url!r}")
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
        return self
