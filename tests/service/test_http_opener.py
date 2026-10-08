"""The opener that refuses to follow a redirect.

The proxy and the indexer's publisher each carried a copy of this handler,
for the same reason: urllib copies Authorization onto the redirected request
even across hosts, which hands a credential to whatever a Location names.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler

import pytest
from harness import serve_stub

from gotg._http import NO_REDIRECT_OPENER


class Redirecting(BaseHTTPRequestHandler):
    hits: list[str] = []

    def log_message(self, format, *args):  # noqa: A002
        pass

    def do_GET(self):  # noqa: N802
        Redirecting.hits.append(self.path)
        if self.path == "/start":
            self.send_response(302)
            self.send_header("Location", "/landed")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = b"landed"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_a_redirect_is_an_error_status_and_is_never_followed():
    Redirecting.hits = []
    with serve_stub(Redirecting) as stub:
        request = urllib.request.Request(f"{stub.url}/start")
        request.add_header("Authorization", "Bearer secret")
        with pytest.raises(urllib.error.HTTPError) as caught:
            NO_REDIRECT_OPENER.open(request, timeout=5)
        assert caught.value.code == 302
        assert caught.value.headers["Location"] == "/landed"
        caught.value.close()
    assert Redirecting.hits == ["/start"]


def test_an_ordinary_answer_is_untouched():
    Redirecting.hits = []
    with serve_stub(Redirecting) as stub:
        with NO_REDIRECT_OPENER.open(f"{stub.url}/landed", timeout=5) as response:
            assert (response.status, response.read()) == (200, b"landed")
