#!/usr/bin/env python3
"""A stand-in for thumbnails.libretro.com, enough of it to exercise matching.

The real server is an Apache directory index over four folders per system, and
that index *is* the search: there is no API and no key, so finding a game means
fetching the list of names and matching against it. This serves the same shape —
an Apache-style listing, percent-encoded and HTML-escaped exactly as Apache
emits it, because unescaping that correctly is most of the client's job.

The images are real PNGs with real dimensions, because the client decides which
Steam filename a box art becomes by looking at its aspect: a cartridge-era box
is landscape and belongs in the wide capsule, a disc-era one is portrait and
belongs in the library tile. A fake header would test nothing.

Endpoints:

    GET /<system>/<kind>/                 -> an Apache index of that folder
    GET /<system>/<kind>/<name>.png       -> a PNG

Usage: mock_libretro.py <port> [--empty] [--no-logos]
"""

from __future__ import annotations

import html
import struct
import sys
import urllib.parse
import zlib
from http.server import BaseHTTPRequestHandler, HTTPServer

SYSTEM = "Nintendo - Nintendo 64"

# One game with several releases to choose between, one with none of the
# complications, and one that exists only as a development build.
NAMES = [
    "Legend of Zelda, The - Majora's Mask (USA)",
    "Legend of Zelda, The - Majora's Mask (USA) (Rev 1)",
    "Legend of Zelda, The - Majora's Mask (Europe) (En,Fr,De,Es)",
    "Legend of Zelda, The - Majora's Mask (Japan)",
    "Legend of Zelda, The - Majora's Mask (USA) (Debug)",
    "Super Mario 64 (USA)",
    "Perfect Dark (USA) (Beta)",
]

# Disc-era art is portrait, cartridge-era art is landscape. Keyed by the game so
# a test can have both shapes from one server.
SHAPES = {"Super Mario 64 (USA)": (512, 712)}
DEFAULT_SHAPE = (512, 357)


def png(width: int, height: int) -> bytes:
    """A real, valid PNG of the given size — one grey rectangle."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x80" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


class Handler(BaseHTTPRequestHandler):
    empty = False
    no_logos = False

    def log_message(self, format, *args):  # noqa: A002 — quiet under bats
        pass

    def _send(self, code: int, body: bytes, kind: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.unquote(self.path.split("?")[0]).lstrip("/")
        parts = path.split("/")
        if len(parts) < 2 or parts[0] != SYSTEM:
            self._send(404, b"not here", "text/plain")
            return

        folder, name = parts[1], "/".join(parts[2:])
        if folder == "Named_Logos" and self.no_logos:
            self._send(404, b"no logos", "text/plain")
            return

        if not name:  # the directory index, which is the whole search
            names = [] if self.empty else NAMES
            rows = "".join(
                '<tr><td><a href="{}.png">{}.png</a></td></tr>'.format(
                    urllib.parse.quote(n), html.escape(n)
                )
                for n in names
            )
            body = f"<html><body><table>{rows}</table></body></html>".encode()
            self._send(200, body, "text/html;charset=UTF-8")
            return

        stem = name[:-4] if name.endswith(".png") else name
        if stem not in NAMES:
            self._send(404, b"no such image", "text/plain")
            return
        width, height = SHAPES.get(stem, DEFAULT_SHAPE)
        self._send(200, png(width, height), "image/png")


def main() -> int:
    port = int(sys.argv[1])
    Handler.empty = "--empty" in sys.argv
    Handler.no_logos = "--no-logos" in sys.argv
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
