"""Same-origin by default; CORS only for origins the operator lists."""

from __future__ import annotations

import pytest
from harness import send, serve

from gotg.service.app import Config
from gotg.service.cli import config_from_env

ORIGIN = "https://play.example.net"


def served(**kwargs):
    return serve(Config(token="client-token", admin_token="admin-token", **kwargs))


def test_no_cors_headers_by_default():
    with served() as s:
        _, headers, _ = s.call("GET", "/healthz", headers={"Origin": ORIGIN})
        assert not any(h.lower().startswith("access-control") for h in headers)
        assert headers["X-Content-Type-Options"] == "nosniff"
        status, headers, _ = send(f"{s.url}/x", method="OPTIONS", token=None, headers={"Origin": ORIGIN})
        assert status == 204 and "Access-Control-Allow-Methods" not in headers


def test_listed_origin_is_echoed_and_exposes_range_headers():
    with served(cors_origins=(ORIGIN,)) as s:
        _, headers, _ = s.call("GET", "/catalog", headers={"Origin": ORIGIN})
        assert headers["Access-Control-Allow-Origin"] == ORIGIN
        assert headers["Vary"] == "Origin"
        assert "Content-Range" in headers["Access-Control-Expose-Headers"]
        _, other, _ = s.call("GET", "/catalog", headers={"Origin": "https://evil.example"})
        assert "Access-Control-Allow-Origin" not in other


def test_preflight_needs_no_token_and_allows_range():
    with served(cors_origins=(ORIGIN,)) as s:
        status, headers, body = send(
            f"{s.url}/games/n64/usa.x/usa.x.z64",
            method="OPTIONS",
            token=None,
            headers={"Origin": ORIGIN, "Access-Control-Request-Method": "GET"},
        )
        assert status == 204 and body == b""
        assert headers["Access-Control-Allow-Origin"] == ORIGIN
        assert "Range" in headers["Access-Control-Allow-Headers"]
        assert "GET" in headers["Access-Control-Allow-Methods"]


def test_wildcard_and_admin_exclusion():
    with served(cors_origins=("*",)) as s:
        _, headers, _ = s.call("GET", "/catalog", headers={"Origin": ORIGIN})
        assert headers["Access-Control-Allow-Origin"] == "*" and "Vary" not in headers
        _, admin, _ = s.call("GET", "/admin/tokens", headers={"Origin": ORIGIN}, token="admin-token")
        assert "Access-Control-Allow-Origin" not in admin


@pytest.mark.parametrize("bad", ["play.example.net", "https://play.example.net/"])
def test_bad_origins_refuse_to_start(bad):
    with pytest.raises(ValueError, match="GOTG_CORS_ORIGINS"):
        Config(token="t", cors_origins=(bad,)).validate()


def test_env_parsing():
    config = config_from_env({"GOTG_PROXY_TOKEN": "t", "GOTG_CORS_ORIGINS": f"{ORIGIN}, http://localhost:5173"})
    assert config.cors_origins == (ORIGIN, "http://localhost:5173")
