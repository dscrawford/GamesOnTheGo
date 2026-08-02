#!/usr/bin/env python3
"""A stand-in for File Browser, just enough of it to exercise the client.

Implements the three behaviours the client depends on: a JSON login that returns
a bare token, /api/raw for a file (with Range support, so resume is really
tested), and /api/raw for a directory served as a zip.

Usage: mock_filebrowser.py <root-dir> <port> [--fail-after N] [--legacy-auth]

--fail-after drops the connection after N bytes, which is how the resume test
produces a genuinely truncated download rather than a simulated one.

--legacy-auth also accepts the token as an ?auth= query parameter. File Browser
removed that in 2.45.0, so the default is header-only: a test that turns this on
is testing an old server on purpose.
"""

from __future__ import annotations

import io
import json
import os
import sys
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

TOKEN = "test-token-12345"
USERNAME = "tester"
PASSWORD = "hunter2"


class Handler(BaseHTTPRequestHandler):
    root = "."
    fail_after = 0
    legacy_auth = False

    def log_message(self, *args):  # keep the test output readable
        pass

    def _authorized(self, query) -> bool:
        if self.headers.get("X-Auth") == TOKEN:
            return True
        return bool(self.legacy_auth and query.get("auth", [""])[0] == TOKEN)

    def _send(self, code, body=b"", ctype="application/octet-stream", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_POST(self):
        if urlparse(self.path).path != "/api/login":
            return self._send(404, b"not found")
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._send(400, b"bad json")
        if payload.get("username") != USERNAME or payload.get("password") != PASSWORD:
            return self._send(403, b"forbidden")
        return self._send(200, TOKEN.encode(), "text/plain")

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if not parsed.path.startswith("/api/raw"):
            return self._send(404, b"not found")
        if not self._authorized(query):
            return self._send(401, b"unauthorized")

        rel = unquote(parsed.path[len("/api/raw") :]).lstrip("/")
        target = os.path.join(self.root, rel.rstrip("/"))

        if query.get("algo", [""])[0] == "zip" or os.path.isdir(target):
            return self._serve_zip(target)
        if not os.path.isfile(target):
            return self._send(404, b"not found")
        return self._serve_file(target)

    def _serve_zip(self, target):
        if not os.path.isdir(target):
            return self._send(404, b"not found")
        buf = io.BytesIO()
        top = os.path.basename(target)
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
            for dirpath, _, filenames in os.walk(target):
                for name in filenames:
                    full = os.path.join(dirpath, name)
                    arc = os.path.join(top, os.path.relpath(full, target))
                    zf.write(full, arc)
        return self._send(200, buf.getvalue(), "application/zip")

    def _serve_file(self, target):
        with open(target, "rb") as fh:
            data = fh.read()

        start = 0
        status = 200
        headers = {"Accept-Ranges": "bytes"}
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            start = int(rng.split("=", 1)[1].split("-", 1)[0] or 0)
            status = 206
            headers["Content-Range"] = f"bytes {start}-{len(data) - 1}/{len(data)}"

        body = data[start:]
        if self.fail_after and len(body) > self.fail_after:
            # Truncate mid-transfer: announce the full length, send less, hang up.
            self.send_response(status)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            for k, v in headers.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body[: self.fail_after])
            self.wfile.flush()
            self.close_connection = True
            return None
        return self._send(status, body, extra=headers)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    Handler.root = args[0]
    port = int(args[1])
    if "--fail-after" in sys.argv:
        Handler.fail_after = int(sys.argv[sys.argv.index("--fail-after") + 1])
    Handler.legacy_auth = "--legacy-auth" in sys.argv

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
