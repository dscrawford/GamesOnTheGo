"""Claiming, whoami and `/admin`: everything about per-person tokens.

The handler's credential half, in its own file because it is the one part of
the service that is reachable before authentication (`_claim`, whose code *is*
the credential), the one that decides who somebody is (`_auth`), and the one
only the admin token opens (`_admin`). Kept apart from the proxy and the
stores so that "what can an unauthenticated or admin caller do" is one file to
read. `_admin` had seventy lines of five routes in one body; each is now a
method, and `parse_invite` is the body's validation as a pure function.
"""

from __future__ import annotations

import hmac
import json
import sqlite3
import sys
import urllib.parse

from ..tokens import Absent, Claimed
from . import admin_page

# The one body read before anybody is authenticated: {"code": …} around a
# 49-character code.
CLAIM_MAX_BODY = 256

MAX_INVITE_DAYS = 3650


def parse_invite(body: bytes | None) -> tuple[str, str | None, float]:
    """`(name, user, ttl_days)` from a mint request, or a ValueError whose
    message is the 400."""
    try:
        asked = json.loads(body or b"{}")
        name = asked.get("name", "")
        user = asked.get("user") or None
        ttl_days = float(asked.get("ttl_days", 7))
    except (ValueError, AttributeError, TypeError):
        raise ValueError('the body is {"name": ..., "user"?: ..., "ttl_days"?: ...}') from None
    # False for NaN, refuses Infinity: json accepts both, and either
    # overflows int() further down as an uncaught OverflowError.
    if not 0 < ttl_days <= MAX_INVITE_DAYS:
        raise ValueError("ttl_days must be between 0 and 3650")
    return name, user, ttl_days


class AdminRoutes:
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
        self._json(200, {"name": name, "token": token})

    def _auth(self, rest: str, principal: str) -> None:
        if rest.split("?")[0] != "whoami":
            self._problem(404, "nothing lives at /auth but whoami")
            return
        if self.command not in ("GET", "HEAD"):
            self._problem(405, "whoami is a GET")
            return
        reply: dict = {"name": principal}
        if self.token_store is not None:
            user = self.token_store.user_for(principal)
            if user:
                reply["user"] = user
        self._json(200, reply)

    def _needs_admin(self, principal: str) -> bool:
        if not self.config.admin_token:
            self._problem(503, "no admin token is configured; administration is off")
            return True
        # The bearer, not the name — what _is_index does, and for the same
        # reason. A principal is "admin" either because the token matched here
        # or because {auth_url}/auth/whoami said so, and that reply is parsed
        # unvalidated. Nothing can currently make a peer say "admin", but the
        # gate on the credential store should not rest on that staying true.
        if not hmac.compare_digest(self._bearer(), self.config.admin_token.encode()):
            self._problem(403, "administration needs the admin token")
            return True
        return False

    def _admin_scan(self, query: str) -> None:
        """`GET /admin/scan` — what the library gained, and what has gone from it.

        The missing half stats every member file, which is most of why this
        sits behind the admin token: it is not a thing a client may ask for.
        The added half is only as current as the indexer's last pass, since
        nothing else turns a payload into a titled entry.
        """
        if self.catalog is None:
            self._problem(503, "this service holds no catalog")
            return
        if self.command not in ("GET", "HEAD"):
            self._problem(405, "a scan is a GET")
            return
        since = urllib.parse.parse_qs(query).get("since", [""])[0] or None
        # One sweep at a time. It is thousands of blocking stats against a
        # network mount, and a person who thinks it has hung will retry —
        # which buys nothing and parks a second thread that a hung mount will
        # not give back. The same discipline as the streaming semaphore.
        if not self.scans.acquire(blocking=False):
            self._problem(503, "a scan is already running; try again in a moment")
            return
        try:
            report = self.catalog.scan(since=since)
        except ValueError as error:
            self._problem(400, str(error))
            return
        # As in _catalog: the message itself never reaches the client, because
        # a storage error string typically embeds server paths.
        except sqlite3.Error as error:
            print(f"catalog scan storage error: {error}", file=sys.stderr)
            self._problem(500, "catalog storage error")
            return
        except OSError as error:
            print(f"catalog scan error: {error}", file=sys.stderr)
            self._problem(500, "catalog storage error")
            return
        finally:
            self.scans.release()
        self._json(200, report)

    def _admin(self, rest: str, body: bytes | None, principal: str) -> None:
        if self._needs_admin(principal):
            return
        # partition, not urlsplit: on a doubled slash urlsplit reads the next
        # segment as a netloc and drops it, so /admin//anything/tokens would
        # reach the token routes that /admin/<one segment>/tokens must not.
        # It also strips a #fragment, and raises on an unbalanced [ .
        path, _, query = rest.partition("?")
        segments = path.strip("/").split("/")

        # The library half of /admin, and the only part that needs no token
        # store: a deployment can hold a catalog without holding one.
        if segments == ["scan"]:
            self._admin_scan(query)
            return

        if self.token_store is None:
            self._problem(503, "this deployment holds no token store")
            return

        if segments == ["info"]:
            self._json(200, {"public_url": self.config.public_url})
        elif segments == ["invites"] and self.command in ("GET", "HEAD"):
            self._json(200, {"invites": self.token_store.invites()})
        elif segments == ["invites"]:
            self._admin_mint(body)
        elif segments == ["tokens"]:
            self._admin_tokens()
        elif len(segments) == 2 and segments[0] == "tokens":
            self._admin_revoke(segments[1])
        else:
            self._problem(404, "nothing lives at /admin but info, invites, tokens and scan")

    def _admin_mint(self, body: bytes | None) -> None:
        if self.command != "POST":
            self._problem(405, "minting an invite is a POST")
            return
        try:
            name, user, ttl_days = parse_invite(body)
        except ValueError as error:
            self._problem(400, str(error))
            return
        try:
            code = self.token_store.mint_invite(name, ttl=ttl_days * 86400, user=user)
        except (ValueError, TypeError, OverflowError) as error:
            self._problem(400, str(error))
            return
        self._json(200, {"name": name, "code": code})

    def _admin_tokens(self) -> None:
        if self.command not in ("GET", "HEAD"):
            self._problem(405, "the token list is a GET")
            return
        self._json(200, {"tokens": self.token_store.tokens()})

    def _admin_revoke(self, name: str) -> None:
        if self.command != "DELETE":
            self._problem(405, "revoking is a DELETE")
            return
        if self.token_store.revoke(name):
            self._json(200, {"revoked": name})
        else:
            self._problem(404, f"nothing live to revoke for {name!r}")

    def _send_page(self, body: bytes, content_type: str) -> None:
        """The admin page, as strict as a page can be: its own script and
        style only, nothing framed, nothing cached, no referrer -- it is
        typed an admin token into."""
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", admin_page.POLICY)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
