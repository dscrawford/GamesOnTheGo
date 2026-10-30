# Web sign-in (OAuth / OIDC)

Browsers and CLIs sign in through an identity provider; the service turns that
into an ordinary per-user `gotg_` token (`src/gotg/tokens.py`). Nothing new is
stored: `gotg admin tokens` lists these, `gotg admin revoke <name>` ends them,
and saves land under the same user as the person's invited devices.

Code: `service/oauth.py` (provider conversation, PKCE, allowlist),
`service/routes_oauth.py` (routes), `_principal()` in `service/app.py`.
Authorization code + PKCE (S256) + `state` (bound to the browser by a cookie).
Identity comes from the provider's **userinfo** endpoint; no JWT is verified.

## Configuration (environment)

One set of names, shared with the deployment (`docs/web-app.md` has the full
table and the Kubernetes wiring). Sign-in is on when any `GOTG_OIDC_*` is set;
`validate` then demands the rest. The earlier `GOTG_OAUTH_*` spellings are still
accepted as aliases; the `GOTG_OIDC_*` name wins when both are set.

| Variable (alias) | Meaning |
| --- | --- |
| `GOTG_OIDC_PROVIDER` (`GOTG_OAUTH_PROVIDER`) | `github` or `oidc`. Via the alias, a client id alone implies `github`. |
| `GOTG_OIDC_CLIENT_ID` (`GOTG_OAUTH_CLIENT_ID`) | The OAuth client id. |
| `GOTG_OIDC_CLIENT_SECRET[_FILE]` (`GOTG_OAUTH_CLIENT_SECRET`) | The `_FILE` form wins. |
| `GOTG_OIDC_ISSUER_URL` (`GOTG_OAUTH_ISSUER`) | oidc: endpoints found at `<issuer>/.well-known/openid-configuration`. |
| `GOTG_OIDC_AUTHORIZE_URL`, `_TOKEN_URL`, `_USERINFO_URL` (`GOTG_OAUTH_*`) | Explicit endpoints (override discovery / GitHub defaults); all three replace the issuer. |
| `GOTG_OIDC_SCOPES` (`GOTG_OAUTH_SCOPES`) | Default `read:user user:email` (github), `openid profile email` (oidc). |
| `GOTG_OIDC_ALLOWLIST` (`GOTG_OAUTH_ALLOW`) | Comma/newline list: usernames, verified emails, or `@domain`, case-insensitive. **Empty is refused.** |
| `GOTG_OIDC_REDIRECT_URL` (`GOTG_OAUTH_REDIRECT_URI`) | Register this at the provider. Through the aliases it defaults to `$GOTG_PUBLIC_URL/auth/callback`. |
| `GOTG_SESSION_TTL` (`GOTG_OAUTH_SESSION_DAYS`, days) | Session / device token lifetime in seconds, default 604800. |
| `GOTG_COOKIE_NAME`, `GOTG_COOKIE_SECURE`, `GOTG_COOKIE_SAMESITE` | The session cookie's name and flags (defaults `gotg_session`, Secure, Lax). The short-lived state cookie is always Lax. |
| `GOTG_SESSION_SECRET[_FILE]` | Required by the deployment's validation (>= 32 chars, distinct from every token). The cookie holds a `gotg_` token, which the token store checks, so nothing is signed with it yet. |
| `GOTG_PUBLIC_URL` | Origin the CSRF check compares `Origin` against. |

Only a *verified* email counts (GitHub: primary and verified; oidc:
`email_verified: true`). The OIDC username is `preferred_username`.
Providers are called with urllib only, redirects not followed.

## Endpoints (public listener)

- `GET /auth/login[?next=/path]` -> 302 to the provider; sets `gotg_oauth_state`.
  `next` must be a same-site path, else `/`.
- `GET /auth/callback` -> on success 302 to `next`, setting `gotg_session`
  (`HttpOnly; Path=/`, plus `Secure` and `SameSite` per config; Max-Age = session TTL).
  Errors are `{"error": ...}`: 400 bad/expired state, 403 refused or not on the
  allowlist, 502 provider failure.
- `POST /auth/logout` -> `{"ok": true}`, revokes the session token, clears the cookie.
- `GET /auth/whoami` (cookie or bearer) -> `{"name": "alice-web-1a2b3c", "user": "alice", "admin": false}`.
  `admin` is true only for a cookie session whose user is in `GOTG_ADMIN_USERS`.
- Cookie-authenticated writes whose `Origin` is not `GOTG_PUBLIC_URL` get 403.

### Device flow (CLI / Deck)

1. `POST /auth/device` -> `{"device_code", "user_code": "BCDF-GHJK", "verification_uri", "verification_uri_complete", "expires_in": 600, "interval": 5}`
2. The person opens `verification_uri_complete` in a browser, signs in if needed, presses Allow
   (`POST /auth/device/approve`, session cookie, `user_code` as form or JSON).
3. The device polls `POST /auth/device/token` with `{"device_code": ...}`:
   200 `{"name": "alice-cli-1a2b3c", "user": "alice", "token": "gotg_..."}` once (single use), or
   400 `{"error": "authorization_pending" | "slow_down" | "expired_token"}`.

Tokens are named `<user>-web-<hex>` / `<user>-cli-<hex>`, one per login, so
signing in elsewhere never logs anyone out. The user is the login lowercased
(sanitized, with a hash suffix when it had to change).

## Admin by session

`GOTG_ADMIN_USERS` (comma list of token-store users, i.e. the slugs `whoami`
shows as `user`) lets a **signed-in cookie session** of one of those users call
`/admin/*`, beside the admin bearer token. Needs sign-in on, else the service
refuses to start. Only the cookie counts: any bearer other than the admin token,
including the same person's CLI token, is not admin. A write (`POST`/`DELETE`)
from a session must carry an `Origin` equal to `GOTG_PUBLIC_URL` (a missing
Origin is refused), on top of `SameSite`. Revoking the session token ends it.

`/admin` is still 404 on a `public` listener and `/auth` is still 404 on the
`admin` one (a split deployment keeps admin on the tailnet, with the token), so
session admin applies to a deployment whose single listener serves both. Setting
it is the decision to expose `/admin` to allowlisted sign-ins there.
