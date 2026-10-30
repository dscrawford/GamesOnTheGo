"""The SPA's files over the real service, and the mock server the UI is developed against."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path[:0] = [str(Path(__file__).parent.parent / "service"), str(Path(__file__).parent)]
from harness import send, serve  # noqa: E402

from gotg.service.app import Config  # noqa: E402
from gotg.service.routes_static import STATIC_DIR, static_file  # noqa: E402

LEGACY = "legacy-token"


@pytest.fixture
def svc():
    with serve(Config(token=LEGACY)) as served:
        yield served


def test_the_index_is_served_without_a_token(svc):
    status, headers, body = svc.call("GET", "/ui/", token=None)
    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert b'src="js/main.js"' in body
    assert "script-src 'self'" in headers["Content-Security-Policy"]


@pytest.mark.parametrize("path", ["/ui/js/api.js", "/ui/style.css", "/ui/js/views/admin.js"])
def test_assets_carry_their_types(svc, path):
    status, headers, _ = svc.call("GET", path, token=None)
    assert status == 200
    assert headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.parametrize("path", ["/ui/../app.py", "/ui/%2e%2e/app.py", "/ui/js", "/ui/nope.js", "/ui/js/../../app.py"])
def test_nothing_outside_the_static_dir_is_served(svc, path):
    status, _, body = svc.call("GET", path, token=None)
    assert status in (400, 404)
    assert b"import" not in body


def test_only_get_reaches_the_static_route(svc):
    status, _, _ = svc.call("POST", "/ui/", token=None, body=b"x")
    assert status == 401


def test_the_service_api_still_wants_a_token(svc):
    assert svc.call("GET", "/catalog", token=None)[0] == 401


def test_static_file_refuses_a_symlink_out_of_the_root(tmp_path):
    (tmp_path / "root").mkdir()
    secret = tmp_path / "secret.js"
    secret.write_text("secret")
    (tmp_path / "root" / "link.js").symlink_to(secret)
    assert static_file("ui/link.js", tmp_path / "root") is None


def test_every_module_import_resolves_to_a_served_file():
    for js in STATIC_DIR.rglob("*.js"):
        for rel in re.findall(r'from "(\.[^"]+)"', js.read_text()):
            assert (js.parent / rel).resolve().is_file(), f"{js.name} imports missing {rel}"
    html = (STATIC_DIR / "index.html").read_text()
    for rel in re.findall(r'(?:src|href)="((?!#|http)[^"]+)"', html):
        assert (STATIC_DIR / rel).is_file(), rel


def test_no_inline_script_or_style_that_the_csp_would_block():
    html = (STATIC_DIR / "index.html").read_text()
    assert "<style" not in html and not re.search(r"<script(?![^>]*src=)", html) and " style=" not in html
    for js in STATIC_DIR.rglob("*.js"):
        assert "innerHTML" not in js.read_text(), js.name


def test_the_mock_serves_the_contract_the_ui_assumes():
    from mock_server import serve as mock

    server = mock()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        assert send(f"{base}/catalog", token=None)[0] == 401
        cookie = {"Cookie": "gotg_session=admin"}
        status, _, body = send(f"{base}/auth/whoami", token=None, headers=cookie)
        assert status == 200 and b'"admin": true' in body
        assert send(f"{base}/saves/env-n64-usa_game_00/meta", token=None, headers=cookie)[0] == 200
        listed = json.loads(send(f"{base}/saves", token=None, headers=cookie)[2])
        assert listed["saves"][0]["attr"] == "env-n64-usa_game_00" and listed["saves"][0]["generations"] == 2
        stale = {**cookie, "X-Gotg-Parent": "h1"}
        assert send(f"{base}/saves/env-n64-usa_game_00", "PUT", token=None, body=b"x", headers=stale)[0] == 409
    finally:
        server.shutdown()
        server.server_close()


def test_bare_ui_redirects_to_the_slash_form(svc):
    import http.client

    def get(path):
        conn = http.client.HTTPConnection(svc.url.removeprefix("http://"), timeout=10)
        conn.request("GET", path)
        reply = conn.getresponse()
        reply.read()
        return reply.status, reply.getheader("Location")

    assert get("/ui") == (302, "/ui/")
    assert get("/ui?x=1") == (302, "/ui/?x=1")
    assert get("/uix")[0] == 401


def test_ui_is_served_from_web_dir_when_set(tmp_path):
    (tmp_path / "index.html").write_text("<p>from the web dir</p>")
    with serve(Config(token=LEGACY, web_dir=str(tmp_path))) as served:
        status, _, body = served.call("GET", "/ui/", token=None)
        assert status == 200 and b"from the web dir" in body
        assert served.call("GET", "/ui/js/main.js", token=None)[0] == 404  # the packaged copy is not mixed in
        assert served.call("GET", "/ui/../secret.html", token=None)[0] in (400, 404)


def test_web_dir_must_exist(tmp_path):
    from gotg.service.cli import config_from_env

    with pytest.raises(ValueError, match="GOTG_WEB_DIR"):
        config_from_env({"GOTG_PROXY_TOKEN": "t", "GOTG_WEB_DIR": str(tmp_path / "missing")})
    assert config_from_env({"GOTG_PROXY_TOKEN": "t", "GOTG_WEB_DIR": str(tmp_path)}).web_dir == str(tmp_path)
