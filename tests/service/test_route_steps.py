"""The decisions the handler's routes make, each aimed at directly.

The decisions that are not I/O are pure functions (a path, header or body in, a
verdict out), asserted here without a server; the routes are covered through the
harness.
"""

from __future__ import annotations

import pytest

from gotg.service import app
from gotg.service.routes_admin import MAX_INVITE_DAYS, parse_invite
from gotg.service.routes_catalog import advertised_hosts, touch_keys
from gotg.service.routes_files import range_window
from gotg.service.routes_saves import GENERATION, HEAD, HISTORY, META, clean_device, saves_target


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("zelda", ("zelda", HEAD, "")),
        ("/zelda/", ("zelda", HEAD, "")),
        ("zelda/meta", ("zelda", META, "")),
        ("zelda/history", ("zelda", HISTORY, "")),
        ("zelda/gen/3", ("zelda", GENERATION, "3")),
        # The digits as written: a 404 for a generation names what was asked.
        ("zelda/gen/007", ("zelda", GENERATION, "007")),
        ("", ("", HEAD, "")),
    ],
)
def test_saves_target_reads_what_a_path_asks_for(path, expected):
    assert saves_target(path) == expected


@pytest.mark.parametrize(
    "path",
    ["zelda/gen/-1", "zelda/gen/1.5", "zelda/gen/", "zelda/gen/x", "zelda/gen/1/2", "zelda/other", "zelda/meta/x"],
)
def test_saves_target_refuses_what_is_not_a_saves_path(path):
    assert saves_target(path) is None


def test_saves_target_refuses_non_ascii_digits():
    # str.isdigit() is true for these; int() of them would also parse.
    assert saves_target("zelda/gen/٣") is None


def test_a_device_name_is_printable_and_short():
    assert clean_device("deck\x1b[31m\n") == "deck[31m"
    assert clean_device("x" * 100) == "x" * 32
    assert clean_device("") == ""


def test_a_touch_names_platform_and_id_pairs():
    assert touch_keys({"games": ["n64/usa.mario", "gba/eur.zelda_minish"]}) == [
        ("n64", "usa.mario"),
        ("gba", "eur.zelda_minish"),
    ]
    assert touch_keys({"games": []}) == []


@pytest.mark.parametrize("payload", [{}, [], "n64/x", {"games": "n64/x"}, {"games": [1]}, {"games": None}])
def test_a_touch_of_the_wrong_shape_says_what_a_touch_is(payload):
    with pytest.raises(ValueError, match="a touch is"):
        touch_keys(payload)


@pytest.mark.parametrize("game", ["n64", "N64/usa.x", "n64/", "/usa.x", "n64/../x", "n64/mario"])
def test_a_touch_of_a_bad_entry_names_it(game):
    with pytest.raises(ValueError, match="not a platform and an entry id"):
        touch_keys({"games": [game]})


def test_the_preferred_host_comes_first_and_is_not_repeated():
    assert advertised_hosts("http://tail", "http://pub") == ["http://tail", "http://pub"]
    assert advertised_hosts("http://pub", "http://pub") == ["http://pub"]
    assert advertised_hosts("", "http://pub") == ["http://pub"]
    assert advertised_hosts("http://tail", "") == ["http://tail"]
    assert advertised_hosts("", "") == []


ETAG = '"abc"'


@pytest.mark.parametrize(
    ("range_header", "if_range", "expected"),
    [
        ("", "", (0, 99, 200)),
        ("bytes=10-19", "", (10, 19, 206)),
        ("bytes=10-", "", (10, 99, 206)),
        ("bytes=10-5000", "", (10, 99, 206)),
        ("bytes=0-99", "", (0, 99, 206)),
        # A current validator keeps the range; a stale one sends the whole file.
        ("bytes=10-19", ETAG, (10, 19, 206)),
        ("bytes=10-19", '"stale"', (0, 99, 200)),
        # Inverted: invalid, so ignored rather than refused.
        ("bytes=50-10", "", (0, 99, 200)),
        # Multi-range and garbage are not understood, so the whole file.
        ("bytes=0-1,5-6", "", (0, 99, 200)),
        ("items=0-1", "", (0, 99, 200)),
        ("bytes=" + "9" * 40 + "-", "", (0, 99, 200)),
    ],
)
def test_range_window_decides_a_status_and_a_window(range_header, if_range, expected):
    assert range_window(range_header, if_range, ETAG, 100) == expected


@pytest.mark.parametrize("range_header", ["bytes=100-", "bytes=500-600"])
def test_a_range_past_the_end_is_unsatisfiable(range_header):
    assert range_window(range_header, "", ETAG, 100)[2] == 416


def test_a_stale_validator_on_an_unsatisfiable_range_still_sends_the_file():
    assert range_window("bytes=500-", '"stale"', ETAG, 100) == (0, 99, 200)


def test_a_file_with_no_etag_never_honours_if_range():
    # /files has no hash: with If-Range present there is nothing to match.
    assert range_window("bytes=10-19", '"x"', None, 100) == (0, 99, 200)
    assert range_window("bytes=10-19", "", None, 100) == (10, 19, 206)


def test_an_empty_file_has_no_satisfiable_range():
    assert range_window("", "", None, 0) == (0, -1, 200)
    assert range_window("bytes=0-", "", None, 0)[2] == 416


def test_an_invite_takes_a_name_and_defaults_to_a_week():
    assert parse_invite(b'{"name": "daniel"}') == ("daniel", None, 7.0)
    assert parse_invite(b'{"name": "d", "user": "u", "ttl_days": 2}') == ("d", "u", 2.0)


@pytest.mark.parametrize("body", [b"not json", b"[]", b'"x"', b'{"ttl_days": "soon"}', b'{"ttl_days": null}'])
def test_an_invite_body_of_the_wrong_shape_says_what_it_should_be(body):
    with pytest.raises(ValueError, match="the body is"):
        parse_invite(body)


@pytest.mark.parametrize("ttl", ["0", "-1", "NaN", "Infinity", str(MAX_INVITE_DAYS + 1)])
def test_an_invite_ttl_outside_its_range_is_refused(ttl):
    with pytest.raises(ValueError, match="ttl_days must be between"):
        parse_invite(f'{{"name": "x", "ttl_days": {ttl}}}'.encode())


def test_the_longest_invite_is_allowed():
    assert parse_invite(f'{{"ttl_days": {MAX_INVITE_DAYS}}}'.encode())[2] == MAX_INVITE_DAYS


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("saves/zelda", ("saves", "zelda")),
        ("catalog", ("catalog", "")),
        ("catalog?full=1", ("catalog", "?full=1")),
        ("catalog/?full=1", ("catalog", "?full=1")),
        ("catalog/n64/x?force=1", ("catalog", "n64/x?force=1")),
        ("", ("", "")),
    ],
)
def test_split_route_keeps_a_query_on_a_bare_prefix_out_of_the_prefix(path, expected):
    assert app.split_route(path) == expected


class _Store:
    max_bytes = 123


def test_only_a_save_a_catalog_write_and_an_art_put_get_a_bigger_body():
    assert app.body_cap("saves", "PUT", _Store()) == 123
    assert app.body_cap("saves", "PUT", None) == app.MAX_BODY
    assert app.body_cap("saves", "GET", _Store()) == app.MAX_BODY
    assert app.body_cap("catalog", "PUT", None) == app.CATALOG_MAX_BODY
    assert app.body_cap("catalog", "POST", None) == app.CATALOG_MAX_BODY
    assert app.body_cap("catalog", "DELETE", None) == app.MAX_BODY
    assert app.body_cap("art", "PUT", None) == app.ART_MAX_BODY
    assert app.body_cap("art", "GET", None) == app.MAX_BODY
    assert app.body_cap("igdb", "POST", _Store()) == app.MAX_BODY
