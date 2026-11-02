"""The ports store: a pushed portable build, kept newest-only and per user, and
the list of what the flake can build that the web app shows beside it."""

from __future__ import annotations

import hashlib
import http.client
import json
from pathlib import Path

import pytest
from harness import send, serve

from gotg.ports import CLAIM_TTL, NotWebBuildable, PortsStore, available, web_builder
from gotg.service.app import Config
from gotg.service.routes_ports import ports_target
from gotg.tokens import TokenStore

GAMES = Path(__file__).resolve().parents[2] / "src" / "client" / "env" / "games"
WEB = GAMES.parent / "web"
SM64 = "n64.usa.super_mario_64"


@pytest.fixture
def ports(tmp_path):
    return PortsStore(root=tmp_path / "ports", max_bytes=1_000_000, games_dir=GAMES, web_dir=WEB)


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
    assert ports_target("requests") == ("requests",)
    assert ports_target(f"{SM64}/web") == ("web", SM64)
    assert ports_target(f"{SM64}/web/file") == ("web-file", SM64)
    assert ports_target(f"{SM64}/web/claim") == ("web-claim", SM64)
    assert ports_target(f"{SM64}/web/fail") == ("web-fail", SM64)
    assert ports_target(f"{SM64}/web/other") is None


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


# --- browser builds, built on request ------------------------------------------


def test_web_builder_is_a_games_own_output_with_a_file():
    assert web_builder(WEB, SM64) == WEB / "n64" / "usa.super_mario_64.nix"
    assert web_builder(WEB, SM64 + ".pc") is None  # a variant is a native launch
    assert web_builder(WEB, "n64.usa.donkey_kong_64") is None
    assert web_builder(None, SM64) is None


def test_the_game_lists_its_browser_build(service):
    info = json.loads(send(f"{service}/ports/n64/usa.super_mario_64")[2])
    assert info["web"]["output"] == SM64 and info["web"]["build"] is None and info["web"]["request"] is None
    assert info["web"]["description"].startswith("Super Mario 64 in the browser")
    assert json.loads(send(f"{service}/ports/n64/usa.donkey_kong_64")[2])["web"] is None


def test_ask_claim_push_play(service, ports):
    # Nothing waits until the web app asks.
    assert json.loads(send(f"{service}/ports/requests")[2])["requests"] == []
    status, _, raw = send(f"{service}/ports/{SM64}/web", method="POST")
    assert status == 200 and json.loads(raw)["request"]["state"] == "pending"
    # Asking again is the same request, not a second one.
    first = json.loads(raw)["request"]["requested_at"]
    assert json.loads(send(f"{service}/ports/{SM64}/web", method="POST")[2])["request"]["requested_at"] == first
    assert [r["output"] for r in json.loads(send(f"{service}/ports/requests")[2])["requests"]] == [SM64]

    # One worker takes it; the next is told it is gone.
    status, _, raw = send(f"{service}/ports/{SM64}/web/claim", method="POST", headers={"X-Gotg-Device": "desk"})
    assert status == 200 and json.loads(raw)["state"] == "building" and json.loads(raw)["device"] == "desk"
    assert send(f"{service}/ports/{SM64}/web/claim", method="POST")[0] == 409
    assert json.loads(send(f"{service}/ports/requests")[2])["requests"] == []
    state = json.loads(send(f"{service}/ports/{SM64}/web")[2])
    assert state["build"] is None and state["request"]["state"] == "building"

    body = b"var Module = Module || {};" + b"x" * 5000
    status, _, raw = send(f"{service}/ports/{SM64}/web", method="PUT", body=body, headers={"X-Gotg-Device": "desk"})
    assert status == 200 and json.loads(raw)["file"].endswith(".js")
    state = json.loads(send(f"{service}/ports/{SM64}/web")[2])
    assert state["build"]["sha256"] == hashlib.sha256(body).hexdigest() and state["request"] is None
    status, headers, got = send(f"{service}/ports/{SM64}/web/file")
    assert status == 200 and got == body and f"gotg-{SM64}.js" in headers["Content-Disposition"]
    # Beside, not instead of, a portable build of the same output.
    assert send(f"{service}/ports/{SM64}/file")[0] == 404
    kinds = {(p["output"], p["kind"]) for p in json.loads(send(f"{service}/ports")[2])["ports"]}
    assert kinds == {(SM64, "web")}

    assert send(f"{service}/ports/{SM64}/web", method="DELETE")[0] == 200
    assert send(f"{service}/ports/{SM64}/web/file")[0] == 404


def test_a_failure_is_shown_until_asked_again(service):
    assert send(f"{service}/ports/{SM64}/web/fail", method="POST", body=b'{"error": "x"}')[0] == 409
    send(f"{service}/ports/{SM64}/web", method="POST")
    send(f"{service}/ports/{SM64}/web/claim", method="POST")
    assert send(f"{service}/ports/{SM64}/web/fail", method="POST", body=b"not json")[0] == 400
    status, _, raw = send(f"{service}/ports/{SM64}/web/fail", method="POST", body=b'{"error": "emcc: error"}')
    assert status == 200 and json.loads(raw)["state"] == "failed"
    request = json.loads(send(f"{service}/ports/{SM64}/web")[2])["request"]
    assert request["state"] == "failed" and request["error"] == "emcc: error"
    # Not something a worker picks up again by itself...
    assert json.loads(send(f"{service}/ports/requests")[2])["requests"] == []
    # ...until somebody presses Play again.
    assert json.loads(send(f"{service}/ports/{SM64}/web", method="POST")[2])["request"]["state"] == "pending"


def test_a_claim_that_never_lands_goes_back_in_the_queue(tmp_path):
    now = [1_000_000.0]
    ports = PortsStore(root=tmp_path / "ports", games_dir=GAMES, web_dir=WEB, clock=lambda: now[0])
    ports.request_web("alice", SM64)
    assert ports.claim("alice", SM64, "desk")["state"] == "building"
    assert ports.pending("alice") == [] and ports.claim("alice", SM64, "laptop") is None
    now[0] += CLAIM_TTL + 1
    assert [r["output"] for r in ports.pending("alice")] == [SM64]
    assert ports.claim("alice", SM64, "laptop")["device"] == "laptop"


def test_only_a_game_with_a_builder_can_be_asked_for_or_pushed(service, ports):
    assert send(f"{service}/ports/n64.usa.donkey_kong_64/web", method="POST")[0] == 404
    assert send(f"{service}/ports/n64.usa.donkey_kong_64/web")[0] == 404
    assert send(f"{service}/ports/n64.usa.donkey_kong_64/web", method="PUT", body=b"x")[0] == 404
    with pytest.raises(NotWebBuildable):
        ports.request_web("legacy", "n64.usa.donkey_kong_64")
    assert send(f"{service}/ports/requests", method="POST")[0] == 405


def test_one_users_requests_are_not_anothers(tmp_path, ports):
    ports.request_web("alice", SM64)
    assert ports.pending("bob") == []
    assert ports.claim("bob", SM64) is None
