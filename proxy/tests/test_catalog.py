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


def test_shapes_are_validated(catalog, library):
    for platform, game_id, mutate in [
        ("N64", "usa.zelda", {}),
        ("n64", "prod.keys!", {}),
        ("n64", "usa.zelda", {"handler": "mystery"}),
        ("n64", "usa.zelda", {"files": []}),
        ("n64", "usa.zelda", {"title": " "}),
    ]:
        with pytest.raises(ValueError):
            catalog.upsert(platform, game_id, entry(library, **mutate))

    for bad_name in ["a/b.z64", "..", ".hidden", ""]:
        payload = entry(library)
        payload["files"][0]["name"] = bad_name
        with pytest.raises(ValueError, match="file name"):
            catalog.upsert("n64", "usa.zelda", payload)


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
