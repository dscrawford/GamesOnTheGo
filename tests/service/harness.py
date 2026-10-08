"""One way to start the service on a free port and one way to talk to it.

One context manager starts and stops the service on a free port; `call()`
always answers `(status, headers, body)`.

It is a module and not `conftest.py` because test files import from it by
name (as they do `test_proxy`), and `tests/conftest.py` already owns that name
(two `conftest` modules shadow each other).
"""

from __future__ import annotations

import socket
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer

from gotg.service.app import Config, make_server

CLIENT_TOKEN = "client-token"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def send(
    url: str,
    method: str | None = None,
    token: str | None = CLIENT_TOKEN,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 10,
) -> tuple[int, dict[str, str], bytes]:
    """`(status, headers, body)` for any status: an HTTP error is an answer here, not an exception."""
    req = urllib.request.Request(url, data=body, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    for name, value in (headers or {}).items():
        req.add_header(name, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers or {}), error.read()


@dataclass(frozen=True)
class Served:
    """A running service: the server (for `RequestHandlerClass`, `server_close`), its address, and a bound `call`."""

    server: HTTPServer
    url: str
    token: str | None = CLIENT_TOKEN

    @property
    def port(self) -> int:
        return self.server.server_port

    def call(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        token: str | None = ...,  # type: ignore[assignment]
    ) -> tuple[int, dict[str, str], bytes]:
        """`token` defaults to the service's client token; pass None to send no Authorization at all."""
        return send(
            f"{self.url}{path}",
            method=method,
            token=self.token if token is ... else token,
            body=body,
            headers=headers,
        )


def _run(server: HTTPServer, token: str | None = CLIENT_TOKEN) -> Served:
    threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True).start()
    return Served(server, f"http://127.0.0.1:{server.server_port}", token)


@contextmanager
def serve(config: Config, *args, **kwargs) -> Iterator[Served]:
    """The service on a free port with `config` (the rest goes to `make_server` as given), stopped on exit."""
    server = make_server("127.0.0.1", free_port(), config, *args, **kwargs)
    try:
        yield _run(server, config.token or None)
    finally:
        server.shutdown()
        server.server_close()


@contextmanager
def serve_stub(handler: type[BaseHTTPRequestHandler]) -> Iterator[Served]:
    """A stub upstream (a bare `HTTPServer`) on a free port, stopped on exit."""
    server = HTTPServer(("127.0.0.1", free_port()), handler)
    try:
        yield _run(server, None)
    finally:
        server.shutdown()
        server.server_close()
