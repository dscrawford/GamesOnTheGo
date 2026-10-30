"""`/ui/`: the web UI's static files (src/gotg/service/static), served before authentication.

The files hold no secret -- the session is the credential -- so, like the admin
page, they answer before a bearer is looked for.
"""

from __future__ import annotations

from pathlib import Path

STATIC_DIR = Path(__file__).parent / "static"
PREFIX = "ui"

POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' blob: data:; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)

TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
}


def static_file(clean: str, root: Path = STATIC_DIR) -> tuple[bytes, str] | None:
    """`(body, content_type)` for a request path without its leading slash, or
    None when it is not under /ui/ or names nothing servable.

    Only the known extensions, only inside `root` after resolving: a symlink or
    `..` must not reach anything else.
    """
    if clean == PREFIX:
        return None
    if not clean.startswith(PREFIX + "/"):
        return None
    rel = clean[len(PREFIX) + 1 :] or "index.html"
    candidate = (root / rel).resolve()
    if not candidate.is_relative_to(root.resolve()) or candidate.suffix not in TYPES:
        return None
    try:
        return candidate.read_bytes(), TYPES[candidate.suffix]
    except OSError:
        return None


class StaticRoutes:
    def _send_static(self, body: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", POLICY)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
