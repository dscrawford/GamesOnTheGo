"""The games catalog, at the store and over the wire.

What matters most: only the index token writes, a same-id-different-source
upsert is a 409 rather than a silent replacement, the sweep reports and never
deletes, and no row can name a path outside the library roots — because a row
is a grant to read that path.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from test_proxy import free_port

import gotg.catalog as catalog_module
from gotg.catalog import CatalogStore, Conflict, SweepRefused
from gotg.service.app import Config, make_server

CLIENT = "client-token"
INDEX = "index-token"


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "Torrents"
    root.mkdir()
    return root


@pytest.fixture
def catalog(tmp_path, library):
    return CatalogStore(db=tmp_path / "state" / "catalog.db", roots=[library])


@pytest.fixture
def service(catalog):
    config = Config(token=CLIENT, index_token=INDEX)
    server = make_server("127.0.0.1", free_port(), config, None, catalog)
    threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def call(url, *, method="GET", token: str | None = CLIENT, body=None):
    data = json.dumps(body).encode() if isinstance(body, dict) else body
    request = urllib.request.Request(url, data=data, method=method)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def entry(library, name="usa.zelda.z64", **overrides):
    payload = {
        "handler": "single_file",
        "title": "Zelda",
        "files": [
            {
                "name": name,
                "path": str(library / "some-release" / name),
                "size_bytes": 4,
                "mtime": 1700000000,
                "sha256": "a" * 64,
            }
        ],
    }
    payload.update(overrides)
    return payload


# --- the store ---------------------------------------------------------------


def test_upsert_and_views(catalog, library):
    catalog.upsert("n64", "usa.zelda", entry(library))

    client = catalog.view()
    assert client["version"] == 2
    game = client["games"][0]
    assert game["id"] == "usa.zelda"
    assert game["handler"] == "single_file"
    assert "path" not in game["files"][0], "the client view never carries server paths"

    full = catalog.view(full=True)
    assert full["games"][0]["files"][0]["path"].endswith("usa.zelda.z64")
    assert full["games"][0]["files"][0]["mtime"] == 1700000000


def test_same_source_upsert_is_idempotent_and_bumps_seen(catalog, library):
    first = catalog.upsert("n64", "usa.zelda", entry(library))
    again = catalog.upsert("n64", "usa.zelda", entry(library))
    assert again["imported_at"] == first["imported_at"]
    assert again["seen_at"] >= first["seen_at"]


def test_same_id_different_source_is_a_conflict(catalog, library):
    catalog.upsert("n64", "usa.zelda", entry(library))
    other = entry(library)
    other["files"][0]["path"] = str(library / "other-release" / "usa.zelda.z64")
    with pytest.raises(Conflict) as caught:
        catalog.upsert("n64", "usa.zelda", other)
    assert caught.value.stored["id"] == "usa.zelda"
    forced = catalog.upsert("n64", "usa.zelda", other, force=True)
    assert forced["files"][0]["path"].endswith("other-release/usa.zelda.z64")


def test_a_path_outside_every_root_is_refused(catalog, tmp_path):
    bad = entry(tmp_path)  # tmp_path itself is not a root
    bad["files"][0]["path"] = str(tmp_path / "escape.z64")
    with pytest.raises(ValueError, match="outside every library root"):
        catalog.upsert("n64", "usa.zelda", bad)


def test_a_traversal_inside_a_root_is_refused(catalog, library):
    sneaky = entry(library)
    sneaky["files"][0]["path"] = str(library) + "/../saves/secret"
    with pytest.raises(ValueError, match="outside every library root"):
        catalog.upsert("n64", "usa.zelda", sneaky)


@pytest.mark.parametrize(
    ("platform", "game_id", "mutate"),
    [
        ("N64", "usa.zelda", {}),
        ("n64!", "usa.zelda", {}),
        ("", "usa.zelda", {}),
        ("n" * 17, "usa.zelda", {}),
        ("n64", "prod.keys!", {}),
        ("n64", "us.zelda", {}),
        ("n64", "usa.", {}),
        ("n64", "usa.Zelda", {}),
        ("n64", "usa.zelda", {"handler": "mystery"}),
        ("n64", "usa.zelda", {"handler": ""}),
        ("n64", "usa.zelda", {"handler": None}),
        ("n64", "usa.zelda", {"files": []}),
        ("n64", "usa.zelda", {"files": "not-a-list"}),
        ("n64", "usa.zelda", {"files": [None]}),
        ("n64", "usa.zelda", {"title": " "}),
        ("n64", "usa.zelda", {"title": 7}),
        ("n64", "usa.zelda", {"title": "two\nlines"}),
    ],
)
def test_shapes_are_validated(catalog, library, platform, game_id, mutate):
    with pytest.raises(ValueError):
        catalog.upsert(platform, game_id, entry(library, **mutate))


def test_a_non_object_entry_is_refused(catalog):
    for payload in [None, [], "entry", 7]:
        with pytest.raises(ValueError, match="JSON object"):
            catalog.upsert("n64", "usa.zelda", payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "a/../b.z64"),
        ("name", "a//b.z64"),
        ("name", "a/.hidden/b.z64"),
        ("name", "a/b/c/d/e/f/g/h/i.z64"),
        ("name", ".."),
        ("name", ".hidden"),
        ("name", ""),
        ("name", "a\tb"),
        ("name", "a" * 256),
        ("name", "/".join(["a" * 255] * 3 + ["a" * 254, "aa"])),
        ("name", "a/"),
        ("name", "/a"),
        ("name", None),
        ("path", ""),
        ("path", "relative/path"),
        ("path", "C:\\Games\\zelda.z64"),
        ("path", None),
        ("size_bytes", -1),
        ("size_bytes", "4"),
        ("size_bytes", None),
        ("size_bytes", 4.0),
        ("size_bytes", True),
        ("size_bytes", 2**63),
        ("mtime", -1),
        ("mtime", None),
        ("mtime", False),
        ("sha256", "A" * 64),
        ("sha256", "a" * 63),
        ("sha256", "g" * 64),
        ("sha256", 42),
    ],
)
def test_file_members_are_validated(catalog, library, field, value):
    payload = entry(library)
    payload["files"][0][field] = value
    with pytest.raises(ValueError):
        catalog.upsert("n64", "usa.zelda", payload)


@pytest.mark.parametrize(
    "name",
    [
        "a" * 255,
        "a/b/c/d/e/f/g/h",
        "/".join(["a" * 255] * 3 + ["a" * 254, "a"]),
    ],
    ids=["segment-255", "depth-8", "total-1024"],
)
def test_names_at_the_limits_are_inside_the_boundary(catalog, library, name):
    catalog.upsert("n64", "usa.zelda", entry(library, name=name))
    assert catalog.view()["games"][0]["files"][0]["name"] == name


def test_the_db_may_not_live_inside_a_root(library):
    with pytest.raises(ValueError, match="inside library root"):
        CatalogStore(db=library / "catalog.db", roots=[library])


def test_multi_file_entries_round_trip(catalog, library):
    volumes = {
        "handler": "scene_archive",
        "title": "Luigis Mansion 2 HD",
        "files": [
            {
                "name": f"hr-banra.r{i:02d}",
                "path": str(library / "rel" / f"hr-banra.r{i:02d}"),
                "size_bytes": 100,
                "mtime": 1,
                "sha256": None,
            }
            for i in range(3)
        ],
    }
    catalog.upsert("switch", "world.luigis_mansion_2_hd", volumes)
    game = catalog.view()["games"][0]
    assert [f["name"] for f in game["files"]] == ["hr-banra.r00", "hr-banra.r01", "hr-banra.r02"]


def test_sweep_reports_and_never_deletes(catalog, library):
    catalog.upsert("n64", "usa.zelda", entry(library))
    report = catalog.sweep("2099-01-01T00:00:00Z", confirm=True)
    assert [v["id"] for v in report["vanished"]] == ["usa.zelda"]
    assert catalog.view()["games"], "sweep must not delete"


def test_sweep_rail_refuses_a_mass_vanish(catalog, library):
    catalog.upsert("n64", "usa.zelda", entry(library))
    with pytest.raises(SweepRefused):
        catalog.sweep("2099-01-01T00:00:00Z")
    report = catalog.sweep("2000-01-01T00:00:00Z")
    assert report["vanished"] == []


def test_lookup_is_containment_checked(catalog, library):
    catalog.upsert("n64", "usa.zelda", entry(library))
    path = catalog.lookup("n64", "usa.zelda", "usa.zelda.z64")
    assert path is not None and path.name == "usa.zelda.z64"
    assert catalog.lookup("n64", "usa.zelda", "missing.z64") is None
    assert catalog.lookup("n64", "usa.ghost", "usa.zelda.z64") is None
    assert catalog.lookup("n64", "usa.zelda", "../escape") is None


def test_concurrent_reads_during_writes(catalog, library):
    errors = []

    def writer():
        try:
            for i in range(30):
                catalog.upsert("n64", f"usa.game_{i}", entry(library, name=f"usa.game_{i}.z64"))
        except Exception as error:  # noqa: BLE001
            errors.append(error)

    def reader():
        try:
            for _ in range(60):
                catalog.view()
        except Exception as error:  # noqa: BLE001
            errors.append(error)

    threads = [threading.Thread(target=writer)] + [threading.Thread(target=reader) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert len(catalog.view()["games"]) == 30


# --- over the wire -----------------------------------------------------------


def test_reads_for_clients_writes_for_the_index(service, library):
    status, _ = call(f"{service}/catalog/n64/usa.zelda", method="PUT", token=INDEX, body=entry(library))
    assert status == 200

    status, view = call(f"{service}/catalog")
    assert status == 200
    assert view["games"][0]["id"] == "usa.zelda"

    status, _ = call(f"{service}/catalog/n64/usa.zelda", method="PUT", token=CLIENT, body=entry(library))
    assert status == 403

    status, _ = call(f"{service}/catalog?full=1")
    assert status == 403, "the full view carries paths and is not for clients"
    status, full = call(f"{service}/catalog?full=1", token=INDEX)
    assert status == 200
    assert "path" in full["games"][0]["files"][0]


def test_wire_conflict_carries_the_stored_entry(service, library):
    call(f"{service}/catalog/n64/usa.zelda", method="PUT", token=INDEX, body=entry(library))
    other = entry(library)
    other["files"][0]["path"] = str(library / "other" / "usa.zelda.z64")
    status, body = call(f"{service}/catalog/n64/usa.zelda", method="PUT", token=INDEX, body=other)
    assert status == 409
    assert body["stored"]["id"] == "usa.zelda"
    status, _ = call(f"{service}/catalog/n64/usa.zelda?force=1", method="PUT", token=INDEX, body=other)
    assert status == 200


def test_wire_sweep_and_delete(service, library):
    call(f"{service}/catalog/n64/usa.zelda", method="PUT", token=INDEX, body=entry(library))

    status, _ = call(f"{service}/catalog/sweep", method="POST", token=CLIENT, body={"since": "2099-01-01T00:00:00Z"})
    assert status == 403

    status, report = call(
        f"{service}/catalog/sweep?confirm=1", method="POST", token=INDEX, body={"since": "2099-01-01T00:00:00Z"}
    )
    assert status == 200
    assert report["vanished"]

    status, _ = call(f"{service}/catalog/n64/usa.zelda", method="DELETE", token=CLIENT)
    assert status == 403
    status, _ = call(f"{service}/catalog/n64/usa.zelda", method="DELETE", token=INDEX)
    assert status == 200
    status, _ = call(f"{service}/catalog/n64/usa.zelda", method="DELETE", token=INDEX)
    assert status == 404


def test_no_token_is_a_401_with_no_route_shape_leaked(service):
    status, _ = call(f"{service}/catalog", token=None)
    assert status == 401


def test_a_service_without_a_catalog_says_so():
    config = Config(token=CLIENT, index_token=INDEX)
    server = make_server("127.0.0.1", free_port(), config)
    threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True).start()
    try:
        status, _ = call(f"http://127.0.0.1:{server.server_port}/catalog")
        assert status == 503
    finally:
        server.shutdown()


def test_matching_tokens_refuse_to_start():
    with pytest.raises(ValueError, match="index token equals the client token"):
        Config(token="same", index_token="same").validate()


def test_the_catalog_names_the_byte_host_when_one_is_configured(catalog):
    # files_url is how a deployment keeps /games off a proxied control plane;
    # clients read it from the catalog rather than being configured.
    config = Config(token=CLIENT, index_token=INDEX, files_url="https://files.example")
    server = make_server("127.0.0.1", free_port(), config, None, catalog)
    threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True).start()
    try:
        status, view = call(f"http://127.0.0.1:{server.server_port}/catalog")
        assert status == 200
        assert view["files_url"] == "https://files.example"
    finally:
        server.shutdown()


def test_no_files_url_configured_means_none_in_the_catalog(service):
    status, view = call(f"{service}/catalog")
    assert status == 200
    assert "files_url" not in view


def test_a_files_url_that_is_not_a_url_refuses_to_start():
    with pytest.raises(ValueError, match="GOTG_FILES_URL"):
        Config(token=CLIENT, files_url="files.example").validate()


def _serve(config, catalog):
    server = make_server("127.0.0.1", free_port(), config, None, catalog)
    threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True).start()
    return server


@pytest.mark.parametrize(
    ("content", "expect"),
    [
        ("41234", "https://files.example:41234"),
        ("41234\n", "https://files.example:41234"),
        (None, None),  # no file: the tunnel is down
        ("", None),
        ("not-a-port", None),
        ("0", None),
        ("70000", None),
    ],
    ids=["port", "newline", "absent", "empty", "junk", "zero", "oversized"],
)
def test_a_port_file_rides_the_files_url_and_a_bad_one_withholds_it(catalog, tmp_path, content, expect):
    # A VPN-fronted byte host has no fixed port; advertising one the tunnel
    # cannot answer would turn every download into a hang.
    port_file = tmp_path / "forwarded_port"
    if content is not None:
        port_file.write_text(content)
    config = Config(token=CLIENT, files_url="https://files.example", files_port_file=str(port_file))
    server = _serve(config, catalog)
    try:
        status, view = call(f"http://127.0.0.1:{server.server_port}/catalog")
        assert status == 200
        assert view.get("files_url") == expect
    finally:
        server.shutdown()


def test_the_port_is_read_per_request_so_a_reconnect_is_picked_up(catalog, tmp_path):
    port_file = tmp_path / "forwarded_port"
    port_file.write_text("41234")
    config = Config(token=CLIENT, files_url="https://files.example", files_port_file=str(port_file))
    server = _serve(config, catalog)
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        assert call(f"{base}/catalog")[1]["files_url"] == "https://files.example:41234"
        port_file.write_text("50001")
        assert call(f"{base}/catalog")[1]["files_url"] == "https://files.example:50001"
    finally:
        server.shutdown()


def test_an_unset_index_token_makes_the_catalog_read_only(catalog, library):
    config = Config(token=CLIENT)
    server = make_server("127.0.0.1", free_port(), config, None, catalog)
    threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        status, _ = call(f"{base}/catalog/n64/usa.zelda", method="PUT", token=CLIENT, body=entry(library))
        assert status == 503, "writes must never fall back to the client token"
        status, _ = call(f"{base}/catalog")
        assert status == 200
    finally:
        server.shutdown()


# --- atomicity and consistency ------------------------------------------------


def test_a_failing_upsert_leaves_the_stored_entry_intact(catalog, library):
    catalog.upsert("n64", "usa.zelda", entry(library))
    bad = entry(library)
    bad["files"].append(
        {
            "name": "usa.extra",
            "path": str(library / "some-release" / "usa.extra"),
            "size_bytes": 2**64,
            "mtime": 1,
            "sha256": None,
        }
    )
    with pytest.raises((OverflowError, ValueError)):
        catalog.upsert("n64", "usa.zelda", bad, force=True)
    stored = catalog.view(full=True)["games"][0]
    assert [f["name"] for f in stored["files"]] == ["usa.zelda.z64"]
    assert stored["title"] == "Zelda"


def test_view_never_carries_nulls(catalog, library):
    for i in range(5):
        catalog.upsert("n64", f"usa.game_{i}", entry(library, name=f"usa.game_{i}.z64"))
    games = catalog.view()["games"]
    assert None not in games
    assert all(g["files"] for g in games)


def test_a_reader_opened_before_the_first_write_sees_later_writes(catalog, library):
    ready, proceed = threading.Event(), threading.Event()
    seen = {}

    def reader():
        seen["before"] = len(catalog.view()["games"])
        ready.set()
        proceed.wait(timeout=10)
        seen["after"] = len(catalog.view()["games"])

    thread = threading.Thread(target=reader)
    thread.start()
    assert ready.wait(timeout=10)
    catalog.upsert("n64", "usa.zelda", entry(library))
    proceed.set()
    thread.join(timeout=10)
    assert seen == {"before": 0, "after": 1}


def test_sweep_during_concurrent_upserts(catalog, library):
    errors = []

    def writer():
        try:
            for i in range(30):
                catalog.upsert("n64", f"usa.game_{i}", entry(library, name=f"usa.game_{i}.z64"))
        except Exception as error:  # noqa: BLE001
            errors.append(error)

    def sweeper():
        try:
            for _ in range(60):
                assert catalog.sweep("2000-01-01T00:00:00Z")["vanished"] == []
                catalog.sweep("2099-01-01T00:00:00Z", confirm=True)
        except Exception as error:  # noqa: BLE001
            errors.append(error)

    threads = [threading.Thread(target=writer)] + [threading.Thread(target=sweeper) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors


def test_delete_cascades_to_files_and_frees_the_id(catalog, library):
    catalog.upsert("n64", "usa.zelda", entry(library))
    assert catalog.delete("n64", "usa.zelda") is True
    assert catalog.lookup("n64", "usa.zelda", "usa.zelda.z64") is None, (
        "an orphaned entry_file row would still grant a path"
    )
    other = entry(library)
    other["files"][0]["path"] = str(library / "other-release" / "usa.zelda.z64")
    catalog.upsert("n64", "usa.zelda", other)


def test_same_paths_with_a_new_hash_is_an_update_not_a_conflict(catalog, library):
    catalog.upsert("n64", "usa.zelda", entry(library))
    rehashed = entry(library)
    rehashed["files"][0]["sha256"] = "b" * 64
    assert catalog.upsert("n64", "usa.zelda", rehashed)["files"][0]["sha256"] == "b" * 64


# --- the sweep rail and its clock --------------------------------------------


@pytest.mark.parametrize(
    "since",
    [
        "",
        "2099-01-01",
        "2099-01-01 00:00:00Z",
        "2099-01-01T00:00:00",
        "2099-01-01T00:00:00+00:00",
        "not-a-date",
        "٢٠٩٩-٠١-٠١T٠٠:٠٠:٠٠Z",
    ],
)
def test_sweep_since_must_be_ascii_iso_utc(catalog, since):
    with pytest.raises(ValueError):
        catalog.sweep(since, confirm=True)


def test_the_sweep_rail_boundary_is_one_in_five(catalog, library, monkeypatch):
    monkeypatch.setattr(catalog_module, "_now", lambda: "2000-01-01T00:00:00Z")
    for i in range(2):
        catalog.upsert("n64", f"usa.stale_{i}", entry(library, name=f"usa.stale_{i}.z64"))
    monkeypatch.setattr(catalog_module, "_now", lambda: "2020-01-01T00:00:00Z")
    for i in range(8):
        catalog.upsert("n64", f"usa.fresh_{i}", entry(library, name=f"usa.fresh_{i}.z64"))

    report = catalog.sweep("2010-01-01T00:00:00Z")
    assert len(report["vanished"]) == 2

    monkeypatch.setattr(catalog_module, "_now", lambda: "2000-01-01T00:00:00Z")
    catalog.upsert("n64", "usa.stale_2", entry(library, name="usa.stale_2.z64"))
    with pytest.raises(SweepRefused):
        catalog.sweep("2010-01-01T00:00:00Z")


# --- hostile strings ----------------------------------------------------------


@pytest.mark.parametrize(
    "hostile",
    [
        "n64'; DROP TABLE entry;--",
        'n64" OR "1"="1',
        "n64\x00",
        "n64 UNION SELECT path FROM entry_file",
    ],
)
def test_sql_metacharacters_are_data_not_syntax(catalog, library, hostile):
    catalog.upsert("n64", "usa.zelda", entry(library))
    assert catalog.delete(hostile, "usa.zelda") is False
    assert catalog.lookup(hostile, "usa.zelda", "usa.zelda.z64") is None
    assert len(catalog.view()["games"]) == 1


@pytest.mark.parametrize(
    "title",
    ["Zelda's Adventure — 時のオカリナ 🎮", 'He said "run"', "Robert'); DROP TABLE entry;--"],
)
def test_titles_round_trip_verbatim(catalog, library, title):
    catalog.upsert("n64", "usa.zelda", entry(library, title=title))
    assert catalog.view()["games"][0]["title"] == title


def test_a_symlink_inside_the_root_cannot_reach_outside(catalog, tmp_path, library):
    outside = tmp_path / "outside"
    outside.mkdir()
    (library / "escape").symlink_to(outside)
    sneaky = entry(library)
    sneaky["files"][0]["path"] = str(library / "escape" / "usa.zelda.z64")
    with pytest.raises(ValueError, match="outside every library root"):
        catalog.upsert("n64", "usa.zelda", sneaky)


def test_open_member_refuses_a_symlink_swapped_in_after_indexing(catalog, tmp_path, library):
    release = library / "some-release"
    release.mkdir()
    target = release / "usa.zelda.z64"
    target.write_bytes(b"rom!")
    catalog.upsert("n64", "usa.zelda", entry(library))

    found = catalog.open_member("n64", "usa.zelda", "usa.zelda.z64")
    assert found is not None and found[1] is not None
    meta, fd = found
    assert meta["size_bytes"] == 4
    import os

    assert os.read(fd, 4) == b"rom!"
    os.close(fd)

    # The torrent client (or anyone who can write inside a root) swaps the
    # indexed file for a symlink pointing outside. Containment fails on the
    # resolved path, so the member reads as not-found at all — an attack does
    # not even earn the stale-index log a merely deleted file gets.
    secret = tmp_path / "secret"
    secret.write_bytes(b"shh")
    target.unlink()
    target.symlink_to(secret)
    assert catalog.open_member("n64", "usa.zelda", "usa.zelda.z64") is None

    # A deleted file, by contrast, is a stale row: metadata answers, no fd.
    target.unlink()
    found = catalog.open_member("n64", "usa.zelda", "usa.zelda.z64")
    assert found is not None and found[1] is None


# --- over the wire, the awkward bodies ---------------------------------------


@pytest.mark.parametrize("raw", [b"null", b"[]", b'"entry"', b"7", b"{}"])
def test_a_non_object_put_body_is_a_400_not_a_dropped_connection(service, raw):
    status, _ = call(f"{service}/catalog/n64/usa.zelda", method="PUT", token=INDEX, body=raw)
    assert status == 400


@pytest.mark.parametrize("raw", [b"null", b"[]", b'"2099-01-01T00:00:00Z"'])
def test_a_non_object_sweep_body_is_a_400(service, raw):
    status, _ = call(f"{service}/catalog/sweep?confirm=1", method="POST", token=INDEX, body=raw)
    assert status == 400


def test_an_empty_put_body_is_a_400(service):
    status, _ = call(f"{service}/catalog/n64/usa.zelda", method="PUT", token=INDEX)
    assert status == 400


def test_a_wiiu_sized_entry_fits_the_catalog_cap(service, library):
    fat = entry(library)
    fat["handler"] = "wiiu_decrypted"
    fat["files"] = [
        {
            "name": f"content/{i:05}.pack",
            "path": str(library / "rel" / f"{i:05}.pack"),
            "size_bytes": 1,
            "mtime": 1,
            "sha256": "a" * 64,
        }
        for i in range(5000)
    ]
    status, stored = call(f"{service}/catalog/wiiu/usa.big", method="PUT", token=INDEX, body=fat)
    assert status == 200
    assert len(stored["files"]) == 5000


def test_an_entry_past_even_the_catalog_cap_is_refused(service):
    # The 413 closes the connection before the body is read; a client still
    # mid-upload may see the reset instead of the status. Either is a refusal.
    blob = b"[" + b"x" * (8 * 1024 * 1024) + b"]"
    try:
        status, _ = call(f"{service}/catalog/n64/usa.big", method="PUT", token=INDEX, body=blob)
    except (urllib.error.URLError, ConnectionError):
        return
    assert status == 413


def test_a_nesting_bomb_is_a_400_not_a_crash(service):
    bomb = b"[" * 30000 + b"]" * 30000
    status, _ = call(f"{service}/catalog/n64/usa.zelda", method="PUT", token=INDEX, body=bomb)
    assert status == 400


def test_unicode_titles_survive_the_wire(service, library):
    title = "ゼルダの伝説 時のオカリナ 🎮"
    status, stored = call(
        f"{service}/catalog/n64/usa.zelda", method="PUT", token=INDEX, body=entry(library, title=title)
    )
    assert status == 200 and stored["title"] == title
    status, view = call(f"{service}/catalog")
    assert view["games"][0]["title"] == title


def test_a_non_ascii_bearer_token_earns_a_401_not_a_crash(service):
    status, _ = call(f"{service}/catalog", token="\xff\xfe")
    assert status == 401


def test_percent_encoded_hostility_in_the_path_is_refused(service, library):
    status, _ = call(
        f"{service}/catalog/n64%27%3B%20DROP%20TABLE%20entry/usa.zelda", method="PUT", token=INDEX, body=entry(library)
    )
    assert status == 400


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("PUT", "/catalog/n64"),
        ("PUT", "/catalog/n64/usa.zelda/extra"),
        ("DELETE", "/catalog/n64"),
        ("GET", "/catalog/n64/usa.zelda"),
        ("POST", "/catalog/sweep/extra"),
    ],
)
def test_malformed_catalog_routes_are_404(service, library, method, path):
    body = entry(library) if method in ("PUT", "POST") else None
    status, _ = call(f"{service}{path}", method=method, token=INDEX, body=body)
    assert status == 404


def test_catalog_env_vars_come_as_a_pair(tmp_path):
    from gotg.service.cli import catalog_from_env

    assert catalog_from_env({}) is None
    with pytest.raises(ValueError, match="set together"):
        catalog_from_env({"GOTG_CATALOG_DB": str(tmp_path / "c.db")})
    with pytest.raises(ValueError, match="set together"):
        catalog_from_env({"GOTG_LIBRARY_ROOTS": str(tmp_path)})


def test_nested_member_names_carry_a_wiiu_tree(catalog, library):
    tree = {
        "handler": "wiiu_decrypted",
        "title": "Wind Waker HD",
        "files": [
            {"name": name, "path": str(library / "rel" / name), "size_bytes": 1, "mtime": 1, "sha256": None}
            for name in ["code/app.rpx", "content/scene/x.pack", "meta/meta.xml"]
        ],
    }
    catalog.upsert("wiiu", "usa.wind_waker_hd", tree)
    game = catalog.view()["games"][0]
    assert [f["name"] for f in game["files"]] == [
        "code/app.rpx",
        "content/scene/x.pack",
        "meta/meta.xml",
    ]


# --- the admin scan -----------------------------------------------------------


def real_entry(library, name="usa.zelda.z64", *, present=True, **overrides):
    """An entry whose file is actually on disk, which the plain one is not."""
    payload = entry(library, name=name, **overrides)
    for member in payload["files"]:
        path = Path(member["path"])
        if present:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"rom!")
    return payload


def test_scan_reports_what_arrived_and_never_the_rest(catalog, library, monkeypatch):
    monkeypatch.setattr(catalog_module, "_now", lambda: "2020-01-01T00:00:00Z")
    catalog.upsert("n64", "usa.old", real_entry(library, name="usa.old.z64"))
    monkeypatch.setattr(catalog_module, "_now", lambda: "2026-01-01T00:00:00Z")
    catalog.upsert("gba", "usa.mother_3", real_entry(library, name="usa.mother_3.gba", title="Mother 3"))

    monkeypatch.setattr(catalog_module, "_now", lambda: "2026-01-01T00:00:01Z")
    report = catalog.scan(since="2025-01-01T00:00:00Z")
    assert [(a["platform"], a["id"], a["title"]) for a in report["added"]] == [("gba", "usa.mother_3", "Mother 3")]
    assert report["missing"] == []
    assert report["total"] == 2
    assert report["suspect"] is False


def test_scan_without_a_since_calls_the_whole_catalog_new(catalog, library, monkeypatch):
    monkeypatch.setattr(catalog_module, "_now", lambda: "2026-01-01T00:00:00Z")
    catalog.upsert("n64", "usa.zelda", real_entry(library))
    monkeypatch.setattr(catalog_module, "_now", lambda: "2026-01-01T00:00:01Z")
    report = catalog.scan()
    assert report["since"] is None
    assert len(report["added"]) == 1
    assert report["added"][0]["size_bytes"] == 4


def test_seeing_a_game_again_does_not_report_it_twice(catalog, library, monkeypatch):
    monkeypatch.setattr(catalog_module, "_now", lambda: "2020-01-01T00:00:00Z")
    catalog.upsert("n64", "usa.zelda", real_entry(library))
    monkeypatch.setattr(catalog_module, "_now", lambda: "2026-01-01T00:00:00Z")
    catalog.upsert("n64", "usa.zelda", real_entry(library))  # the indexer's next pass

    monkeypatch.setattr(catalog_module, "_now", lambda: "2026-01-01T00:00:01Z")
    report = catalog.scan(since="2025-01-01T00:00:00Z")
    assert report["added"] == [], "imported_at is when it arrived, not when it was last seen"


def test_successive_scans_report_an_entry_exactly_once(catalog, library, monkeypatch):
    """The window is [since, scanned_at), and the open top is why this holds.

    imported_at is a whole second, so a run that reported everything up to and
    including its own second — and then marked the caller at that second —
    would report that whole second again on the next call. Caught by hand
    before it was caught by a test: a scan taken in the same second as an
    import listed five games and then listed three of them again.
    """
    clock = ["2026-01-01T00:00:00Z"]
    monkeypatch.setattr(catalog_module, "_now", lambda: clock[0])

    seen: list[str] = []
    mark: str | None = None
    for tick, arriving in enumerate([["a", "b"], [], ["c"], ["d", "e"]]):
        clock[0] = f"2026-01-01T00:00:{tick:02}Z"
        for name in arriving:
            catalog.upsert("n64", f"usa.{name}", real_entry(library, name=f"usa.{name}.z64"))
        report = catalog.scan(since=mark)
        seen.extend(a["id"] for a in report["added"])
        mark = report["scanned_at"]

    # Nothing twice, and — one more tick past the last arrival — nothing lost.
    clock[0] = "2026-01-01T00:00:09Z"
    seen.extend(a["id"] for a in catalog.scan(since=mark)["added"])
    assert sorted(seen) == ["usa.a", "usa.b", "usa.c", "usa.d", "usa.e"]
    assert len(seen) == len(set(seen)), f"reported more than once: {seen}"


def test_the_second_a_scan_runs_in_is_left_for_the_next_scan(catalog, library, monkeypatch):
    monkeypatch.setattr(catalog_module, "_now", lambda: "2026-01-01T00:00:00Z")
    catalog.upsert("n64", "usa.zelda", real_entry(library))

    # Same second as the import: deferred, because more of that second may
    # still be arriving behind this read.
    assert catalog.scan()["added"] == []

    monkeypatch.setattr(catalog_module, "_now", lambda: "2026-01-01T00:00:01Z")
    assert [a["id"] for a in catalog.scan(since="2026-01-01T00:00:00Z")["added"]] == ["usa.zelda"]


def test_scan_finds_the_bytes_gone_from_under_a_row(catalog, library):
    payload = real_entry(library)
    catalog.upsert("n64", "usa.zelda", payload)
    assert catalog.scan()["missing"] == []

    Path(payload["files"][0]["path"]).unlink()
    report = catalog.scan()
    assert [(m["id"], m["missing_files"], m["files"]) for m in report["missing"]] == [("usa.zelda", 1, 1)]
    assert report["missing"][0]["missing"] == ["usa.zelda.z64"]
    assert catalog.view()["games"], "a scan reports and never deletes"


def test_a_partly_gone_entry_names_only_the_files_that_went(catalog, library):
    payload = real_entry(library)
    payload["files"].append(
        {
            "name": "usa.zelda.cue",
            "path": str(library / "some-release" / "usa.zelda.cue"),
            "size_bytes": 4,
            "mtime": 1,
            "sha256": None,
        }
    )
    Path(payload["files"][1]["path"]).write_bytes(b"cue!")
    catalog.upsert("n64", "usa.zelda", payload)
    Path(payload["files"][1]["path"]).unlink()

    missing = catalog.scan()["missing"]
    assert missing[0]["missing"] == ["usa.zelda.cue"]
    assert (missing[0]["missing_files"], missing[0]["files"]) == (1, 2)


def test_a_file_symlinked_out_of_the_library_counts_as_gone(catalog, tmp_path, library):
    outside = tmp_path / "elsewhere.z64"
    outside.write_bytes(b"rom!")
    payload = real_entry(library, present=False)
    Path(payload["files"][0]["path"]).parent.mkdir(parents=True, exist_ok=True)
    catalog.upsert("n64", "usa.zelda", payload)
    Path(payload["files"][0]["path"]).symlink_to(outside)

    # The bytes exist; the streaming endpoint still refuses them, so a scan
    # that called this present would report on a file nobody can fetch.
    assert [m["id"] for m in catalog.scan()["missing"]] == ["usa.zelda"]


def test_the_mass_vanish_rail_flags_rather_than_refuses(catalog, library):
    for i in range(10):
        catalog.upsert("n64", f"usa.game_{i}", real_entry(library, name=f"usa.game_{i}.z64"))
    assert catalog.scan()["suspect"] is False

    for i in range(3):
        (library / "some-release" / f"usa.game_{i}.z64").unlink()
    report = catalog.scan()
    assert report["suspect"] is True
    assert len(report["missing"]) == 3, "flagged, and still reported in full"


def test_an_empty_catalog_scans_clean(catalog):
    report = catalog.scan()
    assert (report["total"], report["added"], report["missing"], report["suspect"]) == (0, [], [], False)


@pytest.mark.parametrize("since", ["", "yesterday", "2026-01-01", "٢٠٢٦-٠١-٠١T٠٠:٠٠:٠٠Z"])
def test_scan_since_must_be_ascii_iso_utc(catalog, since):
    with pytest.raises(ValueError):
        catalog.scan(since=since)


# --- the admin scan, over the wire --------------------------------------------

ADMIN = "admin-token"


@pytest.fixture
def admin_service(catalog):
    config = Config(token=CLIENT, index_token=INDEX, admin_token=ADMIN)
    server = make_server("127.0.0.1", free_port(), config, None, catalog)
    threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_the_scan_is_the_admin_token_and_nobody_else(admin_service, catalog, library, monkeypatch):
    monkeypatch.setattr(catalog_module, "_now", lambda: "2026-01-01T00:00:00Z")
    catalog.upsert("n64", "usa.zelda", real_entry(library))
    monkeypatch.setattr(catalog_module, "_now", lambda: "2026-01-01T00:00:01Z")

    # A catalog read is a client's business; a scan of the bytes behind it is
    # not — it costs a stat per member file of every entry in the library.
    for token in (CLIENT, INDEX):
        status, _ = call(f"{admin_service}/admin/scan", token=token)
        assert status == 403, "the scan is no more open than the token routes"
    status, _ = call(f"{admin_service}/admin/scan", token=None)
    assert status == 401

    status, report = call(f"{admin_service}/admin/scan", token=ADMIN)
    assert status == 200
    assert [a["id"] for a in report["added"]] == ["usa.zelda"]


def test_the_wire_scan_windows_on_since(admin_service, catalog, library, monkeypatch):
    monkeypatch.setattr(catalog_module, "_now", lambda: "2020-01-01T00:00:00Z")
    catalog.upsert("n64", "usa.old", real_entry(library, name="usa.old.z64"))
    monkeypatch.setattr(catalog_module, "_now", lambda: "2026-01-01T00:00:00Z")
    catalog.upsert("n64", "usa.new", real_entry(library, name="usa.new.z64"))
    monkeypatch.setattr(catalog_module, "_now", lambda: "2026-01-01T00:00:01Z")

    status, report = call(f"{admin_service}/admin/scan?since=2025-01-01T00:00:00Z", token=ADMIN)
    assert status == 200
    assert [a["id"] for a in report["added"]] == ["usa.new"]
    assert report["since"] == "2025-01-01T00:00:00Z"


def test_a_malformed_since_is_a_400_not_a_whole_catalog_answer(admin_service, catalog, library):
    catalog.upsert("n64", "usa.zelda", real_entry(library))
    status, problem = call(f"{admin_service}/admin/scan?since=yesterday", token=ADMIN)
    assert status == 400
    assert "ISO" in problem["error"]


def test_a_scan_is_a_get(admin_service):
    status, _ = call(f"{admin_service}/admin/scan", method="POST", token=ADMIN, body={})
    assert status == 405


def test_the_scan_needs_no_token_store_but_does_need_a_catalog():
    config = Config(token=CLIENT, admin_token=ADMIN)
    server = make_server("127.0.0.1", free_port(), config, None, None)
    threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        status, _ = call(f"{url}/admin/scan", token=ADMIN)
        assert status == 503
        # …and the token routes still say what they are missing.
        status, _ = call(f"{url}/admin/tokens", token=ADMIN)
        assert status == 503
    finally:
        server.shutdown()


# --- what the review pass found -----------------------------------------------


def test_a_since_with_a_trailing_newline_is_refused(catalog):
    # "$" in a Python regex also matches before a trailing newline, so "…Z\n"
    # passed a shape check that "…z" does not — and then sorts above every real
    # stamp, silently skipping the whole second it names. It arrives that way
    # as ?since=2026-01-01T00%3A00%3A00Z%0A, so it is wire-reachable.
    with pytest.raises(ValueError):
        catalog.scan(since="2026-01-01T00:00:00Z\n")
    with pytest.raises(ValueError):
        catalog.sweep("2026-01-01T00:00:00Z\n", confirm=True)


def test_a_member_replaced_by_a_symlink_is_gone_because_the_endpoint_says_so(catalog, tmp_path, library):
    """Present here has to mean fetchable there, or this misses what it is for.

    open_member opens with O_NOFOLLOW, so a member swapped for a symlink is
    unfetchable even when the target is a real file inside a root. Resolving
    it here reported the library healthy while every download of that game
    failed — a blind spot in the tool built to catch exactly this.
    """
    release = library / "some-release"
    release.mkdir(parents=True, exist_ok=True)
    (release / ".usa.zelda.z64.tmp").write_bytes(b"rom!")
    (release / "usa.zelda.z64").symlink_to(release / ".usa.zelda.z64.tmp")
    catalog.upsert("n64", "usa.zelda", entry(library))

    assert catalog.open_member("n64", "usa.zelda", "usa.zelda.z64")[1] is None
    assert [m["id"] for m in catalog.scan()["missing"]] == ["usa.zelda"]


def test_a_member_that_is_now_a_directory_is_gone(catalog, library):
    (library / "some-release" / "usa.zelda.z64").mkdir(parents=True)
    catalog.upsert("n64", "usa.zelda", entry(library))
    assert [m["id"] for m in catalog.scan()["missing"]] == ["usa.zelda"]


def test_a_window_that_ends_before_it_begins_says_the_clock_moved(catalog, library, monkeypatch):
    """The one way a game is reported zero times, made loud instead of silent.

    ntp steps the host back; the indexer imports during the replayed stretch;
    that entry is below the old mark and below every mark after it. The scan
    cannot recover it — only a re-run with an explicit --since can — so the
    least it can do is not read like a quiet library.
    """
    monkeypatch.setattr(catalog_module, "_now", lambda: "2026-01-01T00:05:00Z")
    catalog.upsert("n64", "usa.zelda", real_entry(library))
    monkeypatch.setattr(catalog_module, "_now", lambda: "2026-01-01T00:05:01Z")

    stepped = catalog.scan(since="2026-01-01T00:10:00Z")
    assert stepped["added"] == []
    assert stepped["clock_stepped_back"] is True

    ordinary = catalog.scan(since="2026-01-01T00:00:00Z")
    assert ordinary["clock_stepped_back"] is False
    assert catalog.scan()["clock_stepped_back"] is False, "no since is not a stepped clock"


@pytest.mark.parametrize(
    ("total", "gone", "suspect"),
    [(1, 0, False), (1, 1, True), (5, 1, False), (5, 2, True), (10, 2, False), (10, 3, True), (100, 20, False)],
)
def test_the_scan_rail_is_strictly_more_than_a_fifth(catalog, library, total, gone, suspect):
    for i in range(total):
        catalog.upsert("n64", f"usa.game_{i:03}", real_entry(library, name=f"usa.game_{i:03}.z64"))
    for i in range(gone):
        (library / "some-release" / f"usa.game_{i:03}.z64").unlink()

    report = catalog.scan()
    assert report["suspect"] is suspect
    assert len(report["missing"]) == gone, "flagged or not, the list is the same list"


def test_only_the_members_that_went_are_named_in_a_wiiu_tree(catalog, library):
    # The parent-directory memo in _present is per-scan and shared across a
    # tree's members; a bug in it would show up as a whole tree reading gone.
    names = ["meta/meta.xml", "code/app.rpx", "content/scene/x.pack"]
    files = []
    for name in names:
        path = library / "rel" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        files.append({"name": name, "path": str(path), "size_bytes": 1, "mtime": 1, "sha256": None})
    catalog.upsert("wiiu", "usa.wind_waker_hd", {"handler": "wiiu_decrypted", "title": "WW", "files": files})
    assert catalog.scan()["missing"] == []

    (library / "rel" / "code" / "app.rpx").unlink()
    gone = catalog.scan()["missing"][0]
    assert gone["missing"] == ["code/app.rpx"]
    assert (gone["missing_files"], gone["files"]) == (1, 3)


def test_a_game_that_arrived_and_vanished_in_one_window_is_in_both_halves(catalog, library, monkeypatch):
    monkeypatch.setattr(catalog_module, "_now", lambda: "2026-01-01T00:00:00Z")
    catalog.upsert("n64", "usa.zelda", real_entry(library))
    payload = real_entry(library, name="usa.ghost.z64", title="Ghost")
    catalog.upsert("ps2", "usa.ghost", payload)
    Path(payload["files"][0]["path"]).unlink()

    monkeypatch.setattr(catalog_module, "_now", lambda: "2026-01-01T00:00:01Z")
    report = catalog.scan(since="2025-01-01T00:00:00Z")
    assert [a["id"] for a in report["added"]] == ["usa.zelda", "usa.ghost"]
    assert [m["id"] for m in report["missing"]] == ["usa.ghost"]


def test_scan_during_concurrent_upserts(catalog, library):
    # The sweep's neighbour. A row landing between the entry read and the file
    # read must never read as a game that went away: the bytes are written
    # before the row is, so a false vanish here is a false alarm about a
    # library that is fine.
    errors: list[Exception] = []
    false_alarms: list[list[str]] = []

    def writer():
        try:
            for i in range(30):
                catalog.upsert("n64", f"usa.game_{i}", real_entry(library, name=f"usa.game_{i}.z64"))
        except Exception as error:  # noqa: BLE001
            errors.append(error)

    def scanner():
        try:
            for _ in range(60):
                report = catalog.scan()
                if report["missing"]:
                    false_alarms.append([m["id"] for m in report["missing"]])
        except Exception as error:  # noqa: BLE001
            errors.append(error)

    threads = [threading.Thread(target=writer)] + [threading.Thread(target=scanner) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert false_alarms == [], "a row landing mid-scan is not a game that went away"
    assert catalog.scan()["total"] == 30


# --- the /admin routes, after the parsing change ------------------------------


@pytest.fixture
def admin_tokens_service(catalog, tmp_path):
    from gotg.tokens import TokenStore

    config = Config(token=CLIENT, index_token=INDEX, admin_token=ADMIN)
    store = TokenStore(tmp_path / "state" / "tokens.db")
    server = make_server("127.0.0.1", free_port(), config, None, catalog, token_store=store)
    threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.mark.parametrize(
    "path",
    ["/admin//evil.com/tokens", "/admin//anything/scan", "/admin/x/scan", "/admin/scan/extra", "/admin//[x"],
)
def test_a_doubled_slash_does_not_alias_its_way_onto_an_admin_route(admin_tokens_service, path):
    """urlsplit reads the segment after a doubled slash as a netloc and drops it.

    Nothing escalates — it is all behind the admin token — but /admin is the
    one prefix an ingress rule, a NetworkPolicy or an audit grep is keyed on,
    and a request that reaches the token handler without the literal string in
    its path is invisible to every one of them. The "[" case used to raise
    ValueError out of urlsplit and kill the thread instead of answering.
    """
    status, _ = call(f"{admin_tokens_service}{path}", token=ADMIN)
    assert status == 404


def test_the_admin_routes_still_answer_at_their_real_paths(admin_tokens_service):
    assert call(f"{admin_tokens_service}/admin/tokens", token=ADMIN)[0] == 200
    assert call(f"{admin_tokens_service}/admin/scan", token=ADMIN)[0] == 200
    assert call(f"{admin_tokens_service}/admin/scan?since=2020-01-01T00:00:00Z", token=ADMIN)[0] == 200


def test_a_catalog_path_that_urlsplit_refuses_is_a_400_not_a_dropped_thread(service):
    # Pre-existing, and reachable with a plain client token: urlsplit raises
    # ValueError on an unbalanced "[", which left the caller with no status at
    # all and a traceback in the pod log.
    status, _ = call(f"{service}/catalog//[x", token=CLIENT)
    assert status == 400


def test_the_admin_gate_checks_the_bearer_and_not_the_principal_name(catalog, monkeypatch):
    """The admin gate now verifies the credential, as the index gate does.

    A principal is "admin" either because the token matched or because
    {auth_url}/auth/whoami said so, and that reply is parsed unvalidated.
    Nothing can currently make a peer say it — but the gate on the credential
    store should not be the thing resting on that.
    """
    import gotg.service.app as app_module

    config = Config(token=CLIENT, admin_token=ADMIN)
    server = make_server("127.0.0.1", free_port(), config, None, catalog)
    threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}"
    # A peer that vouches for any bearer as "admin".
    monkeypatch.setattr(app_module.Handler, "_principal", lambda self: "admin")
    try:
        status, _ = call(f"{url}/admin/scan", token="not-the-admin-token")
        assert status == 403, "the name says admin; the bearer does not"
        assert call(f"{url}/admin/scan", token=ADMIN)[0] == 200
    finally:
        server.shutdown()


def test_a_scan_that_cannot_read_the_catalog_is_a_500_that_names_no_path(admin_service, catalog, monkeypatch):
    # As in _catalog: a storage error string typically embeds server paths, so
    # it goes to stderr and the caller gets a bare 500 — rather than an
    # uncaught raise, which drops the connection with no status at all and
    # makes the client report a failing disk as a network problem.
    def boom(*_args, **_kwargs):
        raise sqlite3.OperationalError("unable to open database file: /srv/secret/catalog.db")

    monkeypatch.setattr(type(catalog), "scan", boom)
    status, problem = call(f"{admin_service}/admin/scan", token=ADMIN)
    assert status == 500
    assert problem["error"] == "catalog storage error"
    assert "/srv/secret" not in json.dumps(problem)


def test_one_scan_at_a_time(admin_service, catalog, monkeypatch):
    # Thousands of blocking stats against a network mount, and a person who
    # thinks it hung will retry. The second caller is told to come back rather
    # than parking a thread the mount may never give back.
    started, release = threading.Event(), threading.Event()
    real = type(catalog).scan

    def slow(self, **kwargs):
        started.set()
        release.wait(10)
        return real(self, **kwargs)

    monkeypatch.setattr(type(catalog), "scan", slow)
    first: list = []
    thread = threading.Thread(target=lambda: first.append(call(f"{admin_service}/admin/scan", token=ADMIN)))
    thread.start()
    assert started.wait(5)
    try:
        assert call(f"{admin_service}/admin/scan", token=ADMIN)[0] == 503
    finally:
        release.set()
        thread.join()
    assert first[0][0] == 200
    # The slot is released, so the next caller is served normally.
    assert call(f"{admin_service}/admin/scan", token=ADMIN)[0] == 200


# --- updates and DLC ----------------------------------------------------------


def test_extras_arriving_on_an_entry_are_not_a_conflict(catalog, library):
    game = library / "Zelda (World).7z"
    game.write_bytes(b"base")
    update = library / "Zelda_Update_v1.1" / "u.rar"
    update.parent.mkdir()
    update.write_bytes(b"patch")
    base = {"handler": "single_archive", "title": "Zelda", "files": [_file("Zelda (World).7z", game)]}
    catalog.upsert("switch", "world.zelda", base)

    with_update = {
        **base,
        "files": base["files"] + [_file("extras/update_1.1/u.rar", update)],
    }
    stored = catalog.upsert("switch", "world.zelda", with_update)
    assert [f["name"] for f in stored["files"]] == ["Zelda (World).7z", "extras/update_1.1/u.rar"]

    # And back: an update withdrawn is not a new game either.
    stored = catalog.upsert("switch", "world.zelda", base)
    assert [f["name"] for f in stored["files"]] == ["Zelda (World).7z"]


def test_a_different_base_behind_the_same_extras_is_still_a_conflict(catalog, library):
    game = library / "Zelda (World).7z"
    game.write_bytes(b"base")
    other = library / "Other" / "Zelda (World).7z"
    other.parent.mkdir()
    other.write_bytes(b"other")
    update = library / "u.rar"
    update.write_bytes(b"patch")
    extras = [_file("extras/update_1.1/u.rar", update)]
    catalog.upsert(
        "switch",
        "world.zelda",
        {"handler": "single_archive", "title": "Zelda", "files": [_file("Zelda (World).7z", game)] + extras},
    )

    with pytest.raises(Conflict):
        catalog.upsert(
            "switch",
            "world.zelda",
            {"handler": "single_archive", "title": "Zelda", "files": [_file("Zelda (World).7z", other)] + extras},
        )


def _file(name, path):
    st = path.stat()
    return {"name": name, "path": str(path), "size_bytes": st.st_size, "mtime": int(st.st_mtime), "sha256": None}
