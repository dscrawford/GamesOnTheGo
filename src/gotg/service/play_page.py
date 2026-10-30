"""The browser player: which systems run in a tab, and the page that runs them.

`/play/?platform=<slug>&id=<id>` is a static page (like the admin page: it holds
no secret, the token is typed into it and kept for the tab) that fetches the ROM
from `/games`, starts EmulatorJS on it and keeps the game's save in step with the
saves store. `/play/plan/<platform>/<id>` is the one authenticated route: it says
whether this platform runs in the browser at all, with which EmulatorJS system, and
-- for everything else -- the `nix run` command that plays it properly.

The mapping is the point of this module and is plain data (`CORES`), so a test can
hold it against `config/` and the indexer's platform list. EmulatorJS itself is not
in the repository: see docs/browser-play.md and nix/emulatorjs.nix.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path

from ..contract import ENTRY_ID_RE, PLATFORM_RE

# EmulatorJS needs wasm, blob workers and the styles its loader injects. Nothing
# third-party: every asset comes from /play/ejs on this origin. 'unsafe-eval' is
# required: EmulatorJS ships every core as a 7z, and its extractor
# (data/compression/extract7z.js, old Emscripten) calls eval() while it starts.
# It runs in a blob worker, which inherits this policy, so without 'unsafe-eval'
# no core decompresses and the player hangs at "Decompress Game Core". This
# applies to /play only; the SPA and the admin page keep their stricter policy.
POLICY = (
    "default-src 'none'; script-src 'self' 'unsafe-eval' 'wasm-unsafe-eval' blob:; worker-src 'self' blob:; "
    "style-src 'self' 'unsafe-inline'; connect-src 'self' blob: data:; img-src 'self' blob: data:; "
    "media-src 'self' blob:; font-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


def policy(byte_hosts: list[str] | tuple[str, ...] = ()) -> str:
    """POLICY, plus the byte hosts the catalog advertises in connect-src.

    Where the service holds no game files itself (a split deployment), the ROM
    comes from the catalog's files_urls, cross-origin. Only scheme://host[:port]
    of an http(s) URL is added; anything else is dropped, never pasted in.
    """
    origins = []
    for host in byte_hosts:
        parts = urllib.parse.urlsplit(host)
        if parts.scheme in ("http", "https") and parts.hostname and _ORIGIN_SAFE.fullmatch(parts.netloc):
            origins.append(f"{parts.scheme}://{parts.netloc}")
    if not origins:
        return POLICY
    return POLICY.replace(
        "connect-src 'self' blob: data:;", "connect-src 'self' " + " ".join(dict.fromkeys(origins)) + " blob: data:;"
    )


_ORIGIN_SAFE = re.compile(r"[A-Za-z0-9.\-\[\]:]+")

# The bundle a browser pushes is a tiny JSON container, not the tar.zst a native
# client pushes, so the two can never be mistaken for one another: its attr has
# its own prefix. The store never opens a bundle, only the machine whose disk it
# lands on does.
SAVE_ATTR_PREFIX = "env-web-"


class Core:
    """One browser-playable system.

    `system` is what EmulatorJS is told (EJS_core); `core` is the libretro core
    it resolves to, for the licence table; `exts` are the ROM extensions to pick
    among a catalog entry's member files (archives are handed to EmulatorJS as is).
    """

    def __init__(self, system: str, core: str, exts: tuple[str, ...], note: str = "", experimental: bool = False):
        self.system, self.core, self.exts, self.note, self.experimental = system, core, exts, note, experimental

    def view(self) -> dict:
        return {
            "system": self.system,
            "core": self.core,
            "exts": list(self.exts),
            "note": self.note,
            "experimental": self.experimental,
        }


ARCHIVES = (".zip", ".7z")

# The one list of browser-playable systems, shared with Nix: nix/emulatorjs.nix
# packages exactly the cores named here, so the two cannot drift. Only what
# runs in WASM at a useful speed is under "browser" (n64 and ps1 flagged: they
# run on good hardware and are the first thing to stutter on a phone).
# "unsupported" are the systems that stay with the Nix launcher for good.
SYSTEMS_FILE = Path(__file__).with_name("play_systems.json")
_SYSTEMS = json.loads(SYSTEMS_FILE.read_text())

CORES: dict[str, Core] = {
    slug: Core(
        entry["system"],
        entry["core"],
        tuple(entry["exts"]) + ARCHIVES,
        entry.get("note", ""),
        entry.get("experimental", False),
    )
    for slug, entry in _SYSTEMS["browser"].items()
}
UNSUPPORTED: dict[str, str] = dict(_SYSTEMS["unsupported"])


def save_attr(platform: str, game_id: str) -> str:
    """The saves-store attr for a game played in the browser. Per game, because
    EmulatorJS keeps one battery file per ROM, not per emulator; it matches the
    store's ATTR_RE by construction (platform and id are validated shapes)."""
    return f"{SAVE_ATTR_PREFIX}{platform}-{game_id.replace('.', '_')}"


def nix_command(platform: str, game_id: str) -> str:
    return f"nix run gotg#{platform}.{game_id}"


def play_plan(platform: str, game_id: str) -> dict | None:
    """How to play a game, or None when the names are not shaped like a catalog entry."""
    if not (PLATFORM_RE.match(platform) and ENTRY_ID_RE.match(game_id)):
        return None
    plan = {"platform": platform, "id": game_id, "nix": nix_command(platform, game_id)}
    core = CORES.get(platform)
    if core is None:
        reason = UNSUPPORTED.get(platform, "This system is not set up for browser play.")
        return {**plan, "mode": "nix", "unsupported": platform in UNSUPPORTED, "reason": reason}
    return {**plan, "mode": "browser", "save_attr": save_attr(platform, game_id), **core.view()}


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>GOTG play</title>
<link rel="stylesheet" href="app.css">
<script src="app.js" defer></script>
</head>
<body>
<header><h1>GOTG <span class="dim">play</span></h1><span id="status" class="dim"></span></header>
<main>
  <section id="login" hidden>
    <h2>Sign in</h2>
    <p><a id="sso" href="../auth/login">Sign in with your account</a></p>
    <p class="dim">Or paste a GOTG token. It is kept for this tab only.</p>
    <form id="login-form">
      <input id="token" type="password" autocomplete="off" spellcheck="false" placeholder="token" required>
      <button type="submit">Sign in</button>
    </form>
  </section>
  <section id="nix" hidden>
    <h2 id="nix-title"></h2>
    <p id="nix-reason">This system does not run in a browser.</p>
    <p>Play it with the Nix launcher:</p>
    <pre id="nix-command"></pre>
  </section>
  <section id="conflict" hidden>
    <p class="error">The server has a newer save than the one this tab started from.</p>
    <button id="keep-server">Use the server's save (reload)</button>
    <button id="keep-mine">Overwrite with this tab's save</button>
  </section>
  <p id="error" class="error" hidden></p>
  <div id="game"></div>
</main>
</body>
</html>
"""

STYLE = """
body { margin: 0; font: 15px system-ui, sans-serif; background: #111; color: #eee; }
header { display: flex; gap: 1rem; align-items: baseline; padding: .5rem 1rem; }
h1 { font-size: 1.1rem; margin: 0; }
main { padding: 0 1rem 1rem; }
.dim { color: #999; }
.error { color: #f77; }
pre { background: #000; padding: .75rem; overflow-x: auto; user-select: all; }
button, input { font: inherit; padding: .4rem .7rem; }
#game { width: 100%; max-width: 960px; aspect-ratio: 4 / 3; margin: 0 auto; }
"""


def _load_script() -> str:
    from importlib import resources

    return resources.files("gotg.service").joinpath("static/play/app.js").read_text(encoding="utf-8")


def routes() -> dict[str, tuple[bytes, str]]:
    """`path -> (body, content type)` for the page's three static parts."""
    html = "text/html; charset=utf-8"
    return {
        "play/": (PAGE.encode(), html),
        "play/app.js": (_load_script().encode(), "text/javascript; charset=utf-8"),
        "play/app.css": (STYLE.encode(), "text/css; charset=utf-8"),
    }


EJS_PATH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*$")

EJS_TYPES = {
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".wasm": "application/wasm",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".woff2": "font/woff2",
}
