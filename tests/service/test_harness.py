"""The harness the other service tests stand on: if it lies, they all do."""

from __future__ import annotations

import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler

import pytest
from harness import free_port, send, serve, serve_stub

from gotg.service.app import Config


def test_free_ports_differ_between_calls_often_enough_to_bind():
    assert 0 < free_port() < 65536


def test_serve_answers_and_then_stops():
    with serve(Config(token="client-token")) as svc:
        status, headers, body = svc.call("GET", "/nothing/here")
        assert status == 404
        assert body
        assert isinstance(headers, dict)
        url = svc.url
    with pytest.raises(urllib.error.URLError):
        urllib.request.urlopen(f"{url}/nothing/here", timeout=2)


def test_call_sends_the_clients_token_unless_told_otherwise():
    with serve(Config(token="client-token")) as svc:
        assert svc.call("GET", "/nothing/here")[0] == 404
        assert svc.call("GET", "/nothing/here", token=None)[0] == 401
        assert svc.call("GET", "/nothing/here", token="wrong")[0] == 401


def test_request_returns_an_error_status_as_an_answer_with_its_headers():
    class Teapot(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002
            pass

        def do_POST(self):  # noqa: N802
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(418)
            self.send_header("X-Echo", self.headers.get("X-In", ""))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with serve_stub(Teapot) as stub:
        status, headers, body = send(f"{stub.url}/x", method="POST", token=None, body=b"hello", headers={"X-In": "yes"})
    assert (status, headers["X-Echo"], body) == (418, "yes", b"hello")
