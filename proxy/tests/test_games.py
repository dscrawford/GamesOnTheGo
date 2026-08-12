"""Streaming the library: /games and /files.

What matters most: everything is resumable (Range, and If-Range so a resume
against changed bytes restarts instead of splicing), a symlink swapped in
after indexing opens nothing, and a missing file is a 404 for the client and
a loud log line for the operator — the index is stale, and only one of them
can do something about it.
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest
from test_proxy import free_port

from gotg_proxy.app import Config, make_server
from gotg_proxy.catalog import CatalogStore

CLIENT = "client-token"
INDEX = "index-token"
ROM = b"0123456789abcdef"
ROM_SHA = "d" * 64  # asserted as a validator string, never verified here


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "Torrents"
    (root / "some-release").mkdir(parents=True)
    (root / "some-release" / "usa.zelda.z64").write_bytes(ROM)
    return root


@pytest.fixture
def files_dir(tmp_path):
    root = tmp_path / "files"
    (root / "switch").mkdir(parents=True)
    (root / "switch" / "prod.keys").write_text("master_key_00 = deadbeef\n")
    return root


@pytest.fixture
def catalog(tmp_path, library):
    store = CatalogStore(db=tmp_path / "state" / "catalog.db", roots=[library])
    store.upsert(
        "n64",
        "usa.zelda",
        {
            "handler": "single_file",
            "title": "Zelda",
            "files": [
                {
                    "name": "usa.zelda.z64",
                    "path": str(library / "some-release" / "usa.zelda.z64"),
                    "size_bytes": len(ROM),
                    "mtime": 1700000000,
                    "sha256": ROM_SHA,
                }
            ],
        },
    )
    return store


@pytest.fixture
def service(catalog, files_dir):
    config = Config(token=CLIENT, index_token=INDEX)
    server = make_server(
        "127.0.0.1", free_port(), config, None, catalog, files_dir=files_dir, stream_slots=2
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", server
    server.shutdown()


def fetch(base, path, *, method="GET", token: str | None = CLIENT, headers=None):
    request = urllib.request.Request(f"{base}{path}", method=method)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as error:
        return error.code, error.read(), dict(error.headers or {})


def add_member(catalog, library, game_id, name, data, sha=None):
    path = library / "some-release" / name
    path.write_bytes(data)
    catalog.upsert(
        "n64",
        game_id,
        {
            "handler": "single_file",
            "title": "Extra",
            "files": [
                {"name": name, "path": str(path), "size_bytes": len(data), "mtime": 1, "sha256": sha}
            ],
        },
    )
    return f"/games/n64/{game_id}/{urllib.parse.quote(name)}"


def test_a_full_download_carries_every_resume_header(service):
    base, _ = service
    status, body, headers = fetch(base, "/games/n64/usa.zelda/usa.zelda.z64")
    assert status == 200
    assert body == ROM
    assert headers["Content-Length"] == str(len(ROM))
    assert headers["Accept-Ranges"] == "bytes"
    assert headers["ETag"] == f'"{ROM_SHA}"'
    assert "usa.zelda.z64" in headers["Content-Disposition"]


@pytest.mark.parametrize(
    ("range_header", "status", "piece", "content_range"),
    [
        ("bytes=4-", 206, ROM[4:], f"bytes 4-{len(ROM) - 1}/{len(ROM)}"),
        ("bytes=2-5", 206, ROM[2:6], f"bytes 2-5/{len(ROM)}"),
        ("bytes=0-0", 206, ROM[:1], f"bytes 0-0/{len(ROM)}"),
        (f"bytes={len(ROM) - 1}-", 206, ROM[-1:], f"bytes {len(ROM) - 1}-{len(ROM) - 1}/{len(ROM)}"),
        ("bytes=0-999", 206, ROM, f"bytes 0-{len(ROM) - 1}/{len(ROM)}"),
        (" bytes=0-5", 206, ROM[:6], f"bytes 0-5/{len(ROM)}"),
        (f"bytes={len(ROM)}-", 416, b"", f"bytes */{len(ROM)}"),
        ("bytes=999-", 416, b"", f"bytes */{len(ROM)}"),
        # An inverted range invalidates the whole header, which is ignored,
        # not refused (RFC 9110 s14.1.1).
        ("bytes=5-2", 200, ROM, None),
        # Shapes RANGE_RE refuses all fall back to 200 with the whole file — a
        # restart, never a corrupt splice.
        ("bytes=-5", 200, ROM, None),
        ("bytes=0-1,3-4", 200, ROM, None),
        ("bytes = 0-5", 200, ROM, None),
        ("Bytes=0-5", 200, ROM, None),
        ("bytes=", 200, ROM, None),
        ("0-5", 200, ROM, None),
        ("bytes=" + "9" * 40 + "-", 200, ROM, None),
    ],
)
def test_range_semantics(service, range_header, status, piece, content_range):
    base, _ = service
    got, body, headers = fetch(
        base, "/games/n64/usa.zelda/usa.zelda.z64", headers={"Range": range_header}
    )
    assert got == status
    assert body == piece
    assert headers.get("Content-Range") == content_range
    assert headers["Content-Length"] == str(len(piece))


@pytest.mark.parametrize(
    ("validator", "status", "piece"),
    [
        (f'"{ROM_SHA}"', 206, ROM[4:]),
        ('"' + "e" * 64 + '"', 200, ROM),
        ("Wed, 21 Oct 2015 07:28:00 GMT", 200, ROM),
    ],
)
def test_if_range_resumes_only_against_the_current_etag(service, validator, status, piece):
    base, _ = service
    got, body, _ = fetch(
        base,
        "/games/n64/usa.zelda/usa.zelda.z64",
        headers={"Range": "bytes=4-", "If-Range": validator},
    )
    assert (got, body) == (status, piece)


def test_head_answers_headers_without_a_body(service):
    base, _ = service
    status, body, headers = fetch(base, "/games/n64/usa.zelda/usa.zelda.z64", method="HEAD")
    assert status == 200
    assert body == b""
    assert headers["Content-Length"] == str(len(ROM))
    assert headers["ETag"] == f'"{ROM_SHA}"'


@pytest.mark.parametrize(
    "path",
    [
        "/games/n64/usa.zelda/missing.z64",
        "/games/n64/usa.ghost/usa.zelda.z64",
        "/games/gb/usa.zelda/usa.zelda.z64",
        "/games/n64/usa.zelda",
        "/games/n64/usa.zelda/usa.zelda.z64/extra",
    ],
)
def test_unknown_halves_are_404(service, path):
    base, _ = service
    status, _, _ = fetch(base, path)
    assert status == 404


def test_a_row_whose_file_vanished_is_a_404(service, library):
    base, _ = service
    (library / "some-release" / "usa.zelda.z64").unlink()
    status, _, _ = fetch(base, "/games/n64/usa.zelda/usa.zelda.z64")
    assert status == 404


def test_downloads_need_a_token_but_not_the_index_token(service):
    base, _ = service
    status, _, _ = fetch(base, "/games/n64/usa.zelda/usa.zelda.z64", token=None)
    assert status == 401
    status, body, _ = fetch(base, "/games/n64/usa.zelda/usa.zelda.z64", token=INDEX)
    assert status == 200 and body == ROM


def test_the_stream_cap_answers_503_with_retry_after(service):
    base, server = service
    handler = server.RequestHandlerClass
    # Both slots taken by (simulated) slow streams; the next byte-moving
    # request must bounce rather than queue a third thread.
    assert handler.streams.acquire(blocking=False)
    assert handler.streams.acquire(blocking=False)
    try:
        status, _, headers = fetch(base, "/games/n64/usa.zelda/usa.zelda.z64")
        assert status == 503
        assert headers["Retry-After"] == "5"
        # HEAD moves no bytes and must not need a slot.
        status, _, _ = fetch(base, "/games/n64/usa.zelda/usa.zelda.z64", method="HEAD")
        assert status == 200
    finally:
        handler.streams.release()
        handler.streams.release()
    status, _, _ = fetch(base, "/games/n64/usa.zelda/usa.zelda.z64")
    assert status == 200


def test_files_serves_the_hand_placed_keys(service):
    base, _ = service
    status, body, _ = fetch(base, "/files/switch/prod.keys")
    assert status == 200
    assert b"master_key_00" in body


def test_files_supports_resume_too(service):
    base, _ = service
    status, body, _ = fetch(base, "/files/switch/prod.keys", headers={"Range": "bytes=7-"})
    assert status == 206
    assert body.startswith(b"key_00")


@pytest.mark.parametrize(
    "path",
    [
        "/files/switch/title.keys",
        "/files/gb/prod.keys",
        "/files/switch/.hidden",
        "/files/switch/PROD.KEYS",
        "/files/switch",
        "/files/switch/prod.keys/extra",
        "/files/Switch/prod.keys",
        "/files/sw.itch/prod.keys",
        "/files/-switch/prod.keys",
        "/files/" + "s" * 17 + "/prod.keys",
        "/files/switch/" + "k" * 65,
    ],
)
def test_files_absent_or_misshapen_is_404(service, path):
    base, _ = service
    status, _, _ = fetch(base, path)
    assert status == 404


def test_an_encoded_traversal_is_refused_before_routing(service):
    base, _ = service
    status, _, _ = fetch(base, "/files/switch/%2e%2e")
    assert status == 400


def test_files_never_follows_a_symlink(service, files_dir, tmp_path):
    base, _ = service
    secret = tmp_path / "secret"
    secret.write_text("shh")
    (files_dir / "switch" / "firmware.zip").symlink_to(secret)
    status, _, _ = fetch(base, "/files/switch/firmware.zip")
    assert status == 404


def test_a_service_without_a_files_dir_says_so(catalog):
    config = Config(token=CLIENT)
    server = make_server("127.0.0.1", free_port(), config, None, catalog)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        status, body, _ = fetch(base, "/files/switch/prod.keys")
        assert status == 503
        assert json.loads(body)["error"] == "this service holds no files directory"
    finally:
        server.shutdown()


def test_a_large_file_streams_intact_with_a_mid_file_resume(service, library, catalog):
    # Multi-chunk: bigger than one sendfile call's worth is not practical in a
    # unit test, but crossing the fallback read chunk (64 KiB) is.
    blob = bytes(range(256)) * 1024  # 256 KiB
    (library / "some-release" / "usa.big.z64").write_bytes(blob)
    catalog.upsert(
        "n64",
        "usa.big",
        {
            "handler": "single_file",
            "title": "Big",
            "files": [
                {
                    "name": "usa.big.z64",
                    "path": str(library / "some-release" / "usa.big.z64"),
                    "size_bytes": len(blob),
                    "mtime": 1,
                    "sha256": None,
                }
            ],
        },
    )
    base, _ = service
    status, body, headers = fetch(base, "/games/n64/usa.big/usa.big.z64")
    assert status == 200 and body == blob
    assert "ETag" not in headers, "no hash, no validator"
    status, tail, _ = fetch(
        base, "/games/n64/usa.big/usa.big.z64", headers={"Range": f"bytes={len(blob) // 2}-"}
    )
    assert status == 206 and tail == blob[len(blob) // 2 :]


def test_if_range_without_a_stored_hash_sends_the_whole_file(service, library, catalog):
    # No sha means no validator: an unverifiable resume must restart, and a
    # plain Range with nothing to verify against must still work.
    path = add_member(catalog, library, "usa.nosha", "usa.nosha.z64", ROM)
    base, _ = service
    status, body, _ = fetch(base, path, headers={"Range": "bytes=4-", "If-Range": f'"{ROM_SHA}"'})
    assert (status, body) == (200, ROM)
    status, body, _ = fetch(base, path, headers={"Range": "bytes=4-"})
    assert (status, body) == (206, ROM[4:])


def test_a_zero_byte_file_streams_and_refuses_every_range(service, library, catalog):
    path = add_member(catalog, library, "usa.empty", "usa.empty.z64", b"")
    base, _ = service
    status, body, headers = fetch(base, path)
    assert (status, body, headers["Content-Length"]) == (200, b"", "0")
    status, _, headers = fetch(base, path, headers={"Range": "bytes=0-"})
    assert (status, headers["Content-Range"]) == (416, "bytes */0")


def test_head_with_a_range_carries_206_headers_and_no_body(service):
    base, _ = service
    status, body, headers = fetch(
        base, "/games/n64/usa.zelda/usa.zelda.z64", method="HEAD", headers={"Range": "bytes=2-5"}
    )
    assert (status, body) == (206, b"")
    assert headers["Content-Range"] == f"bytes 2-5/{len(ROM)}"
    assert headers["Content-Length"] == "4"
    status, body, _ = fetch(
        base, "/games/n64/usa.zelda/usa.zelda.z64", method="HEAD", headers={"Range": "bytes=999-"}
    )
    assert (status, body) == (416, b"")


def test_a_head_404_is_bodiless_and_the_connection_stays_framed(service):
    # _send writing a JSON body to a HEAD error would sit on the kept-alive
    # connection and be parsed as the start of the next response.
    base, server = service
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    try:
        conn.request(
            "HEAD", "/games/none/usa.ghost/x.z64",
            headers={"Authorization": f"Bearer {CLIENT}"},
        )
        response = conn.getresponse()
        assert response.status == 404
        assert response.read() == b""
        conn.request(
            "GET", "/games/n64/usa.zelda/usa.zelda.z64",
            headers={"Authorization": f"Bearer {CLIENT}"},
        )
        response = conn.getresponse()
        assert (response.status, response.read()) == (200, ROM)
    finally:
        conn.close()


def test_a_non_ascii_member_name_round_trips_url_and_disposition(service, library, catalog):
    name = "ポケモン ①.z64"
    path = add_member(catalog, library, "usa.mon", name, ROM, sha="a" * 64)
    base, _ = service
    status, body, headers = fetch(base, path)
    assert (status, body) == (200, ROM)
    assert headers["Content-Disposition"] == f"attachment; filename*=UTF-8''{urllib.parse.quote(name)}"


def test_the_sendfile_fallback_streams_the_same_bytes(service, monkeypatch):
    # socket.sendfile falls back to plain send() internally when the zero-copy
    # path is refused outright.
    def refused(*args, **kwargs):
        raise OSError(38, "sendfile not supported")

    monkeypatch.setattr(os, "sendfile", refused)
    base, _ = service
    status, body, _ = fetch(base, "/games/n64/usa.zelda/usa.zelda.z64")
    assert (status, body) == (200, ROM)
    status, body, _ = fetch(
        base, "/games/n64/usa.zelda/usa.zelda.z64", headers={"Range": "bytes=4-9"}
    )
    assert (status, body) == (206, ROM[4:10])


def test_a_kept_alive_connection_survives_full_partial_and_416_streams(service):
    base, server = service
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    try:
        sock = None
        for extra, status, piece in [
            ({}, 200, ROM),
            ({"Range": "bytes=4-"}, 206, ROM[4:]),
            ({"Range": "bytes=999-"}, 416, b""),
            ({}, 200, ROM),
        ]:
            conn.request(
                "GET",
                "/games/n64/usa.zelda/usa.zelda.z64",
                headers={"Authorization": f"Bearer {CLIENT}", **extra},
            )
            response = conn.getresponse()
            assert (response.status, response.read()) == (status, piece)
            sock = sock or conn.sock
            assert conn.sock is sock, "a completed stream must leave the connection framed"
    finally:
        conn.close()


def test_two_pinned_streams_saturate_the_cap_for_real(service, library, catalog):
    base, server = service
    big = library / "some-release" / "usa.big.z64"
    with open(big, "wb") as handle:
        handle.truncate(64 << 20)  # sparse: outgrows every socket buffer, costs no disk
    catalog.upsert(
        "n64",
        "usa.big",
        {
            "handler": "single_file",
            "title": "Big",
            "files": [
                {"name": "usa.big.z64", "path": str(big), "size_bytes": 64 << 20,
                 "mtime": 1, "sha256": None}
            ],
        },
    )

    def pin():
        sock = socket.socket()
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        sock.settimeout(10)
        sock.connect(("127.0.0.1", server.server_port))
        sock.sendall(
            b"GET /games/n64/usa.big/usa.big.z64 HTTP/1.1\r\nHost: service\r\n"
            + f"Authorization: Bearer {CLIENT}\r\n\r\n".encode()
        )
        head = b""
        while b"\r\n\r\n" not in head:
            head += sock.recv(1024)
        # Headers received means the slot is held; the tiny receive buffer
        # keeps it held until we hang up.
        assert head.startswith(b"HTTP/1.1 200")
        return sock

    first, second = pin(), pin()
    try:
        status, _, headers = fetch(base, "/games/n64/usa.zelda/usa.zelda.z64")
        assert (status, headers["Retry-After"]) == (503, "5")
        status, _, _ = fetch(base, "/games/n64/usa.zelda/usa.zelda.z64", method="HEAD")
        assert status == 200
    finally:
        first.close()
        second.close()

    # The dying sockets error out of sendfile; the finally must give the
    # slots back rather than leak them.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        status, _, _ = fetch(base, "/games/n64/usa.zelda/usa.zelda.z64")
        if status == 200:
            break
        time.sleep(0.2)
    assert status == 200


def test_a_query_string_does_not_change_which_file_is_served(service):
    base, _ = service
    status, body, _ = fetch(base, "/files/switch/prod.keys?cache=no")
    assert status == 200 and b"master_key_00" in body


def test_files_never_follows_a_symlinked_platform_directory(service, files_dir, tmp_path):
    # O_NOFOLLOW only guards the final component; the /proc re-check in
    # open_contained is what has to catch a retargeted directory.
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "prod.keys").write_text("shh")
    (files_dir / "wii").symlink_to(outside)
    base, _ = service
    status, _, _ = fetch(base, "/files/wii/prod.keys")
    assert status == 404


def test_a_nested_member_streams_through_its_nested_url(service, library, catalog):
    release = library / "some-release" / "wiiu-rel"
    (release / "code").mkdir(parents=True)
    (release / "code" / "app.rpx").write_bytes(b"rpx!")
    catalog.upsert(
        "n64",
        "usa.tree",
        {
            "handler": "wiiu_decrypted",
            "title": "Tree",
            "files": [
                {"name": "code/app.rpx", "path": str(release / "code" / "app.rpx"),
                 "size_bytes": 4, "mtime": 1, "sha256": None}
            ],
        },
    )
    base, _ = service
    status, body, headers = fetch(base, "/games/n64/usa.tree/code/app.rpx")
    assert (status, body) == (200, b"rpx!")
    assert "app.rpx" in headers["Content-Disposition"]
    assert "code" not in headers["Content-Disposition"]
