"""The catalog as a browser asks for it: pages, search, filters, one game."""

from __future__ import annotations

import json

import pytest
from harness import send, serve

from gotg.catalog import CatalogStore
from gotg.service.app import Config

CLIENT = "client-token"

GAMES = [
    ("n64", "usa.zelda_ocarina", "Zelda Ocarina", 300, "single_file"),
    ("n64", "eur.zelda_ocarina", "Zelda Ocarina", 310, "single_file"),
    ("n64", "usa.mario_64", "Super Mario 64", 100, "single_file"),
    ("snes", "usa.metroid", "Super Metroid", 50, "single_file"),
    ("gc", "jpn.melee", "Melee", 1000, "scene_archive"),
]


@pytest.fixture
def service(tmp_path):
    root = tmp_path / "lib"
    root.mkdir()
    catalog = CatalogStore(db=tmp_path / "c.db", roots=[root])
    for platform, game_id, title, size, handler in GAMES:
        catalog.upsert(
            platform,
            game_id,
            {
                "handler": handler,
                "title": title,
                "files": [
                    {
                        "name": f"{game_id}.bin",
                        "path": str(root / f"{game_id}.bin"),
                        "size_bytes": size,
                        "mtime": 1,
                        "sha256": "a" * 64,
                    }
                ],
            },
        )
    with serve(Config(token=CLIENT), None, catalog) as server:
        yield server


def get(service, path):
    status, headers, body = service.call("GET", path)
    return status, json.loads(body)


def ids(reply):
    return [g["id"] for g in reply["games"]]


def test_unparameterised_catalog_is_unchanged(service):
    status, reply = get(service, "/catalog")
    assert status == 200
    assert "total" not in reply and len(reply["games"]) == 5
    assert "region" not in reply["games"][0]


def test_pagination(service):
    _, first = get(service, "/catalog?limit=2&sort=id")
    assert first["total"] == 5 and first["limit"] == 2 and first["offset"] == 0
    _, second = get(service, "/catalog?limit=2&offset=2&sort=id")
    _, third = get(service, "/catalog?limit=2&offset=4&sort=id")
    assert ids(first) + ids(second) + ids(third) == sorted(g[1] for g in GAMES)
    _, beyond = get(service, "/catalog?offset=99")
    assert beyond["games"] == [] and beyond["total"] == 5


def test_search_matches_title_and_id_terms(service):
    _, reply = get(service, "/catalog?q=zelda")
    assert sorted(ids(reply)) == ["eur.zelda_ocarina", "usa.zelda_ocarina"]
    _, reply = get(service, "/catalog?q=SUPER%2064")
    assert ids(reply) == ["usa.mario_64"]


def test_filters_combine(service):
    _, reply = get(service, "/catalog?platform=n64&region=usa")
    assert sorted(ids(reply)) == ["usa.mario_64", "usa.zelda_ocarina"]
    _, reply = get(service, "/catalog?platform=snes,gc")
    assert reply["total"] == 2
    _, reply = get(service, "/catalog?handler=scene_archive")
    assert ids(reply) == ["jpn.melee"]
    _, reply = get(service, "/catalog?min_size=100&max_size=300")
    assert sorted(ids(reply)) == ["usa.mario_64", "usa.zelda_ocarina"]


def test_game_summary_fields_and_sort(service):
    _, reply = get(service, "/catalog?sort=size&order=desc&limit=1")
    game = reply["games"][0]
    assert game["id"] == "jpn.melee" and game["region"] == "jpn"
    assert game["size_bytes"] == 1000 and game["file_count"] == 1
    assert "path" not in game["files"][0]


def test_facets_count_everything_not_just_the_page(service):
    _, reply = get(service, "/catalog?platform=gc&facets=1")
    assert reply["facets"]["platform"] == {"gc": 1, "n64": 3, "snes": 1}
    assert reply["facets"]["region"]["usa"] == 3


@pytest.mark.parametrize(
    "query", ["limit=0", "limit=100000", "limit=x", "offset=-1", "sort=nope", "order=up", "min_size=a"]
)
def test_bad_parameters_are_400(service, query):
    status, reply = get(service, f"/catalog?{query}")
    assert status == 400 and "error" in reply


def test_one_game(service):
    status, reply = get(service, "/catalog/snes/usa.metroid")
    assert status == 200 and reply["title"] == "Super Metroid" and reply["region"] == "usa"
    assert get(service, "/catalog/snes/usa.nothing")[0] == 404


def test_paged_view_still_needs_auth(service):
    assert send(f"{service.url}/catalog?limit=1", token=None)[0] == 401
