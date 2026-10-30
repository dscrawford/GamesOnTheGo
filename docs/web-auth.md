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

| Variable | Meaning |
| --- | --- |
| `GOTG_OAUTH_CLIENT_ID` | Turns sign-in on. Unset: `/auth/*` sign-in routes answer 503. |
| `GOTG_OAUTH_CLIENT_SECRET` | Optional for public clients. |
| `GOTG_OAUTH_PROVIDER` | `github` (default) or `oidc`. |
| `GOTG_OAUTH_ISSUER` | oidc: endpoints found at `<issuer>/.well-known/openid-configuration`. |
| `GOTG_OAUTH_AUTHORIZE_URL`, `_TOKEN_URL`, `_USERINFO_URL` | Explicit endpoints (override discovery / GitHub defaults). |
| `GOTG_OAUTH_SCOPES` | Default `read:user user:email` (github), `openid profile email` (oidc). |
| `GOTG_OAUTH_ALLOW` | Comma list: usernames, verified emails, or `@domain`. **Empty admits nobody.** |
| `GOTG_OAUTH_REDIRECT_URI` | Default `$GOTG_PUBLIC_URL/auth/callback`; register this at the provider. |
| `GOTG_OAUTH_SESSION_DAYS` | Session / device token lifetime, default 30. |
| `GOTG_PUBLIC_URL` | Required; an `https://` url also sets `Secure` on cookies. |

Only a *verified* email counts (GitHub: primary and verified; oidc:
`email_verified: true`). The OIDC username is `preferred_username`.
Providers are called with urllib only, redirects not followed.

## Endpoints (public listener)

- `GET /auth/login[?next=/path]` -> 302 to the provider; sets `gotg_oauth_state`.
  `next` must be a same-site path, else `/`.
- `GET /auth/callback` -> on success 302 to `next`, setting `gotg_session`
  (`HttpOnly; SameSite=Lax; Path=/; Secure` on https; Max-Age = session days).
  Errors are `{"error": ...}`: 400 bad/expired state, 403 refused or not on the
  allowlist, 502 provider failure.
- `POST /auth/logout` -> `{"ok": true}`, revokes the session token, clears the cookie.
- `GET /auth/whoami` (cookie or bearer) -> `{"name": "alice-web-1a2b3c", "user": "alice"}`.
- `POST /auth/bearer` (cookie session only, `Origin` must be this site) ->
  `{"token": "gotg_…", "name": "alice-play-1a2b3c", "expires_in": 21600}`.
  The token is for byte hosts on another origin, which never see the cookie.
  The browser player asks for one and keeps it in memory. It is an ordinary
  personal token: listed under `/admin`, revocable, and expiring after 6 h.
  A request that already carries a bearer gets 400.
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
