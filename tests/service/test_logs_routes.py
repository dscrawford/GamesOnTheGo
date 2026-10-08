"""`/logs` over the wire: players put, only the admin token reads or deletes."""

from __future__ import annotations

import json

import pytest
from harness import serve

from gotg.logs import LogsStore
from gotg.service.app import Config
from gotg.service.cli import logs_from_env
from gotg.tokens import TokenStore

ADMIN = "admin-token"
ZSTD = b"\x28\xb5\x2f\xfd" + b"x" * 100
SID = "20261008T165221Z-env-n64-game"
SID2 = "20261009T165221Z-env-n64-game"


@pytest.fixture
def tokens(tmp_path):
    return TokenStore(tmp_path / "tokens.db")


def player(tokens, name, user):
    return tokens.claim(tokens.mint_invite(name, user=user))[1]


@pytest.fixture
def service(tmp_path, tokens):
    store = LogsStore(tmp_path / "logs", quota_bytes=1000, max_upload_bytes=300)
    config = Config(token="client-token", admin_token=ADMIN)
    with serve(config, logs=store, token_store=tokens) as server:
        yield server


def blob(n=150):
    return ZSTD + b"y" * (n - len(ZSTD))


def put(service, sid=SID, body=None, token=...):
    return service.call(
        "PUT",
        f"/logs/{sid}",
        body=blob() if body is None else body,
        headers={"X-Gotg-Attr": "env-n64-game", "X-Gotg-Device": "deck"},
        token=token,
    )


def test_put_answers_kept(service):
    status, _, body = put(service)
    assert status == 200
    assert json.loads(body) == {"kept": 150, "sessions": 1, "dropped": []}


def test_round_trip_list_get_delete(service):
    put(service)
    status, _, body = service.call("GET", "/logs", token=ADMIN)
    assert status == 200
    (user,) = json.loads(body)["users"]
    assert user["bytes"] == 150
    (session,) = user["sessions"]
    assert session["id"] == SID and session["attr"] == "env-n64-game" and session["device"] == "deck"
    name = user["user"]

    status, headers, body = service.call("GET", f"/logs/{name}/{SID}", token=ADMIN)
    assert (status, body) == (200, blob())
    assert headers["Content-Type"] == "application/zstd"

    assert service.call("DELETE", f"/logs/{name}/{SID}", token=ADMIN)[0] == 200
    assert service.call("GET", f"/logs/{name}/{SID}", token=ADMIN)[0] == 404
    assert json.loads(service.call("GET", "/logs", token=ADMIN)[2])["users"] == []


def test_quota_pruning_is_reported(service):
    for sid in (SID, SID2):
        put(service, sid, blob(300))
    put(service, "20261010T165221Z-env-n64-game", blob(300))
    status, _, body = put(service, "20261011T165221Z-env-n64-game", blob(300))
    assert status == 200
    assert json.loads(body)["dropped"]


def test_over_the_upload_cap_is_413(service):
    assert put(service, body=blob(301))[0] == 413


def test_bad_id_is_400(service):
    assert put(service, "not-a-session")[0] == 400


def test_non_zstd_is_400(service):
    assert put(service, body=b"plain text body")[0] == 400


def test_no_token_is_401(service):
    assert put(service, token=None)[0] == 401
    assert service.call("GET", "/logs", token=None)[0] == 401


def test_a_player_cannot_list_read_or_delete(service):
    put(service)
    assert service.call("GET", "/logs")[0] == 403
    assert service.call("GET", f"/logs/legacy/{SID}")[0] == 403
    assert service.call("DELETE", f"/logs/legacy/{SID}")[0] == 403


def test_the_admin_token_cannot_upload(service):
    assert put(service, token=ADMIN)[0] == 401


def test_no_store_is_503():
    with serve(Config(token="client-token", admin_token=ADMIN)) as server:
        assert server.call("PUT", f"/logs/{SID}", body=blob())[0] == 503
        assert server.call("GET", "/logs", token=ADMIN)[0] == 503


def test_paths_that_climb_or_do_not_exist_are_refused(service):
    assert service.call("GET", "/logs/legacy/../x", token=ADMIN)[0] in (400, 404)
    assert service.call("GET", "/logs/legacy/nope", token=ADMIN)[0] == 404
    assert service.call("POST", "/logs")[0] == 405


def test_each_players_sessions_land_under_their_own_user(service, tokens):
    ann, bob = player(tokens, "ann-deck", "ann"), player(tokens, "bob-deck", "bob")
    assert put(service, token=ann)[0] == 200
    assert put(service, token=bob)[0] == 200
    listed = {u["user"]: u["bytes"] for u in json.loads(service.call("GET", "/logs", token=ADMIN)[2])["users"]}
    assert listed == {"ann": 150, "bob": 150}
    for token in (ann, bob):
        assert service.call("GET", f"/logs/ann/{SID}", token=token)[0] == 403
    assert service.call("GET", f"/logs/ann/{SID}", token=ADMIN)[0] == 200


def test_the_public_listener_does_not_take_the_admin_token_for_logs(tmp_path):
    store = LogsStore(tmp_path / "logs", quota_bytes=1000, max_upload_bytes=300)
    config = Config(token="client-token", admin_token=ADMIN)
    with serve(config, logs=store, listener="public") as server:
        assert server.call("GET", "/logs", token=ADMIN)[0] == 401
    with serve(config, logs=store, listener="admin") as server:
        assert server.call("GET", "/logs", token=ADMIN)[0] == 200


def test_config_from_env(tmp_path):
    assert logs_from_env({}) is None
    store = logs_from_env(
        {"GOTG_LOGS_DIR": str(tmp_path), "GOTG_LOGS_QUOTA_BYTES": "5", "GOTG_LOGS_MAX_UPLOAD_BYTES": "7"}
    )
    assert (store.quota_bytes, store.max_upload_bytes) == (5, 7)
    default = logs_from_env({"GOTG_LOGS_DIR": str(tmp_path)})
    assert (default.quota_bytes, default.max_upload_bytes) == (262144000, 33554432)
