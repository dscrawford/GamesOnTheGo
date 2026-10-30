"""/auth/login, /callback, /logout and the device flow: sign-in as HTTP.

A session is a `gotg_` token (tokens.py) kept in an httpOnly cookie, so a
browser resolves to the same principal as a bearer and `admin revoke <name>`
ends it. Every route here is answered before bearer authentication; each
checks its own credential (the state cookie, the session cookie, a device code).
"""

from __future__ import annotations

import hmac
import html
import json
import secrets
import sys
import urllib.parse

from .oauth import ProviderError, allowed, pkce_pair, safe_next, user_slug

STATE_COOKIE = "gotg_oauth_state"
MAX_FORM = 4096

ROUTES = {
    "auth/login",
    "auth/callback",
    "auth/logout",
    "auth/device",
    "auth/device/token",
    "auth/device/verify",
    "auth/device/approve",
}


class OAuthRoutes:
    def _oauth_route(self, clean: str) -> None:
        """Answer an /auth route that is not whoami. Closes the connection:
        nothing here leaves a request body unread on a kept-alive socket."""
        self.close_connection = True
        if self.oauth is None or self.token_store is None:
            self._problem(503, "sign-in is not configured on this deployment")
            return
        parts = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(parts.query)
        verbs = {
            "auth/login": ("GET",),
            "auth/callback": ("GET",),
            "auth/logout": ("POST",),
            "auth/device": ("POST", "GET"),
            "auth/device/token": ("POST",),
            "auth/device/verify": ("GET",),
            "auth/device/approve": ("POST",),
        }[clean]
        if self.command not in verbs and not (self.command == "HEAD" and "GET" in verbs):
            self._problem(405, f"{clean} takes {' or '.join(verbs)}", close=True)
            return
        handler = {
            "auth/login": lambda: self._oauth_login(query),
            "auth/callback": lambda: self._oauth_callback(query),
            "auth/logout": self._oauth_logout,
            "auth/device": self._device_start,
            "auth/device/token": self._device_poll,
            "auth/device/verify": lambda: self._device_verify(query),
            "auth/device/approve": self._device_approve,
        }[clean]
        handler()

    # --- cookies & replies ---------------------------------------------------

    def _cookie(self, name: str) -> str:
        for part in self.headers.get("Cookie", "").split(";"):
            key, _, value = part.strip().partition("=")
            if key == name:
                return value
        return ""

    def _set_cookie(self, name: str, value: str, max_age: int) -> str:
        # The state cookie must ride the provider's cross-site redirect back, so it is Lax even
        # when the session cookie is Strict.
        samesite = "Lax" if name == STATE_COOKIE else self.config.cookie_samesite
        secure = "; Secure" if self.config.cookie_secure else ""
        return f"{name}={value}; Path=/; HttpOnly; SameSite={samesite}; Max-Age={max_age}{secure}"

    def _redirect(self, location: str, cookies: list[str] = ()) -> None:  # noqa: B006
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        for cookie in cookies:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()

    def _page(self, code: int, text: str) -> None:
        body = (
            "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
            f"<title>GOTG sign-in</title><body style='font-family:sans-serif;max-width:30em;margin:3em auto'>{text}"
        ).encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _origin(self) -> str:
        if self.config.public_url:
            return self.config.public_url
        return f"http://{self.headers.get('Host', '')}"

    def _csrf_refused(self) -> bool:
        """A cookie-authenticated write from a page on another origin. SameSite
        already keeps the cookie off most of them; this is the second lock."""
        origin = self.headers.get("Origin")
        return origin is not None and origin != self._origin()

    def _read_small(self) -> bytes | None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_FORM:
            self._problem(400, "malformed or oversized body")
            return None
        return self.rfile.read(length) if length else b""

    def _body_fields(self) -> dict[str, str] | None:
        raw = self._read_small()
        if raw is None:
            return None
        if "json" in self.headers.get("Content-Type", ""):
            try:
                parsed = json.loads(raw or b"{}")
            except ValueError:
                parsed = None
            if not isinstance(parsed, dict):
                self._problem(400, "the body is a JSON object")
                return None
            return {k: v for k, v in parsed.items() if isinstance(v, str)}
        return {k: v[0] for k, v in urllib.parse.parse_qs(raw.decode("utf-8", "replace")).items()}

    # --- sessions -----------------------------------------------------------

    def _session_user(self) -> str | None:
        """The person behind the session cookie, or None."""
        name = self._cookie_principal()
        return self.token_store.user_for(name) if name else None

    def _mint(self, user: str, kind: str, ttl: float) -> tuple[str, str]:
        name = f"{user}-{kind}-{secrets.token_hex(3)}"
        return name, self.token_store.issue(name, user, ttl)

    # --- browser flow -------------------------------------------------------

    def _oauth_login(self, query: dict) -> None:
        verifier, challenge = pkce_pair()
        state = secrets.token_urlsafe(24)
        nxt = safe_next((query.get("next") or [""])[0])
        if not self.oauth.states.put(state, {"verifier": verifier, "next": nxt}):
            self._problem(503, "too many sign-ins in flight; retry shortly")
            return
        try:
            url = self.oauth.authorize_url(state, challenge)
        except ProviderError as error:
            self._problem(502, str(error))
            return
        self._redirect(url, [self._set_cookie(STATE_COOKIE, state, 600)])

    def _oauth_callback(self, query: dict) -> None:
        clear = self._set_cookie(STATE_COOKIE, "", 0)
        state = (query.get("state") or [""])[0]
        cookie = self._cookie(STATE_COOKIE)
        # The cookie ties the callback to the browser that began it; without
        # it a victim could be handed an attacker's code.
        if not state or not cookie or not hmac.compare_digest(state.encode(), cookie.encode()):
            self._problem(400, "sign-in state does not match this browser; start again at /auth/login")
            return
        pending = self.oauth.states.pop(state)
        if pending is None:
            self._problem(400, "sign-in expired; start again at /auth/login")
            return
        if "error" in query:
            self._problem(403, f"the provider refused sign-in: {query['error'][0][:80]}")
            return
        code = (query.get("code") or [""])[0]
        if not code:
            self._problem(400, "no authorization code")
            return
        try:
            identity = self.oauth.identify(code, pending["verifier"])
        except ProviderError as error:
            print(f"sign-in failed: {error}", file=sys.stderr)
            self._problem(502, "the identity provider failed; try again")
            return
        if not allowed(identity, self.config.oidc_allowlist):
            print(f"sign-in refused: {identity.login or identity.subject}", file=sys.stderr)
            self._problem(403, "this account is not on the allowlist")
            return
        ttl = self.config.session_ttl
        _, token = self._mint(user_slug(identity), "web", ttl)
        self._redirect(pending["next"], [self._set_cookie(self.config.cookie_name, token, int(ttl)), clear])

    def _oauth_logout(self) -> None:
        if self._csrf_refused():
            self._problem(403, "cross-origin logout")
            return
        name = self._cookie_principal()
        if name:
            self.token_store.revoke(name)
        self._json(200, {"ok": True}, {"Set-Cookie": self._set_cookie(self.config.cookie_name, "", 0)})

    # --- device flow ---------------------------------------------------------

    def _device_start(self) -> None:
        if self.command == "GET":
            self._redirect("/auth/device/verify")
            return
        self._read_small()
        started = self.oauth.start_device()
        if started is None:
            self._problem(503, "too many device sign-ins in flight; retry shortly")
            return
        base = self._origin() + "/auth/device/verify"
        self._json(
            200,
            {
                **started,
                "verification_uri": base,
                "verification_uri_complete": f"{base}?user_code={urllib.parse.quote(started['user_code'])}",
                "expires_in": 600,
                "interval": 5,
            },
        )

    def _device_poll(self) -> None:
        fields = self._body_fields()
        if fields is None:
            return
        status, user = self.oauth.poll_device(fields.get("device_code", ""))
        if status == "approved":
            # Named per device, so revoking the CLI leaves the browser signed in.
            name, token = self._mint(user, "cli", self.config.session_ttl)
            self._json(200, {"name": name, "user": user, "token": token})
            return
        error = {"pending": "authorization_pending", "slow_down": "slow_down", "expired": "expired_token"}[status]
        self._json(400, {"error": error})

    def _device_verify(self, query: dict) -> None:
        code = (query.get("user_code") or [""])[0].strip().upper()
        user = self._session_user()
        if not code:
            self._page(
                200,
                "<h1>Connect a device</h1><form method=get><label>Code shown on the device "
                "<input name=user_code autofocus></label> <button>Continue</button></form>",
            )
            return
        if user is None:
            here = "/auth/device/verify?" + urllib.parse.urlencode({"user_code": code})
            self._redirect("/auth/login?" + urllib.parse.urlencode({"next": here}))
            return
        safe = html.escape(code)
        self._page(
            200,
            f"<h1>Connect a device</h1><p>Signed in as <b>{html.escape(user)}</b>. Allow the device showing "
            f"<b>{safe}</b> to use your library?</p><form method=post action=/auth/device/approve>"
            f"<input type=hidden name=user_code value='{safe}'><button>Allow</button></form>",
        )

    def _device_approve(self) -> None:
        if self._csrf_refused():
            self._problem(403, "cross-origin approval")
            return
        fields = self._body_fields()
        if fields is None:
            return
        user = self._session_user()
        if user is None:
            self._problem(401, "sign in first at /auth/login")
            return
        if not self.oauth.approve_device(fields.get("user_code", ""), user):
            self._problem(404, "no such pending device code")
            return
        if "json" in self.headers.get("Content-Type", ""):
            self._json(200, {"ok": True})
        else:
            self._page(200, "<h1>Done</h1><p>You can return to the device.</p>")
