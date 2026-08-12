"""Publishing: the indexer's half that names raw sources instead of moving them.

What matters most: the member list is exactly the raw bytes a client recipe
needs, unchanged sources are never re-hashed, a 409 surfaces as the same
collision error the hardlink no-clobber used to raise, and no publish failure
ever stops an import.
"""

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from gotg_importer.execute import STATUS_DONE, STATUS_ERROR, Result
from gotg_importer.plan import (
    ACTION_ARCHIVE,
    ACTION_CONVERT,
    ACTION_EXTRACT,
    ACTION_HARDLINK,
    ACTION_MANUAL,
    Op,
)
from gotg_importer.publish import CatalogAPI, Collision, Publisher, PublishError, _members


class StubCatalog(BaseHTTPRequestHandler):
    """The catalog API surface the publisher touches, recorded."""

    games: list = []
    puts: list = []
    sweeps: list = []
    put_status = 200

    def log_message(self, *args):  # noqa: A002
        pass

    def _reply(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._reply(200, {"version": 2, "games": self.games})

    def do_PUT(self):
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length))
        self.puts.append((self.path, payload))
        if self.put_status == 409:
            self._reply(409, {"error": "entry exists with different paths", "stored": {}})
        else:
            self._reply(self.put_status, payload)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.sweeps.append(json.loads(self.rfile.read(length)))
        self._reply(200, {"total": 1, "vanished": [{"platform": "n64", "id": "usa.gone"}]})


@pytest.fixture
def stub():
    handler = type("Bound", (StubCatalog,), {"games": [], "puts": [], "sweeps": []})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", handler
    server.shutdown()


def link_result(tmp_path, content=b"rom!", entry_id="usa.zelda"):
    src = tmp_path / "Torrents" / "Legend of Zelda (USA).z64"
    src.parent.mkdir(exist_ok=True)
    src.write_bytes(content)
    op = Op(
        ACTION_HARDLINK,
        "n64",
        str(src),
        str(tmp_path / "Games" / "n64" / f"{entry_id}.z64"),
        entry_id,
        title="Zelda",
        handler="single_file",
    )
    return Result(op, STATUS_DONE)


# --- member enumeration -------------------------------------------------------


def test_a_hardlink_travels_under_its_canonical_name(tmp_path):
    result = link_result(tmp_path)
    members = _members(result.op)
    assert [(m.name, m.path.name) for m in members] == [("usa.zelda.z64", "Legend of Zelda (USA).z64")]


def test_a_scene_release_lists_its_volume_set(tmp_path):
    release = tmp_path / "Torrents" / "Game_NSW-GROUP"
    release.mkdir(parents=True)
    for name in ["group.rar", "group.r00", "group.sfv", "group.nfo"]:
        (release / name).write_bytes(b"x")
    (release / "Sample").mkdir()  # directories are not members
    op = Op(ACTION_EXTRACT, "switch", str(release), "/g/switch/world.game.xci", "world.game", handler="scene_archive")
    assert [m.name for m in _members(op)] == [
        "group.nfo",
        "group.r00",
        "group.rar",
        "group.sfv",
    ]


def test_a_single_archive_is_one_member_under_its_real_name(tmp_path):
    archive = tmp_path / "Torrents" / "Game (USA).7z"
    archive.parent.mkdir(exist_ok=True)
    archive.write_bytes(b"7z!")
    op = Op(ACTION_CONVERT, "gamecube", str(archive), "/g/gamecube/usa.game.rvz", "usa.game", handler="single_archive")
    assert [(m.name, str(m.path)) for m in _members(op)] == [("Game (USA).7z", str(archive))]


def test_a_wiiu_tree_keeps_its_relative_names(tmp_path):
    dump = tmp_path / "Torrents" / "Game [0005000010143600]"
    (dump / "code").mkdir(parents=True)
    (dump / "content" / "scene").mkdir(parents=True)
    (dump / "code" / "app.rpx").write_bytes(b"rpx")
    (dump / "content" / "scene" / "x.pack").write_bytes(b"pack")
    op = Op(ACTION_ARCHIVE, "wiiu", str(dump), "/g/wiiu/usa.game.zip", "usa.game", handler="wiiu_decrypted")
    assert [m.name for m in _members(op)] == ["code/app.rpx", "content/scene/x.pack"]


# --- publishing ----------------------------------------------------------------


def test_a_link_publishes_handler_title_and_hash(stub, tmp_path):
    base, handler = stub
    result = link_result(tmp_path)
    Publisher(CatalogAPI(base, "index-token")).publish(result)

    path, payload = handler.puts[0]
    assert path == "/catalog/n64/usa.zelda"
    assert payload["handler"] == "single_file"
    assert payload["title"] == "Zelda"
    member = payload["files"][0]
    assert member["name"] == "usa.zelda.z64"
    assert member["sha256"] == hashlib.sha256(b"rom!").hexdigest()
    assert member["size_bytes"] == 4


def test_skip_manual_error_and_idless_results_publish_nothing(stub, tmp_path):
    base, handler = stub
    publisher = Publisher(CatalogAPI(base, "t"))
    src = tmp_path / "x"
    src.write_bytes(b"x")
    for result in [
        Result(Op(ACTION_MANUAL, "n64", str(src), "", "", handler="manual"), STATUS_DONE),
        Result(link_result(tmp_path).op, STATUS_ERROR),
        Result(Op(ACTION_HARDLINK, "n64", str(src), "/g/n64/x.z64", "", handler="single_file"), STATUS_DONE),
    ]:
        publisher.publish(result)
    assert handler.puts == []


def test_an_unchanged_member_keeps_the_stored_hash_without_rehashing(stub, tmp_path, monkeypatch):
    base, handler = stub
    result = link_result(tmp_path)
    src = result.op.src
    from pathlib import Path

    st = Path(src).stat()
    handler.games = [
        {
            "platform": "n64",
            "id": "usa.zelda",
            "files": [
                {
                    "name": "usa.zelda.z64",
                    "path": src,
                    "size_bytes": st.st_size,
                    "mtime": int(st.st_mtime),
                    "sha256": "f" * 64,
                }
            ],
        }
    ]

    def never(*args, **kwargs):
        raise AssertionError("an unchanged member must not be re-hashed")

    monkeypatch.setattr("gotg_importer.publish.ex.sha256_file", never)
    Publisher(CatalogAPI(base, "t")).publish(result)
    assert handler.puts[0][1]["files"][0]["sha256"] == "f" * 64


def test_a_changed_member_is_rehashed(stub, tmp_path):
    base, handler = stub
    result = link_result(tmp_path)
    handler.games = [
        {
            "platform": "n64",
            "id": "usa.zelda",
            "files": [
                {"name": "usa.zelda.z64", "path": result.op.src, "size_bytes": 999, "mtime": 1, "sha256": "f" * 64}
            ],
        }
    ]
    Publisher(CatalogAPI(base, "t")).publish(result)
    assert handler.puts[0][1]["files"][0]["sha256"] == hashlib.sha256(b"rom!").hexdigest()


def test_a_link_reuses_the_sidecar_execute_wrote(stub, tmp_path, monkeypatch):
    base, handler = stub
    result = link_result(tmp_path)
    dst = tmp_path / "Games" / "n64" / "usa.zelda.z64"
    dst.parent.mkdir(parents=True)
    (tmp_path / "Games" / "n64" / "usa.zelda.z64.sha256").write_text("e" * 64 + "\n")

    def never(*args, **kwargs):
        raise AssertionError("the sidecar hash was already paid for")

    monkeypatch.setattr("gotg_importer.publish.ex.sha256_file", never)
    Publisher(CatalogAPI(base, "t")).publish(result)
    assert handler.puts[0][1]["files"][0]["sha256"] == "e" * 64


def test_no_hash_is_refused_unless_allowed(stub, tmp_path):
    base, handler = stub
    result = link_result(tmp_path)
    with pytest.raises(PublishError, match="allow-unhashed"):
        Publisher(CatalogAPI(base, "t")).publish(result, checksum=False)
    Publisher(CatalogAPI(base, "t"), allow_unhashed=True).publish(result, checksum=False)
    assert handler.puts[0][1]["files"][0]["sha256"] is None


def test_a_409_is_a_collision(stub, tmp_path):
    base, handler = stub
    handler.put_status = 409
    with pytest.raises(Collision):
        Publisher(CatalogAPI(base, "t")).publish(link_result(tmp_path))


def test_sweep_reports_the_vanished(stub, caplog):
    base, handler = stub
    publisher = Publisher(CatalogAPI(base, "t"))
    with caplog.at_level("WARNING", logger="gotg.publish"):
        publisher.sweep("2026-01-01T00:00:00Z")
    assert handler.sweeps == [{"since": "2026-01-01T00:00:00Z"}]
    assert "usa.gone" in caplog.text


def test_an_unreachable_api_is_a_publish_error():
    with pytest.raises(PublishError, match="unreachable"):
        Publisher(CatalogAPI("http://127.0.0.1:1", "t"))


def test_diff_catalog_maps_links_by_bytes_and_derived_by_presence(stub):
    from gotg_importer.manifest import Entry
    from gotg_importer.publish import diff_catalog

    base, handler = stub
    handler.games = [
        {
            "platform": "n64",
            "id": "usa.zelda",
            "handler": "single_file",
            "files": [{"name": "usa.zelda.z64", "size_bytes": 4, "sha256": "a" * 64, "path": "/t/z.z64", "mtime": 1}],
        },
        {
            "platform": "gamecube",
            "id": "usa.sunshine",
            "handler": "single_archive",
            "files": [{"name": "Sunshine.7z", "size_bytes": 999, "sha256": "b" * 64, "path": "/t/s.7z", "mtime": 1}],
        },
        {
            "platform": "n64",
            "id": "usa.stray",
            "handler": "single_file",
            "files": [{"name": "usa.stray.z64", "size_bytes": 1, "sha256": None, "path": "/t/x.z64", "mtime": 1}],
        },
    ]
    manifest = {
        "/Games/n64/usa.zelda.z64": Entry("n64", "/Games/n64/usa.zelda.z64", "file", 4, "a" * 64, "Zelda"),
        # Derived: sizes and hashes deliberately differ from the raw source.
        "/Games/gamecube/usa.sunshine.rvz": Entry(
            "gamecube", "/Games/gamecube/usa.sunshine.rvz", "file", 123, "c" * 64, "Sunshine"
        ),
        "/Games/snes/usa.metroid.sfc": Entry("snes", "/Games/snes/usa.metroid.sfc", "file", 5, None, "Metroid"),
    }
    problems = diff_catalog(manifest, CatalogAPI(base, "t"))
    assert problems == [
        "missing from catalog: snes/usa.metroid",
        "in catalog but not the manifest: n64/usa.stray",
    ]


def test_diff_catalog_flags_a_link_hash_mismatch(stub):
    from gotg_importer.manifest import Entry
    from gotg_importer.publish import diff_catalog

    base, handler = stub
    handler.games = [
        {
            "platform": "n64",
            "id": "usa.zelda",
            "handler": "single_file",
            "files": [{"name": "usa.zelda.z64", "size_bytes": 4, "sha256": "a" * 64, "path": "/t/z.z64", "mtime": 1}],
        },
    ]
    manifest = {
        "/Games/n64/usa.zelda.z64": Entry("n64", "/Games/n64/usa.zelda.z64", "file", 4, "d" * 64, "Zelda"),
    }
    assert diff_catalog(manifest, CatalogAPI(base, "t")) == ["hash mismatch: n64/usa.zelda"]
