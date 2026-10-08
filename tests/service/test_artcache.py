"""The fleet's tile-picture cache.

What matters here is the trade the cache exists to make: one machine resolves
a picture once, slowly, and every other machine gets it from us without ever
touching SteamGridDB. So the tests are about who may write, what a client can
learn without asking an upstream, and that a miss is an answer rather than a
gap.
"""

from __future__ import annotations

import json

import pytest
from harness import send, serve

from gotg.service.app import Config, RateLimiter
from gotg.service.artcache import ArtCache

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff" + b"\x00" * 32


@pytest.fixture
def cache(tmp_path):
    return ArtCache(tmp_path / "art")


@pytest.fixture
def service(cache):
    config = Config(token="client-token", index_token="index-token", art_dir=str(cache.root))
    with serve(config, art=cache) as server:
        yield server.url


def call(url, token="client-token", data=None, method=None, headers=None):
    status, response_headers, body = send(url, method=method, token=token, body=data, headers=headers)
    return status, body, response_headers


# --- the cache on its own ---------------------------------------------------


def test_a_picture_comes_back_with_its_kind(cache):
    cache.put("n64", "usa.zelda", PNG)
    path, extension = cache.get("n64", "usa.zelda")
    assert extension == ".png"
    assert path.read_bytes() == PNG


def test_the_same_id_on_two_platforms_is_two_pictures(cache):
    cache.put("gb", "usa.bugs_life", PNG)
    cache.put("snes", "usa.bugs_life", JPEG)
    assert cache.get("gb", "usa.bugs_life")[1] == ".png"
    assert cache.get("snes", "usa.bugs_life")[1] == ".jpg"


def test_a_new_format_replaces_the_old_one(cache):
    cache.put("n64", "usa.zelda", PNG)
    cache.put("n64", "usa.zelda", JPEG)
    assert cache.get("n64", "usa.zelda")[1] == ".jpg"
    assert not cache.path_for("n64", "usa.zelda", ".png").exists()


def test_a_miss_is_remembered(cache):
    cache.put_miss("switch", "usa.some_game")
    assert cache.is_miss("switch", "usa.some_game")
    assert cache.get("switch", "usa.some_game") is None


def test_only_contract_names_become_paths(cache):
    for platform, game_id in (("../etc", "usa.zelda"), ("n64", "../../etc/passwd"), ("n64", "Usa.Zelda")):
        with pytest.raises(ValueError):
            cache.put(platform, game_id, PNG)


def test_the_index_is_everything_known(cache):
    cache.put("n64", "usa.zelda", PNG)
    cache.put_miss("switch", "usa.some_game")
    index = cache.index()
    assert index["art"]["n64/usa.zelda"] == {"ext": ".png", "bytes": len(PNG)}
    assert index["misses"] == ["switch/usa.some_game"]


def test_an_empty_cache_is_an_empty_index(cache):
    # The volume exists before the first warm has ever run.
    assert cache.index() == {"version": 1, "art": {}, "misses": []}


def test_forgetting_drops_both_halves(cache):
    cache.put("n64", "usa.zelda", PNG)
    cache.put_miss("switch", "usa.some_game")
    assert cache.forget("n64", "usa.zelda")
    assert cache.forget("switch", "usa.some_game")
    assert not cache.forget("n64", "usa.never_seen")


# --- on the wire ------------------------------------------------------------


def test_reading_art_needs_a_token(service):
    status, _, _ = call(f"{service}/art", token=None)
    assert status == 401


def test_a_client_can_read_the_index(service, cache):
    cache.put("n64", "usa.zelda", PNG)
    status, body, _ = call(f"{service}/art")
    assert status == 200
    assert json.loads(body)["art"]["n64/usa.zelda"]["ext"] == ".png"


def test_a_client_gets_the_picture_with_its_content_type(service, cache):
    cache.put("n64", "usa.zelda", PNG)
    status, body, headers = call(f"{service}/art/n64/usa.zelda")
    assert (status, body) == (200, PNG)
    assert headers["Content-Type"] == "image/png"


def test_a_second_ask_can_be_answered_with_a_304(service, cache):
    cache.put("n64", "usa.zelda", PNG)
    _, _, headers = call(f"{service}/art/n64/usa.zelda")
    status, body, _ = call(f"{service}/art/n64/usa.zelda", headers={"If-None-Match": headers["ETag"]})
    assert (status, body) == (304, b"")


def test_a_picture_carries_a_stable_etag_and_a_long_lifetime(service, cache):
    cache.put("n64", "usa.zelda", PNG)
    _, _, first = call(f"{service}/art/n64/usa.zelda")
    _, _, second = call(f"{service}/art/n64/usa.zelda")
    assert first["ETag"] == second["ETag"]
    assert first["ETag"].startswith('"') and first["ETag"].endswith('"')
    assert first["Cache-Control"] == "public, max-age=604800"
    assert first["X-Gotg-Art"] == "hit"


def test_a_304_keeps_the_validators_and_a_stale_etag_gets_the_picture(service, cache):
    cache.put("n64", "usa.zelda", PNG)
    _, _, headers = call(f"{service}/art/n64/usa.zelda")
    status, body, not_modified = call(f"{service}/art/n64/usa.zelda", headers={"If-None-Match": headers["ETag"]})
    assert (status, body) == (304, b"")
    assert not_modified["ETag"] == headers["ETag"]
    assert not_modified["Content-Type"] == "image/png"

    status, body, _ = call(f"{service}/art/n64/usa.zelda", headers={"If-None-Match": '"not-this-one"'})
    assert (status, body) == (200, PNG)


def test_a_404_says_which_kind_of_nothing_in_its_body_too(service, cache):
    cache.put_miss("switch", "usa.some_game")
    status, body, headers = call(f"{service}/art/switch/usa.some_game")
    assert status == 404
    assert headers["Content-Type"] == "application/json"
    assert json.loads(body)["miss"] is True
    assert json.loads(call(f"{service}/art/switch/usa.other_game")[1])["miss"] is False


def test_a_miss_is_told_apart_from_never_looked(service, cache):
    cache.put_miss("switch", "usa.some_game")
    _, _, miss = call(f"{service}/art/switch/usa.some_game")
    _, _, absent = call(f"{service}/art/switch/usa.other_game")
    assert miss["X-Gotg-Art"] == "miss"
    assert absent["X-Gotg-Art"] == "absent"


def test_a_client_token_cannot_write_art(service, cache):
    status, _, _ = call(f"{service}/art/n64/usa.zelda", data=PNG, method="PUT")
    assert status == 403
    assert cache.get("n64", "usa.zelda") is None


def test_the_index_token_writes_art(service, cache):
    status, _, _ = call(f"{service}/art/n64/usa.zelda", token="index-token", data=PNG, method="PUT")
    assert status == 200
    assert cache.get("n64", "usa.zelda")[1] == ".png"


def test_the_warmer_records_a_miss(service, cache):
    status, _, _ = call(f"{service}/art/switch/usa.some_game?miss=1", token="index-token", data=b"", method="PUT")
    assert status == 200
    assert cache.is_miss("switch", "usa.some_game")


def test_something_that_is_not_a_picture_is_refused(service, cache):
    status, _, _ = call(f"{service}/art/n64/usa.zelda", token="index-token", data=b"<html>nope", method="PUT")
    assert status == 415
    assert cache.get("n64", "usa.zelda") is None


def test_a_name_off_the_contract_is_a_400_not_a_traceback(service):
    status, _, _ = call(f"{service}/art/n64/Usa.Zelda", token="index-token", data=PNG, method="PUT")
    assert status == 400


def test_deleting_lets_the_next_warm_look_again(service, cache):
    cache.put_miss("switch", "usa.some_game")
    status, _, _ = call(f"{service}/art/switch/usa.some_game", token="index-token", method="DELETE")
    assert status == 200
    assert not cache.is_miss("switch", "usa.some_game")


def test_a_deployment_without_a_cache_says_so(cache):
    config = Config(token="client-token")
    with serve(config) as server:
        status, _, _ = call(f"{server.url}/art")
        assert status == 503


# --- the pace ---------------------------------------------------------------


def test_the_burst_goes_straight_through():
    limiter = RateLimiter(rate=1.0, burst=5.0)
    assert [limiter.reserve(10.0) for _ in range(5)] == [(True, 0.0)] * 5


def test_past_the_burst_a_caller_is_asked_to_wait():
    limiter = RateLimiter(rate=2.0, burst=1.0)
    limiter.reserve(10.0)
    allowed, delay = limiter.reserve(10.0)
    assert allowed and 0 < delay <= 0.5


def test_a_wait_longer_than_the_caller_will_hold_is_refused():
    limiter = RateLimiter(rate=1.0, burst=1.0)
    limiter.reserve(10.0)
    assert limiter.reserve(0.1) == (False, pytest.approx(1.0, abs=0.05))


def test_a_refused_slot_is_not_spent():
    # Otherwise a client that gets a 429 has still made everybody else wait.
    limiter = RateLimiter(rate=1.0, burst=1.0)
    limiter.reserve(10.0)
    limiter.reserve(0.0)
    limiter.reserve(0.0)
    allowed, delay = limiter.reserve(10.0)
    assert allowed and delay <= 1.0
