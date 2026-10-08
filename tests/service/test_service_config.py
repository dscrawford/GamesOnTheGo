"""Configuration: what the environment becomes, and what a Config refuses.

`cli.py` maps ~25 environment variables onto `Config` by hand and `validate`
guards the credential rules; neither had a test that named every field, so a
renamed variable or a swapped pair of keyword arguments (the url fields are
all strings, and the type checker cannot tell them apart) would have passed.
"""

from __future__ import annotations

import pytest

from gotg.service import cli
from gotg.service.config import Config

EVERYTHING = {
    "GOTG_PROXY_TOKEN": "client",
    "STEAMGRIDDB_API_KEY": "sgdb",
    "GOTG_UPSTREAM_CACHE_DIR": "/cache/up",
    "GOTG_ART_DIR": "/cache/art",
    "GOTG_UPSTREAM_RATE": "3.5",
    "GOTG_UPSTREAM_BURST": "7",
    "GOTG_UPSTREAM_WAIT": "1.25",
    "IGDB_CLIENT_ID": "igdb-id",
    "IGDB_CLIENT_SECRET": "igdb-secret",
    "GOTG_INDEX_TOKEN": "index",
    "GOTG_ADMIN_TOKEN": "admin",
    "GOTG_LIBRARY_TOKEN": "library",
    "GOTG_AUTH_URL": "http://api.internal:8080/",
    "GOTG_FILES_URL": "https://files.example/",
    "GOTG_FILES_PORT_FILE": "/run/forwarded_port",
    "GOTG_FILES_PREFERRED_URL": "http://100.64.0.2:30780/",
    "GOTG_LEGACY_USER": "daniel",
    "GOTG_ADMIN_URL": "http://100.64.0.1:30781/",
    "GOTG_PUBLIC_URL": "https://gotg.example/",
}


def test_every_variable_lands_in_its_own_field():
    config = cli.config_from_env(EVERYTHING)
    assert config.token == "client"
    assert config.steamgriddb_key == "sgdb"
    assert config.upstream_cache_dir == "/cache/up"
    assert config.art_dir == "/cache/art"
    assert (config.upstream_rate, config.upstream_burst, config.upstream_wait) == (3.5, 7.0, 1.25)
    assert (config.igdb_client_id, config.igdb_client_secret) == ("igdb-id", "igdb-secret")
    assert (config.index_token, config.admin_token, config.library_token) == ("index", "admin", "library")
    assert config.files_port_file == "/run/forwarded_port"
    assert config.legacy_user == "daniel"


def test_urls_lose_their_trailing_slash_and_only_urls_do():
    config = cli.config_from_env(EVERYTHING)
    assert config.auth_url == "http://api.internal:8080"
    assert config.files_url == "https://files.example"
    assert config.files_preferred_url == "http://100.64.0.2:30780"
    assert config.admin_url == "http://100.64.0.1:30781"
    assert config.public_url == "https://gotg.example"
    assert config.files_port_file == "/run/forwarded_port"


def test_a_bare_environment_is_the_defaults():
    config = cli.config_from_env({"GOTG_PROXY_TOKEN": "client"})
    assert config == Config(token="client")
    assert (config.upstream_rate, config.upstream_burst, config.upstream_wait) == (2.0, 10.0, 5.0)
    assert config.legacy_user == "legacy"


def test_no_client_token_means_no_start():
    with pytest.raises(ValueError, match="no client token"):
        cli.config_from_env({})


@pytest.mark.parametrize("name", ["GOTG_UPSTREAM_RATE", "GOTG_UPSTREAM_BURST", "GOTG_UPSTREAM_WAIT"])
@pytest.mark.parametrize(("raw", "message"), [("2O", "not a number"), ("-1", "negative")])
def test_a_pace_that_is_not_a_sane_number_is_refused_not_defaulted(name, raw, message):
    with pytest.raises(ValueError, match=message) as caught:
        cli.config_from_env({"GOTG_PROXY_TOKEN": "t", name: raw})
    assert name in str(caught.value)


def test_an_empty_pace_is_the_default_and_zero_is_zero():
    assert cli.config_from_env({"GOTG_PROXY_TOKEN": "t", "GOTG_UPSTREAM_RATE": ""}).upstream_rate == 2.0
    assert cli.config_from_env({"GOTG_PROXY_TOKEN": "t", "GOTG_UPSTREAM_RATE": "0"}).upstream_rate == 0.0


@pytest.mark.parametrize("raw", ["0", "65536", "-1", "http"])
def test_an_admin_port_outside_the_range_is_refused(raw):
    with pytest.raises(ValueError, match="GOTG_ADMIN_PORT"):
        cli.admin_port_from_env({"GOTG_ADMIN_PORT": raw})


def test_the_stores_follow_their_variables(tmp_path):
    assert cli.token_store_from_env({}) is None
    assert cli.store_from_env({}) is None
    tokens = cli.token_store_from_env({"GOTG_TOKENS_DB": str(tmp_path / "t.db")})
    assert tokens is not None and tokens.db == tmp_path / "t.db"
    saves = cli.store_from_env(
        {"GOTG_SAVES_DIR": str(tmp_path / "saves"), "GOTG_SAVES_KEEP": "4", "GOTG_SAVES_MAX_BYTES": "999"}
    )
    assert saves is not None
    assert (saves.root, saves.keep, saves.max_bytes) == (tmp_path / "saves", 4, 999)
    defaults = cli.store_from_env({"GOTG_SAVES_DIR": str(tmp_path / "saves2")})
    assert (defaults.keep, defaults.max_bytes) == (cli.DEFAULT_KEEP, cli.DEFAULT_MAX_BYTES)


def test_the_catalog_roots_are_a_colon_separated_list(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    catalog = cli.catalog_from_env(
        {"GOTG_CATALOG_DB": str(tmp_path / "state" / "c.db"), "GOTG_LIBRARY_ROOTS": f"{first}::{second}:"}
    )
    assert catalog is not None
    assert catalog.roots == [first, second]


# --- Config.validate: one credential rule per case ----------------------------


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"token": ""}, "no client token"),
        ({"files_url": "files.example"}, "GOTG_FILES_URL"),
        ({"files_url": "ftp://files.example"}, "GOTG_FILES_URL"),
        ({"files_preferred_url": "node1:30780"}, "GOTG_FILES_PREFERRED_URL"),
        ({"index_token": "client"}, "index token equals the client token"),
        ({"admin_token": "client"}, "admin token equals"),
        ({"index_token": "index", "admin_token": "index"}, "admin token equals"),
        ({"library_token": "client"}, "library token equals"),
        ({"index_token": "index", "library_token": "index"}, "library token equals"),
        ({"admin_token": "admin", "library_token": "admin"}, "library token equals"),
    ],
)
def test_validate_refuses(fields, message):
    base = {"token": "client"}
    with pytest.raises(ValueError, match=message):
        Config(**{**base, **fields}).validate()


def test_validate_accepts_four_distinct_credentials_and_returns_the_config():
    config = Config(
        token="a",
        index_token="b",
        admin_token="c",
        library_token="d",
        files_url="https://f.example",
        files_preferred_url="http://100.64.0.2:30780",
    )
    assert config.validate() is config


def test_an_unset_credential_never_collides_with_another_unset_one():
    # Two empty strings are equal; the rules must not read that as a clash.
    assert Config(token="a").validate().token == "a"
