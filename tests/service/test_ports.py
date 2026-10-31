"""The ports store: a pushed portable build, kept newest-only and per user, and
the list of what the flake can build that the web app shows beside it."""

from __future__ import annotations

import hashlib
import http.client
import json
from pathlib import Path

import pytest
from harness import send, serve

from gotg.ports import PortsStore, available
from gotg.service.app import Config
from gotg.service.routes_ports import ports_target
from gotg.tokens import TokenStore

GAMES = Path(__file__).resolve().parents[2] / "src" / "client" / "env" / "games"


@pytest.fixture
def ports(tmp_path):
    return PortsStore(root=tmp_path / "ports", max_bytes=1_000_000, games_dir=GAMES)


@pytest.fixture
def service(ports):
    with serve(Config(token="client-token"), ports=ports) as server:
        yield server.url


def put(url, output, body, headers=None, token="client-token"):
    return send(f"{url}/ports/{output}", method="PUT", body=body, token=token, headers=headers)


def test_available_lists_the_port_its_emulator_and_the_variants():
    entries = {e["output"]: e for e in available(GAMES, "n64", "usa.legend_of_zelda_ocarina_of_time_rev2")}
    assert list(entries)[:2] == [
        "n64.usa.legend_of_zelda_ocarina_of_time_rev2",
        "n64.usa.legend_of_zelda_ocarina_of_time_rev2.emulate",
    ]
    assert {"2p", "3p", "4p", "rando"} <= {e["variant"] for e in entries.values()}
    assert entries["n64.usa.legend_of_zelda_ocarina_of_time_rev2"]["description"] == (
        "Ocarina of Time — Ship of Harkinian."
    )
    # The usage line a variant header ends with is not its description.
    two = entries["n64.usa.legend_of_zelda_ocarina_of_time_rev2.2p"]["description"]
    assert two == "Ocarina of Time for 2."


def test_available_has_nothing_for_an_emulator_only_game_and_a_bad_shape():
    assert available(GAMES, "genesis", "usa.sonic_the_hedgehog_3") == []
    assert available(GAMES, "n64", "../../etc") == []
    assert available(None, "n64", "usa.donkey_kong_64") == []


def test_a_title_abbreviation_is_not_the_end_of_the_sentence():
    melee = available(GAMES, "gamecube", "usa.super_smash_bros_melee_rev2")[0]["description"]
    assert melee.startswith("Super Smash Bros. Melee — melee-pc")


def test_targets():
    assert ports_target("") == ("list",)
    assert ports_target("n64/usa.donkey_kong_64") == ("game", "n64", "usa.donkey_kong_64")
    assert ports_target("n64.usa.donkey_kong_64") == ("build", "n64.usa.donkey_kong_64")
    assert ports_target("n64.usa.super_mario_64.pc-2p/file") == ("file", "n64.usa.super_mario_64.pc-2p")
    assert ports_target("n64.usa.donkey_kong_64/other") is None
    assert ports_target("..%2f..") is None


def test_push_then_download_then_replace(service, ports):
    body = b"#!/bin/sh\necho portable\n" + bytes(range(256)) * 100
    status, _, raw = put(service, "n64.usa.donkey_kong_64", body, {"X-Gotg-Device": "deck"})
    assert status == 200
    meta = json.loads(raw)
    assert meta["sha256"] == hashlib.sha256(body).hexdigest() and meta["size"] == len(body) and meta["device"] == "deck"

    status, headers, got = send(f"{service}/ports/n64.usa.donkey_kong_64/file")
    assert status == 200 and got == body
    assert headers["ETag"] == f'"{meta["sha256"]}"'
    assert "gotg-n64.usa.donkey_kong_64.run" in headers["Content-Disposition"]
    # Resumable, like a game's files.
    status, _, tail = send(f"{service}/ports/n64.usa.donkey_kong_64/file", headers={"Range": "bytes=10-"})
    assert status == 206 and tail == body[10:]

    status, _, raw = send(f"{service}/ports/n64/usa.donkey_kong_64")
    listed = {e["output"]: e for e in json.loads(raw)["available"]}
    assert listed["n64.usa.donkey_kong_64"]["build"]["sha256"] == meta["sha256"]
    assert listed["n64.usa.donkey_kong_64.emulate"]["build"] is None

    # The newest build is the only one kept.
    assert put(service, "n64.usa.donkey_kong_64", b"second")[0] == 200
    files = sorted(p.name for p in (ports.root / "legacy" / "n64.usa.donkey_kong_64").iterdir())
    assert files == [f"build-{hashlib.sha256(b'second').hexdigest()[:12]}.run", "current.json"]
    assert send(f"{service}/ports/n64.usa.donkey_kong_64/file")[2] == b"second"

    assert [p["output"] for p in json.loads(send(f"{service}/ports")[2])["ports"]] == ["n64.usa.donkey_kong_64"]
    assert send(f"{service}/ports/n64.usa.donkey_kong_64", method="DELETE")[0] == 200
    assert send(f"{service}/ports/n64.usa.donkey_kong_64/file")[0] == 404


def test_a_claimed_hash_that_does_not_match_keeps_nothing(service, ports):
    status, _, raw = put(service, "n64.usa.donkey_kong_64", b"bytes", {"X-Gotg-Sha256": "0" * 64})
    assert status == 400 and b"hashes to" in raw
    directory = ports.root / "legacy" / "n64.usa.donkey_kong_64"
    assert [p.name for p in directory.iterdir()] == []


def test_over_the_cap_is_refused_before_the_body_is_read(service):
    host, port = service.removeprefix("http://").split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=10)
    conn.putrequest("PUT", "/ports/n64.usa.donkey_kong_64")
    conn.putheader("Authorization", "Bearer client-token")
    conn.putheader("Content-Length", str(10_000_000))
    conn.endheaders()
    response = conn.getresponse()
    assert response.status == 413
    conn.close()


def test_a_body_cut_short_leaves_no_file(service, ports):
    host, port = service.removeprefix("http://").split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=10)
    conn.putrequest("PUT", "/ports/n64.usa.donkey_kong_64")
    conn.putheader("Authorization", "Bearer client-token")
    conn.putheader("Content-Length", "1000")
    conn.endheaders()
    conn.send(b"only this")
    conn.sock.shutdown(1)
    response = conn.getresponse()
    assert response.status == 400
    conn.close()
    assert ports.meta("legacy", "n64.usa.donkey_kong_64") is None
    assert list((ports.root / "legacy" / "n64.usa.donkey_kong_64").iterdir()) == []


def test_shapes_and_methods(service):
    assert send(f"{service}/ports/n64.usa.donkey_kong_64/other")[0] == 404
    assert put(service, "../etc", b"x")[0] == 400
    assert put(service, "not-an-output", b"x")[0] == 404
    assert send(f"{service}/ports/n64/usa.donkey_kong_64", method="DELETE")[0] == 405
    assert send(f"{service}/ports", token=None)[0] == 401
    assert send(f"{service}/ports/n64.usa.donkey_kong_64")[0] == 404


def test_one_users_build_is_not_anothers(tmp_path, ports):
    tokens = TokenStore(db=tmp_path / "tokens.db")
    alice = tokens.claim(tokens.mint_invite("alice-deck"))[1]
    bob = tokens.claim(tokens.mint_invite("bob-pc"))[1]
    with serve(Config(token="client-token"), ports=ports, token_store=tokens) as server:
        assert put(server.url, "n64.usa.donkey_kong_64", b"alice's", token=alice)[0] == 200
        assert send(f"{server.url}/ports/n64.usa.donkey_kong_64/file", token=bob)[0] == 404
        assert send(f"{server.url}/ports/n64.usa.donkey_kong_64/file", token=alice)[2] == b"alice's"


def test_no_store_is_a_503(tmp_path):
    with serve(Config(token="client-token")) as server:
        assert send(f"{server.url}/ports")[0] == 503
        assert put(server.url, "n64.usa.donkey_kong_64", b"x")[0] == 503
