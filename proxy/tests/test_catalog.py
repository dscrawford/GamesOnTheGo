"""The games catalog, at the store and over the wire.

What matters most: only the index token writes, a same-id-different-source
upsert is a 409 rather than a silent replacement, the sweep reports and never
deletes, and no row can name a path outside the library roots — because a row
is a grant to read that path.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest
from test_proxy import free_port

import gotg_proxy.catalog as catalog_module
from gotg_proxy.app import Config, make_server
from gotg_proxy.catalog import CatalogStore, Conflict, SweepRefused

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
    threading.Thread(target=server.serve_forever, daemon=True).start()
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
        ("name", "a/b.z64"),
        ("name", ".."),
        ("name", ".hidden"),
        ("name", ""),
        ("name", "a\tb"),
        ("name", "a" * 256),
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


def test_a_255_character_name_is_inside_the_boundary(catalog, library):
    catalog.upsert("n64", "usa.zelda", entry(library, name="a" * 255))
    assert catalog.view()["games"][0]["files"][0]["name"] == "a" * 255


def test_the_db_may_not_live_inside_a_root(library):
    with pytest.raises(ValueError, match="inside library root"):
        CatalogStore(db=library / "catalog.db", roots=[library])


def test_multi_file_entries_round_trip(catalog, library):
    volumes = {
        "handler": "scene_archive",
        "title": "Luigis Mansion 2 HD",
        "files": [
            {"name": f"hr-banra.r{i:02d}", "path": str(library / "rel" / f"hr-banra.r{i:02d}"),
             "size_bytes": 100, "mtime": 1, "sha256": None}
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
    status, _ = call(f"{service}/catalog/n64/usa.zelda", method="PUT",
                     token=INDEX, body=entry(library))
    assert status == 200

    status, view = call(f"{service}/catalog")
    assert status == 200
    assert view["games"][0]["id"] == "usa.zelda"

    status, _ = call(f"{service}/catalog/n64/usa.zelda", method="PUT",
                     token=CLIENT, body=entry(library))
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
    status, _ = call(f"{service}/catalog/n64/usa.zelda?force=1", method="PUT",
                     token=INDEX, body=other)
    assert status == 200


def test_wire_sweep_and_delete(service, library):
    call(f"{service}/catalog/n64/usa.zelda", method="PUT", token=INDEX, body=entry(library))

    status, _ = call(f"{service}/catalog/sweep", method="POST", token=CLIENT,
                     body={"since": "2099-01-01T00:00:00Z"})
    assert status == 403

    status, report = call(f"{service}/catalog/sweep?confirm=1", method="POST", token=INDEX,
                          body={"since": "2099-01-01T00:00:00Z"})
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
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        status, _ = call(f"http://127.0.0.1:{server.server_port}/catalog")
        assert status == 503
    finally:
        server.shutdown()


def test_matching_tokens_refuse_to_start():
    with pytest.raises(ValueError, match="index token equals the client token"):
        Config(token="same", index_token="same").validate()


def test_an_unset_index_token_makes_the_catalog_read_only(catalog, library):
    config = Config(token=CLIENT)
    server = make_server("127.0.0.1", free_port(), config, None, catalog)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        status, _ = call(f"{base}/catalog/n64/usa.zelda", method="PUT",
                         token=CLIENT, body=entry(library))
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
        {"name": "usa.extra", "path": str(library / "some-release" / "usa.extra"),
         "size_bytes": 2**64, "mtime": 1, "sha256": None}
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


def test_an_entry_larger_than_the_body_cap_is_a_413(service, library):
    fat = entry(library)
    fat["files"] = [
        {"name": f"usa.vol_{i:05}", "path": str(library / "rel" / f"usa.vol_{i:05}"),
         "size_bytes": 1, "mtime": 1, "sha256": None}
        for i in range(1000)
    ]
    status, _ = call(f"{service}/catalog/n64/usa.big", method="PUT", token=INDEX, body=fat)
    assert status == 413


def test_a_nesting_bomb_is_a_400_not_a_crash(service):
    bomb = b"[" * 30000 + b"]" * 30000
    status, _ = call(f"{service}/catalog/n64/usa.zelda", method="PUT", token=INDEX, body=bomb)
    assert status == 400


def test_unicode_titles_survive_the_wire(service, library):
    title = "ゼルダの伝説 時のオカリナ 🎮"
    status, stored = call(f"{service}/catalog/n64/usa.zelda", method="PUT", token=INDEX,
                          body=entry(library, title=title))
    assert status == 200 and stored["title"] == title
    status, view = call(f"{service}/catalog")
    assert view["games"][0]["title"] == title


def test_a_non_ascii_bearer_token_earns_a_401_not_a_crash(service):
    status, _ = call(f"{service}/catalog", token="\xff\xfe")
    assert status == 401


def test_percent_encoded_hostility_in_the_path_is_refused(service, library):
    status, _ = call(f"{service}/catalog/n64%27%3B%20DROP%20TABLE%20entry/usa.zelda",
                     method="PUT", token=INDEX, body=entry(library))
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
    from gotg_proxy.cli import catalog_from_env

    assert catalog_from_env({}) is None
    with pytest.raises(ValueError, match="set together"):
        catalog_from_env({"GOTG_CATALOG_DB": str(tmp_path / "c.db")})
    with pytest.raises(ValueError, match="set together"):
        catalog_from_env({"GOTG_LIBRARY_ROOTS": str(tmp_path)})
