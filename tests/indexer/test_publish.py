"""Publishing: the indexer's half that names raw sources instead of moving them.

What matters most: the member list is exactly the raw bytes a client recipe
needs, unchanged sources are never re-hashed, a 409 surfaces as the same
collision error the hardlink no-clobber used to raise, and no publish failure
ever stops an import.
"""

import hashlib
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from gotg.indexer.execute import STATUS_DONE, STATUS_ERROR, Result
from gotg.indexer.plan import (
    ACTION_ARCHIVE,
    ACTION_ATTACH,
    ACTION_CONVERT,
    ACTION_EXTRACT,
    ACTION_HARDLINK,
    ACTION_MANUAL,
    Op,
)
from gotg.indexer.publish import CatalogAPI, Collision, Publisher, PublishError, _members


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
        payload = json.loads(self.rfile.read(length))
        self.sweeps.append(payload)
        if self.path.endswith("/seen"):
            self._reply(200, {"seen": len(payload.get("games", [])), "asked": len(payload.get("games", []))})
            return
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


@pytest.mark.parametrize("action", [ACTION_CONVERT, ACTION_EXTRACT])
def test_a_single_archive_file_is_one_member_under_its_real_name(tmp_path, action):
    # ACTION_EXTRACT on a lone file is the scene shortcut: same branch as convert.
    archive = tmp_path / "Torrents" / "Game (USA).7z"
    archive.parent.mkdir(exist_ok=True)
    archive.write_bytes(b"7z!")
    op = Op(action, "gamecube", str(archive), "/g/gamecube/usa.game.rvz", "usa.game", handler="single_archive")
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

    monkeypatch.setattr("gotg.indexer.publish.ex.sha256_file", never)
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
    import os

    os.link(result.op.src, dst)  # dual-publish: execute linked before we publish
    (tmp_path / "Games" / "n64" / "usa.zelda.z64.sha256").write_text("e" * 64 + "\n")

    def never(*args, **kwargs):
        raise AssertionError("the sidecar hash was already paid for")

    monkeypatch.setattr("gotg.indexer.publish.ex.sha256_file", never)
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
    from gotg.indexer.manifest import Entry
    from gotg.indexer.publish import diff_catalog

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
    from gotg.indexer.manifest import Entry
    from gotg.indexer.publish import diff_catalog

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


def test_files_inside_a_scene_subdirectory_are_not_members(tmp_path):
    release = tmp_path / "Torrents" / "Game_NSW-GROUP"
    (release / "Sample").mkdir(parents=True)
    (release / "group.rar").write_bytes(b"x")
    (release / "Sample" / "sample.mkv").write_bytes(b"not the game")
    op = Op(ACTION_EXTRACT, "switch", str(release), "/g/switch/world.game.xci", "world.game", handler="scene_archive")
    assert [m.name for m in _members(op)] == ["group.rar"]


def test_an_archive_tree_does_pick_up_the_same_nested_files(tmp_path):
    # The deliberate contrast with the scene case: rglob descends, iterdir does not.
    release = tmp_path / "Torrents" / "Game [0005000010143600]"
    (release / "Sample").mkdir(parents=True)
    (release / "group.rar").write_bytes(b"x")
    (release / "Sample" / "sample.mkv").write_bytes(b"x")
    op = Op(ACTION_ARCHIVE, "wiiu", str(release), "/g/wiiu/usa.game.zip", "usa.game", handler="wiiu_decrypted")
    assert [m.name for m in _members(op)] == ["Sample/sample.mkv", "group.rar"]


def test_a_vanished_source_file_is_a_publish_error(stub, tmp_path):
    base, _ = stub
    result = link_result(tmp_path)
    Path(result.op.src).unlink()
    with pytest.raises(PublishError, match="cannot stat"):
        Publisher(CatalogAPI(base, "t")).publish(result)


def test_a_vanished_extract_dir_is_a_publish_error_not_a_crash(stub, tmp_path):
    # A pruned torrent must stay a per-entry failure — FileNotFoundError out of
    # iterdir would escape run_paths' except clause and abort the whole import.
    base, _ = stub
    op = Op(
        ACTION_EXTRACT,
        "switch",
        str(tmp_path / "gone_NSW-GROUP"),
        "/g/switch/world.game.xci",
        "world.game",
        handler="scene_archive",
    )
    with pytest.raises(PublishError):
        Publisher(CatalogAPI(base, "t")).publish(Result(op, STATUS_DONE))


def test_a_vanished_archive_dir_is_a_publish_error(stub, tmp_path):
    base, _ = stub
    op = Op(
        ACTION_ARCHIVE, "wiiu", str(tmp_path / "gone"), "/g/wiiu/usa.game.zip", "usa.game", handler="wiiu_decrypted"
    )
    with pytest.raises(PublishError, match="nothing to publish"):
        Publisher(CatalogAPI(base, "t")).publish(Result(op, STATUS_DONE))


def test_a_fractional_mtime_still_matches_the_stored_integer_row(stub, tmp_path, monkeypatch):
    # int(123.9) == 123: the stored hash is kept, and the published row carries
    # the int the service's mtime validation demands. The flip side is inherent:
    # an edit within the same second keeps a stale hash.
    base, handler = stub
    result = link_result(tmp_path)
    src = Path(result.op.src)
    os.utime(src, (123.9, 123.9))
    handler.games = [
        {
            "platform": "n64",
            "id": "usa.zelda",
            "files": [{"name": "usa.zelda.z64", "path": str(src), "size_bytes": 4, "mtime": 123, "sha256": "f" * 64}],
        }
    ]

    def never(*args, **kwargs):
        raise AssertionError("a same-second mtime must not trigger a rehash")

    monkeypatch.setattr("gotg.indexer.publish.ex.sha256_file", never)
    Publisher(CatalogAPI(base, "t")).publish(result)
    member = handler.puts[0][1]["files"][0]
    assert member["sha256"] == "f" * 64
    assert member["mtime"] == 123


def test_an_unhashed_row_is_rehashed_when_hashing_returns(stub, tmp_path):
    # --allow-unhashed on run one must not become never-hashed: a stored null
    # sha must not satisfy the skip.
    base, handler = stub
    result = link_result(tmp_path)
    src = Path(result.op.src)
    st = src.stat()
    handler.games = [
        {
            "platform": "n64",
            "id": "usa.zelda",
            "files": [
                {
                    "name": "usa.zelda.z64",
                    "path": str(src),
                    "size_bytes": st.st_size,
                    "mtime": int(st.st_mtime),
                    "sha256": None,
                }
            ],
        }
    ]
    Publisher(CatalogAPI(base, "t")).publish(result)
    assert handler.puts[0][1]["files"][0]["sha256"] == hashlib.sha256(b"rom!").hexdigest()


def test_diff_of_an_empty_manifest_against_an_empty_catalog_is_clean(stub):
    from gotg.indexer.publish import diff_catalog

    base, _ = stub
    assert diff_catalog({}, CatalogAPI(base, "t")) == []


def test_an_empty_manifest_reports_every_catalog_row(stub):
    from gotg.indexer.publish import diff_catalog

    base, handler = stub
    handler.games = [
        {
            "platform": "n64",
            "id": "usa.zelda",
            "handler": "single_file",
            "files": [{"name": "usa.zelda.z64", "size_bytes": 4, "sha256": "a" * 64, "path": "/t/z.z64", "mtime": 1}],
        },
    ]
    assert diff_catalog({}, CatalogAPI(base, "t")) == ["in catalog but not the manifest: n64/usa.zelda"]


def test_an_empty_catalog_reports_every_manifest_entry(stub):
    from gotg.indexer.manifest import Entry
    from gotg.indexer.publish import diff_catalog

    base, _ = stub
    manifest = {
        "/Games/n64/usa.zelda.z64": Entry("n64", "/Games/n64/usa.zelda.z64", "file", 4, "a" * 64, "Zelda"),
    }
    assert diff_catalog(manifest, CatalogAPI(base, "t")) == ["missing from catalog: n64/usa.zelda"]


def test_a_link_entry_with_extra_members_is_flagged(stub):
    from gotg.indexer.manifest import Entry
    from gotg.indexer.publish import diff_catalog

    base, handler = stub
    handler.games = [
        {
            "platform": "n64",
            "id": "usa.zelda",
            "handler": "single_file",
            "files": [
                {"name": "usa.zelda.z64", "size_bytes": 4, "sha256": "a" * 64, "path": "/t/z.z64", "mtime": 1},
                {"name": "extra.bin", "size_bytes": 1, "sha256": None, "path": "/t/e.bin", "mtime": 1},
            ],
        },
    ]
    manifest = {
        "/Games/n64/usa.zelda.z64": Entry("n64", "/Games/n64/usa.zelda.z64", "file", 4, "a" * 64, "Zelda"),
    }
    assert diff_catalog(manifest, CatalogAPI(base, "t")) == ["link entry with 2 members: n64/usa.zelda"]


def test_the_games_root_pass_covers_what_no_longer_seeds(stub, tmp_path):
    from gotg.indexer.config import load as load_config
    from gotg.indexer.manifest import Entry
    from gotg.indexer.publish import publish_games_root

    base, handler = stub
    games = tmp_path / "Games"
    (games / "n64").mkdir(parents=True)
    (games / "n64" / "usa.pruned.z64").write_bytes(b"still here")
    cfg = load_config(
        {"GAMES_ROOT": str(games), "SOURCE_ROOT": str(tmp_path / "T"), "STATE_DIR": str(tmp_path / "s")},
        require_qbit=False,
    )
    entries = {
        "/Games/n64/usa.pruned.z64": Entry("n64", "/Games/n64/usa.pruned.z64", "file", 10, "a" * 64, "Pruned"),
        "/Games/n64/usa.seeding.z64": Entry("n64", "/Games/n64/usa.seeding.z64", "file", 4, "b" * 64, "Live"),
    }
    publisher = Publisher(CatalogAPI(base, "t"))
    publisher.published_this_run.add(("n64", "usa.seeding"))  # the torrent pass got this one

    published, errors = publish_games_root(publisher, entries, cfg)

    assert (published, errors) == (1, 0)
    path, payload = handler.puts[0]
    assert path == "/catalog/n64/usa.pruned"
    assert payload["handler"] == "single_file"
    assert payload["files"][0]["sha256"] == "a" * 64
    assert payload["files"][0]["path"].endswith("Games/n64/usa.pruned.z64")


def test_a_games_root_entry_missing_on_disk_is_an_error_not_a_crash(stub, tmp_path):
    from gotg.indexer.config import load as load_config
    from gotg.indexer.manifest import Entry
    from gotg.indexer.publish import publish_games_root

    base, _ = stub
    (tmp_path / "Games").mkdir()
    cfg = load_config(
        {"GAMES_ROOT": str(tmp_path / "Games"), "SOURCE_ROOT": str(tmp_path / "T"), "STATE_DIR": str(tmp_path / "s")},
        require_qbit=False,
    )
    entries = {"/Games/n64/usa.gone.z64": Entry("n64", "/Games/n64/usa.gone.z64", "file", 4, "a" * 64, "Gone")}
    published, errors = publish_games_root(Publisher(CatalogAPI(base, "t")), entries, cfg)
    assert (published, errors) == (0, 1)


# --- updates and DLC ride on the base entry -----------------------------------


def attach_result(tmp_path, *, version="1.4.3", role="update", entry_id="world.zelda"):
    release = tmp_path / "Torrents" / f"Zelda_Update_v{version}_NSW-GRP"
    release.mkdir(parents=True, exist_ok=True)
    for name in ["g.rar", "g.r00", "g.sfv"]:
        (release / name).write_bytes(name.encode())
    op = Op(ACTION_ATTACH, "switch", str(release), "", entry_id, role=role, version=version, handler="scene_archive")
    return Result(op, STATUS_DONE)


def base_row(tmp_path, entry_id="world.zelda", extras=()):
    archive = tmp_path / "Torrents" / "Zelda (World).7z"
    archive.parent.mkdir(exist_ok=True)
    archive.write_bytes(b"base")
    files = [{"name": "Zelda (World).7z", "path": str(archive), "size_bytes": 4, "mtime": 1, "sha256": "a" * 64}]
    for name, path in extras:
        files.append({"name": name, "path": str(path), "size_bytes": 1, "mtime": 1, "sha256": "b" * 64})
    return {"platform": "switch", "id": entry_id, "handler": "single_archive", "title": "Zelda", "files": files}


def test_an_update_lists_its_members_under_the_extras_prefix(tmp_path):
    result = attach_result(tmp_path)
    assert [m.name for m in _members(result.op)] == [
        "extras/update_1.4.3/g.r00",
        "extras/update_1.4.3/g.rar",
        "extras/update_1.4.3/g.sfv",
    ]


def test_an_update_attaches_to_the_stored_base_entry(tmp_path, stub):
    url, handler = stub
    handler.games.append(base_row(tmp_path))
    publisher = Publisher(CatalogAPI(url, "t"))

    publisher.publish(attach_result(tmp_path))

    path, payload = handler.puts[-1]
    assert path == "/catalog/switch/world.zelda"
    assert payload["handler"] == "single_archive" and payload["title"] == "Zelda"
    assert [f["name"] for f in payload["files"]] == [
        "Zelda (World).7z",
        "extras/update_1.4.3/g.r00",
        "extras/update_1.4.3/g.rar",
        "extras/update_1.4.3/g.sfv",
    ]
    assert all(f["sha256"] for f in payload["files"])


def test_an_update_without_a_base_is_a_publish_error(tmp_path, stub):
    url, handler = stub
    publisher = Publisher(CatalogAPI(url, "t"))

    with pytest.raises(PublishError, match="no base game"):
        publisher.publish(attach_result(tmp_path))
    assert handler.puts == []


def test_a_base_republished_keeps_the_extras_it_had(tmp_path, stub):
    url, handler = stub
    kept = tmp_path / "Torrents" / "dlc.nsp"
    kept.parent.mkdir(exist_ok=True)
    kept.write_bytes(b"dlc")
    gone = tmp_path / "Torrents" / "vanished.nsp"
    handler.games.append(base_row(tmp_path, extras=[("extras/dlc_pack/dlc.nsp", kept), ("extras/dlc_old/x.nsp", gone)]))
    publisher = Publisher(CatalogAPI(url, "t"))

    archive = tmp_path / "Torrents" / "Zelda (World).7z"
    op = Op(
        ACTION_EXTRACT,
        "switch",
        str(archive),
        "/g/switch/world.zelda.xci",
        "world.zelda",
        title="Zelda",
        handler="single_archive",
    )
    publisher.publish(Result(op, STATUS_DONE))

    _, payload = handler.puts[-1]
    assert [f["name"] for f in payload["files"]] == ["Zelda (World).7z", "extras/dlc_pack/dlc.nsp"]


def test_a_newer_release_of_the_same_update_replaces_it(tmp_path, stub):
    url, handler = stub
    old = tmp_path / "Torrents" / "old.rar"
    old.parent.mkdir(exist_ok=True)
    old.write_bytes(b"old")
    handler.games.append(base_row(tmp_path, extras=[("extras/update_1.4.3/old.rar", old)]))
    publisher = Publisher(CatalogAPI(url, "t"))

    publisher.publish(attach_result(tmp_path))

    _, payload = handler.puts[-1]
    names = [f["name"] for f in payload["files"]]
    assert "extras/update_1.4.3/old.rar" not in names
    assert "extras/update_1.4.3/g.rar" in names


def test_an_update_sees_a_base_published_earlier_in_the_run(tmp_path, stub):
    url, handler = stub
    publisher = Publisher(CatalogAPI(url, "t"))
    archive = tmp_path / "Torrents" / "Zelda (World).7z"
    archive.parent.mkdir(exist_ok=True)
    archive.write_bytes(b"base")
    op = Op(
        ACTION_EXTRACT,
        "switch",
        str(archive),
        "/g/switch/world.zelda.xci",
        "world.zelda",
        title="Zelda",
        handler="single_archive",
    )
    publisher.publish(Result(op, STATUS_DONE))

    publisher.publish(attach_result(tmp_path))

    assert len(handler.puts) == 2
    assert [f["name"] for f in handler.puts[-1][1]["files"]][:2] == ["Zelda (World).7z", "extras/update_1.4.3/g.r00"]


def test_an_update_and_a_dlc_coexist_under_different_extras_slots(tmp_path, stub):
    url, handler = stub
    handler.games.append(base_row(tmp_path))
    publisher = Publisher(CatalogAPI(url, "t"))
    publisher.publish(attach_result(tmp_path, role="update", version="1.4.3"))

    dlc_release = tmp_path / "Torrents" / "Zelda_DLC_Pack"
    dlc_release.mkdir()
    (dlc_release / "pack.nsp").write_bytes(b"dlc")
    dlc_op = Op(ACTION_ATTACH, "switch", str(dlc_release), "", "world.zelda", role="dlc", handler="scene_archive")
    publisher.publish(Result(dlc_op, STATUS_DONE))

    _, payload = handler.puts[-1]
    names = [f["name"] for f in payload["files"]]
    assert names == [
        "Zelda (World).7z",
        "extras/update_1.4.3/g.r00",
        "extras/update_1.4.3/g.rar",
        "extras/update_1.4.3/g.sfv",
        "extras/dlc_zelda_dlc_pack/pack.nsp",
    ]


# --- what a run costs when nothing changed -----------------------------------


def stored_row(result, digest):
    """The catalog's view of an entry the importer already published."""
    op = result.op
    member = _members(op)[0]
    stat = member.path.stat()
    return {
        "platform": op.platform,
        "id": op.entry_id,
        "handler": op.handler,
        "title": op.title,
        "files": [
            {
                "name": member.name,
                "path": str(member.path),
                "size_bytes": stat.st_size,
                "mtime": int(stat.st_mtime),
                "sha256": digest,
            }
        ],
    }


def test_an_entry_identical_to_the_catalog_is_not_written_again(stub, tmp_path):
    # The common case by a mile: most of the library is hardlinks nobody has
    # touched since the day they were made, and re-PUTting each one to advance
    # a timestamp was most of an import's wall clock.
    url, handler = stub
    result = link_result(tmp_path)
    digest = hashlib.sha256(b"rom!").hexdigest()
    handler.games = [stored_row(result, digest)]

    publisher = Publisher(CatalogAPI(url, "token"))
    publisher.publish(result)

    assert handler.puts == [], "nothing changed, so nothing was written"
    assert ("n64", "usa.zelda") in publisher.unchanged, "but it was still seen"


def test_a_changed_entry_is_written(stub, tmp_path):
    url, handler = stub
    result = link_result(tmp_path)
    row = stored_row(result, hashlib.sha256(b"rom!").hexdigest())
    row["files"][0]["size_bytes"] = 999  # the bytes moved under us
    handler.games = [row]

    publisher = Publisher(CatalogAPI(url, "token"))
    publisher.publish(result)

    assert [path for path, _ in handler.puts] == ["/catalog/n64/usa.zelda"]
    assert publisher.unchanged == set()


def test_everything_unchanged_is_marked_seen_in_one_request(stub):
    # Or the sweep, which reads seen_at, reports the whole library as vanished.
    url, handler = stub
    publisher = Publisher(CatalogAPI(url, "token"))
    publisher.unchanged = {("n64", "usa.zelda"), ("gb", "usa.tetris")}

    assert publisher.touch() == 2
    assert handler.sweeps == [{"games": ["gb/usa.tetris", "n64/usa.zelda"]}]
    assert publisher.unchanged == set(), "said once, not again next time"
