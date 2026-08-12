"""Streaming the library: /games and /files.

What matters most: everything is resumable (Range, and If-Range so a resume
against changed bytes restarts instead of splicing), a symlink swapped in
after indexing opens nothing, and a missing file is a 404 for the client and
a loud log line for the operator — the index is stale, and only one of them
can do something about it.
"""

from __future__ import annotations

import json
import threading
import urllib.error
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


def test_a_full_download_carries_every_resume_header(service):
    base, _ = service
    status, body, headers = fetch(base, "/games/n64/usa.zelda/usa.zelda.z64")
    assert status == 200
    assert body == ROM
    assert headers["Content-Length"] == str(len(ROM))
    assert headers["Accept-Ranges"] == "bytes"
    assert headers["ETag"] == f'"{ROM_SHA}"'
    assert "usa.zelda.z64" in headers["Content-Disposition"]


def test_a_range_resumes_mid_file(service):
    base, _ = service
    status, body, headers = fetch(
        base, "/games/n64/usa.zelda/usa.zelda.z64", headers={"Range": "bytes=4-"}
    )
    assert status == 206
    assert body == ROM[4:]
    assert headers["Content-Range"] == f"bytes 4-{len(ROM) - 1}/{len(ROM)}"
    assert headers["Content-Length"] == str(len(ROM) - 4)


def test_a_bounded_range_is_honored(service):
    base, _ = service
    status, body, headers = fetch(
        base, "/games/n64/usa.zelda/usa.zelda.z64", headers={"Range": "bytes=2-5"}
    )
    assert status == 206
    assert body == ROM[2:6]
    assert headers["Content-Range"] == f"bytes 2-5/{len(ROM)}"


def test_a_range_past_the_end_is_a_416(service):
    base, _ = service
    status, _, headers = fetch(
        base, "/games/n64/usa.zelda/usa.zelda.z64", headers={"Range": "bytes=999-"}
    )
    assert status == 416
    assert headers["Content-Range"] == f"bytes */{len(ROM)}"


def test_if_range_with_the_current_etag_resumes(service):
    base, _ = service
    status, body, _ = fetch(
        base,
        "/games/n64/usa.zelda/usa.zelda.z64",
        headers={"Range": "bytes=4-", "If-Range": f'"{ROM_SHA}"'},
    )
    assert status == 206
    assert body == ROM[4:]


def test_if_range_with_a_stale_etag_restarts_instead_of_splicing(service):
    base, _ = service
    status, body, _ = fetch(
        base,
        "/games/n64/usa.zelda/usa.zelda.z64",
        headers={"Range": "bytes=4-", "If-Range": '"' + "e" * 64 + '"'},
    )
    assert status == 200
    assert body == ROM


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
