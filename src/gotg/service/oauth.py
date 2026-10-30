"""OAuth 2.0 / OIDC sign-in: the provider conversation, PKCE, and the pending state.

GitHub by default, any OIDC provider by configuration. The identity is whatever
the provider's userinfo endpoint says over TLS for the access token we just
exchanged -- no JWT is parsed or verified, so there are no keys to fetch. The
result is a person on the allowlist or nobody; routes_oauth.py turns that into
a `gotg_` token through tokens.py.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .._http import NO_REDIRECT_OPENER
from ..tokens import NAME_RE, RESERVED_NAMES
from .config import TIMEOUT, USER_AGENT, Config

GITHUB_AUTHORIZE = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN = "https://github.com/login/oauth/access_token"
GITHUB_USERINFO = "https://api.github.com/user"
GITHUB_SCOPES = "read:user user:email"
OIDC_SCOPES = "openid profile email"

STATE_TTL = 600
DEVICE_TTL = 600
DEVICE_INTERVAL = 5
# Unauthenticated callers fill these; a bound beats reasoning about growth.
MAX_PENDING = 2000
# No 0/O/1/I/U-style lookalikes: a person types this from a TV.
USER_CODE_ALPHABET = "BCDFGHJKLMNPQRSTVWXZ"


class ProviderError(Exception):
    """The provider could not be reached or answered nonsense: a 502, not a 403."""


@dataclass(frozen=True)
class Endpoints:
    authorize: str
    token: str
    userinfo: str
    scopes: str


@dataclass(frozen=True)
class Identity:
    login: str  # the provider's handle, "" when it has none
    email: str  # verified addresses only
    subject: str

    def candidates(self) -> set[str]:
        return {c.lower() for c in (self.login, self.email) if c}


def pkce_pair() -> tuple[str, str]:
    """(verifier, S256 challenge)."""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def safe_next(value: str | None) -> str:
    """A same-site path or "/": never a URL, so login cannot be an open redirect."""
    if not value or not value.startswith("/") or value.startswith(("//", "/\\")):
        return "/"
    if any(ord(c) < 32 or c == "\x7f" for c in value):
        return "/"
    return value


def user_slug(identity: Identity) -> str:
    """The token-store user for a person: NAME_RE-shaped, at most 20 characters.

    A slug that had to be altered (or is reserved) gets a hash suffix, so two
    different people cannot land in one person's saves by sanitizing alike.
    """
    raw = (identity.login or identity.email.split("@")[0] or identity.subject).lower()
    slug = re.sub(r"[^a-z0-9_-]+", "-", raw).strip("-_")[:20].strip("-_")
    if slug == raw and NAME_RE.match(slug) and slug not in RESERVED_NAMES:
        return slug
    suffix = hashlib.sha256((identity.login or identity.email or identity.subject).lower().encode()).hexdigest()[:5]
    return f"{slug[:14] or 'u'}-{suffix}"


def allowed(identity: Identity, allowlist: tuple[str, ...]) -> bool:
    """Exact username or verified email, or "@domain" for a verified email's domain.
    An empty list admits nobody."""
    have = identity.candidates()
    email = identity.email.lower()
    for entry in allowlist:
        entry = entry.strip().lower()
        if entry and (entry in have or (entry.startswith("@") and email.endswith(entry))):
            return True
    return False


class _Pending:
    """A bounded, expiring map of single-use entries."""

    def __init__(self, ttl: float):
        self.ttl = ttl
        self._items: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()

    def put(self, key: str, value: dict) -> bool:
        now = time.monotonic()
        with self._lock:
            self._sweep(now)
            if len(self._items) >= MAX_PENDING:
                return False
            self._items[key] = (now + self.ttl, value)
            return True

    def pop(self, key: str) -> dict | None:
        with self._lock:
            hit = self._items.pop(key, None)
        return hit[1] if hit and hit[0] > time.monotonic() else None

    def get(self, key: str) -> dict | None:
        """The live entry itself, mutable under the caller's care."""
        with self._lock:
            hit = self._items.get(key)
        return hit[1] if hit and hit[0] > time.monotonic() else None

    def find(self, predicate) -> tuple[str, dict] | None:
        now = time.monotonic()
        with self._lock:
            for key, (expires, value) in self._items.items():
                if expires > now and predicate(value):
                    return key, value
        return None

    def _sweep(self, now: float) -> None:
        for key in [k for k, (expires, _) in self._items.items() if expires <= now]:
            del self._items[key]


class OAuth:
    def __init__(self, config: Config):
        self.config = config
        self.states = _Pending(STATE_TTL)
        self.devices = _Pending(DEVICE_TTL)
        self._endpoints: Endpoints | None = None
        self._lock = threading.Lock()

    # --- configuration ------------------------------------------------------

    def redirect_uri(self) -> str:
        return self.config.oidc_redirect_url or f"{self.config.public_url}/auth/callback"

    def endpoints(self) -> Endpoints:
        with self._lock:
            if self._endpoints is not None:
                return self._endpoints
        config = self.config
        if config.oidc_provider == "github":
            found = Endpoints(
                config.oidc_authorize_url or GITHUB_AUTHORIZE,
                config.oidc_token_url or GITHUB_TOKEN,
                config.oidc_userinfo_url or GITHUB_USERINFO,
                config.oidc_scopes or GITHUB_SCOPES,
            )
        else:
            authorize, token, userinfo = config.oidc_authorize_url, config.oidc_token_url, config.oidc_userinfo_url
            if config.oidc_issuer_url and not (authorize and token and userinfo):
                doc = self._json(f"{config.oidc_issuer_url}/.well-known/openid-configuration")
                authorize = authorize or doc.get("authorization_endpoint", "")
                token = token or doc.get("token_endpoint", "")
                userinfo = userinfo or doc.get("userinfo_endpoint", "")
            if not (authorize and token and userinfo):
                raise ProviderError("the OIDC provider's endpoints are unknown")
            found = Endpoints(authorize, token, userinfo, config.oidc_scopes or OIDC_SCOPES)
        with self._lock:
            self._endpoints = found
        return found

    # --- the conversation ---------------------------------------------------

    def authorize_url(self, state: str, challenge: str) -> str:
        endpoints = self.endpoints()
        query = urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id": self.config.oidc_client_id,
                "redirect_uri": self.redirect_uri(),
                "scope": endpoints.scopes,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        sep = "&" if "?" in endpoints.authorize else "?"
        return f"{endpoints.authorize}{sep}{query}"

    def identify(self, code: str, verifier: str) -> Identity:
        """Code -> access token -> userinfo. The token is used once and dropped."""
        endpoints = self.endpoints()
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri(),
            "client_id": self.config.oidc_client_id,
            "code_verifier": verifier,
        }
        if self.config.oidc_client_secret:
            form["client_secret"] = self.config.oidc_client_secret
        answer = self._json(endpoints.token, data=urllib.parse.urlencode(form).encode())
        access = answer.get("access_token")
        if not isinstance(access, str) or not access:
            raise ProviderError(f"the provider gave no access token ({answer.get('error', 'unknown')})")
        auth = {"Authorization": f"Bearer {access}"}
        info = self._json(endpoints.userinfo, headers=auth)
        if self.config.oidc_provider == "github":
            return self._github_identity(info, endpoints.userinfo, auth)
        email = info.get("email") if info.get("email_verified") is True else ""
        login = info.get("preferred_username") or ""
        return Identity(
            login=login if isinstance(login, str) else "",
            email=email if isinstance(email, str) else "",
            subject=str(info.get("sub", "")),
        )

    def _github_identity(self, info: dict, userinfo_url: str, auth: dict) -> Identity:
        login = info.get("login")
        if not isinstance(login, str) or not login:
            raise ProviderError("the provider named no user")
        email = ""
        try:
            emails = self._json(f"{userinfo_url}/emails", headers=auth, expect=list)
        except ProviderError:
            emails = []
        for row in emails:
            if isinstance(row, dict) and row.get("primary") and row.get("verified") and row.get("email"):
                email = str(row["email"])
        return Identity(login=login, email=email, subject=str(info.get("id", "")))

    def _json(self, url: str, data: bytes | None = None, headers: dict | None = None, expect: type = dict):
        request = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET")
        request.add_header("User-Agent", USER_AGENT)
        request.add_header("Accept", "application/json")
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        try:
            with NO_REDIRECT_OPENER.open(request, timeout=TIMEOUT) as response:  # noqa: S310
                parsed = json.loads(response.read(1 << 20))
        except urllib.error.HTTPError as error:
            error.close()
            raise ProviderError(f"{url} answered {error.code}") from None
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise ProviderError(f"{url}: {error}") from None
        if not isinstance(parsed, expect):
            raise ProviderError(f"{url} answered something unexpected")
        return parsed

    # --- device codes -------------------------------------------------------

    def start_device(self) -> dict | None:
        device_code = secrets.token_urlsafe(32)
        raw = "".join(secrets.choice(USER_CODE_ALPHABET) for _ in range(8))
        user_code = f"{raw[:4]}-{raw[4:]}"
        entry = {"user_code": user_code, "user": None, "last_poll": 0.0, "denied": False}
        if not self.devices.put(device_code, entry):
            return None
        return {"device_code": device_code, "user_code": user_code}

    def approve_device(self, user_code: str, user: str) -> bool:
        wanted = user_code.strip().upper()
        found = self.devices.find(lambda e: e["user_code"] == wanted and e["user"] is None)
        if found is None:
            return False
        found[1]["user"] = user
        return True

    def poll_device(self, device_code: str) -> tuple[str, str | None]:
        """("pending"|"slow_down"|"expired"|"approved", user)."""
        entry = self.devices.get(device_code)
        if entry is None:
            return "expired", None
        if entry["user"] is not None:
            # Whoever pops it first gets the token; a racing poll finds it gone.
            taken = self.devices.pop(device_code)
            return ("approved", taken["user"]) if taken else ("expired", None)
        now = time.monotonic()
        early = now - entry["last_poll"] < DEVICE_INTERVAL
        entry["last_poll"] = now
        return ("slow_down" if early else "pending"), None
