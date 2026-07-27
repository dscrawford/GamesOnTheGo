"""Env parsing per IMPORTER_SPEC.md §10 — the CronJob depends on these knobs."""

from pathlib import Path

import pytest

from gotg_importer.config import ConfigError, load

BASE_ENV = {
    "QBIT_URL": "http://qbittorrent.default.svc.cluster.local:8080",
    "QBIT_USER": "user",
    "QBIT_PASS": "pass",
    "GAMES_ROOT": "/data/Games",
    "SOURCE_ROOT": "/data/Torrents",
    "STATE_DIR": "/state",
}


def test_loads_the_cronjob_environment():
    cfg = load(BASE_ENV)
    assert cfg.games_root == Path("/data/Games")
    assert cfg.qbit_category == "games"  # default
    assert cfg.path_prefix == "/Games"  # default
    assert cfg.manifest_path == Path("/data/Games/.gotg/manifest.json")


def test_server_path_strips_the_mount_point():
    cfg = load(BASE_ENV)
    assert cfg.server_path("/data/Games/n64/usa.foo.z64") == "/Games/n64/usa.foo.z64"


@pytest.mark.parametrize("key", ["GAMES_ROOT", "SOURCE_ROOT", "STATE_DIR"])
def test_missing_paths_fail_fast(key):
    env = {k: v for k, v in BASE_ENV.items() if k != key}
    with pytest.raises(ConfigError, match=key):
        load(env)


def test_relative_paths_are_rejected():
    with pytest.raises(ConfigError, match="absolute"):
        load({**BASE_ENV, "GAMES_ROOT": "data/Games"})


def test_games_root_may_not_equal_source_root():
    with pytest.raises(ConfigError, match="differ"):
        load({**BASE_ENV, "SOURCE_ROOT": "/data/Games"})


def test_qbit_credentials_optional_for_bootstrap():
    env = {k: v for k, v in BASE_ENV.items() if not k.startswith("QBIT")}
    cfg = load(env, require_qbit=False)
    assert cfg.qbit_user == ""


def test_qbit_url_must_be_http():
    with pytest.raises(ConfigError, match="http"):
        load({**BASE_ENV, "QBIT_URL": "qbittorrent:8080"})
