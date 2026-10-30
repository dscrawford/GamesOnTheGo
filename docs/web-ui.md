# Web UI

A build-less SPA (plain HTML, CSS and ES modules) in `src/gotg/service/static`,
served by the service at `/ui/` before authentication. It ports the picker's
browse (platform, region, search), game detail with art, saves and the admin
screen. All network access is `static/js/api.js`, whose header states the
contract it assumes (cookie session, `/auth/login`, `/auth/me`, and the
existing `/catalog`, `/art`, `/saves`, `/admin` routes).

A game's page asks `/play/plan/<platform>/<id>` how the game plays:
- a browser system gets "Play in browser", which opens `/play/` signed in with
  the same cookie (`docs/browser-play.md`);
- anything else shows its `nix run gotg#…` command;
- a service without the player (404) shows neither.

Develop without the real service:

```bash
PYTHONPATH=src python tests/web/mock_server.py 8099   # sign in at /auth/login?as=admin
PYTHONPATH=src python -m pytest tests/web -q
PLAYWRIGHT_BROWSERS_PATH=... PYTHONPATH=src:tests/web python tests/web/e2e_render.py   # needs playwright
```
