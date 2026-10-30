# Web UI

A build-less SPA (plain HTML, CSS and ES modules) in `src/gotg/service/static`,
served by the service at `/ui/` before authentication. It ports the picker's
browse (platform, region, search), game detail with art, saves and the admin
screen. All network access is `static/js/api.js`, whose header states the
contract it assumes (cookie session, `/auth/login`, `/auth/whoami`, and the
existing `/catalog`, `/art`, `/saves`, `/admin` routes).

Develop without the real service:

```bash
PYTHONPATH=src python tests/web/mock_server.py 8099   # sign in at /auth/login?as=admin
PYTHONPATH=src python -m pytest tests/web -q
PLAYWRIGHT_BROWSERS_PATH=... PYTHONPATH=src:tests/web python tests/web/e2e_render.py   # needs playwright
```

## Serving

`/ui/` serves the files packaged with the service, or, when `GOTG_WEB_DIR` is
set (the web image sets it), that directory instead: same layout as
`src/gotg/service/static`, nothing mixed in from the packaged copy, and a
directory that does not exist is refused at start. The `gotg-web` Nix package is
the packaged static directory. Bare `/ui` redirects (302) to `/ui/`, keeping the
query, because the page's relative URLs resolve against `/ui/`.

The saves view lists environments from `GET /saves` (`docs/web-api.md`) and
looks a game up by its candidate names (`env-<platform>[-<id>]`) only to offer a
first upload where none exists. Identity is `GET /auth/whoami`, whose `admin`
decides whether the Admin tab shows.
