# The web app: deployment

The web app is the static frontend plus the existing GOTG service. Sign-in is
**optional configuration of the service**: with none of the variables below set
the service behaves exactly as it did before, and `Config.oidc_enabled` is
false. The sign-in flow itself is implemented elsewhere (the oauth module);
this page covers packaging and configuration.

## What the flake provides

| Output | What |
| --- | --- |
| `packages.gotg-web` | the static frontend directory (a placeholder page until the frontend lands) |
| `packages.gotg-service` | the service (same binary as `gotg-proxy`) |
| `packages.web-image` | container image: service + frontend at `/share/gotg-web` (`GOTG_WEB_DIR`) |
| `apps.service` | `nix run .#service`, for local runs |

## 1. Register a GitHub OAuth app

GitHub → Settings → Developer settings → OAuth Apps → New OAuth App.

- Homepage URL: your public URL, e.g. `https://gotg.example.com`
- Authorization callback URL: `https://gotg.example.com/auth/callback`
  (this must equal `GOTG_OIDC_REDIRECT_URL` exactly)

Generate a client secret. The client id is public; the secret goes in a file
or a Kubernetes Secret, never in the Nix store or a manifest. For local
development register a second app with callback
`http://localhost:8080/auth/callback`.

## 2. Configuration

All configuration is environment variables (`src/gotg/service/config.py`,
`cli.py`). Set none for no web sign-in; set any and the service refuses to
start unless the required ones are all present.

| Variable | Required with sign-in | Meaning |
| --- | --- | --- |
| `GOTG_OIDC_PROVIDER` | yes | `github` or `oidc` |
| `GOTG_OIDC_ISSUER_URL` | for `oidc` | issuer for a generic OIDC provider |
| `GOTG_OIDC_CLIENT_ID` | yes | OAuth client id |
| `GOTG_OIDC_CLIENT_SECRET_FILE` / `GOTG_OIDC_CLIENT_SECRET` | yes | secret; the `_FILE` form wins |
| `GOTG_OIDC_REDIRECT_URL` | yes | the registered callback url |
| `GOTG_OIDC_ALLOWLIST` | yes | comma/newline separated logins (GitHub usernames; emails for `oidc`), case-insensitive. Empty is refused, never "everyone" |
| `GOTG_SESSION_SECRET_FILE` / `GOTG_SESSION_SECRET` | yes | signs the session cookie; ≥32 chars, distinct from every token |
| `GOTG_OIDC_AUTHORIZE_URL`, `_TOKEN_URL`, `_USERINFO_URL`, `GOTG_OIDC_SCOPES` | no | explicit provider endpoints / scopes (`docs/web-auth.md`) |
| `GOTG_COOKIE_NAME` | no | default `gotg_session` |
| `GOTG_COOKIE_SECURE` | no | default `1`; `0` only for http localhost |
| `GOTG_COOKIE_SAMESITE` | no | `Lax` (default), `Strict`, `None` (needs Secure) |
| `GOTG_SESSION_TTL` | no | seconds, default 604800 |

The sign-in code (`docs/web-auth.md`) reads exactly these names from `Config`. The older
`GOTG_OAUTH_*` spellings (`_PROVIDER`, `_CLIENT_ID`, `_CLIENT_SECRET`, `_ISSUER`, `_ALLOW`,
`_REDIRECT_URI`, `_SESSION_DAYS`, `_AUTHORIZE_URL`, `_TOKEN_URL`, `_USERINFO_URL`, `_SCOPES`)
still work as aliases; the `GOTG_OIDC_*` one wins when both are set.

Unreadable secret files, non-boolean flags, non-http(s) urls, an `http`
redirect with Secure cookies, and a session secret equal to another credential
are all startup errors rather than silent fallbacks.

Generate a session secret: `openssl rand -hex 32 > session-secret`.

## 3. Running locally

```bash
openssl rand -hex 32 > /tmp/session-secret
printf '%s' "$GITHUB_CLIENT_SECRET" > /tmp/oidc-secret   # chmod 600
GOTG_PROXY_TOKEN=dev-token \
GOTG_OIDC_PROVIDER=github \
GOTG_OIDC_CLIENT_ID=Iv1.xxxx \
GOTG_OIDC_CLIENT_SECRET_FILE=/tmp/oidc-secret \
GOTG_SESSION_SECRET_FILE=/tmp/session-secret \
GOTG_OIDC_REDIRECT_URL=http://localhost:8080/auth/callback \
GOTG_OIDC_ALLOWLIST=your-github-login \
GOTG_COOKIE_SECURE=0 \
nix run .#service
```

The service listens on `$PORT` (default 8080).

## 4. Kubernetes

`k8s/web/web.yaml`: Deployment, Service and an Ingress with TLS. Secrets are
mounted as files from the `gotg-web` Secret (creation command at the top of
the manifest). Build and push the image with `nix build .#web-image` then
`skopeo copy`.

## 5. Reverse-proxy notes

- **TLS must terminate in front, and the browser must see https.** Cookies are
  `Secure` by default; behind plain http nobody can sign in.
- `GOTG_OIDC_REDIRECT_URL` and `GOTG_PUBLIC_URL` use the public host, not the
  pod's. Forward `Host` and `X-Forwarded-Proto` unchanged.
- Keep `/admin` off the public ingress; use the tailnet listener
  (`GOTG_ADMIN_PORT`, `GOTG_ADMIN_URL`).
- Raise the body-size limit (nginx `proxy-body-size`) for save and log uploads,
  and don't buffer file streams if `GOTG_FILES_URL` routes bytes elsewhere.
- Run one replica unless the token/saves stores are on shared storage.
- Rotating `GOTG_SESSION_SECRET` signs everyone out; changing the allowlist
  takes effect on restart.
