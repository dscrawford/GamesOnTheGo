"""The browser player: the platform mapping, the plan route, the static page."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from harness import CLIENT_TOKEN, serve

from gotg.saves import ATTR_RE
from gotg.service import play_page
from gotg.service.app import Config

RULES = Path(__file__).parents[2] / "src/gotg/indexer/rules.yaml"
CONFIG = Path(__file__).parents[2] / "config/controllers"


def test_every_mapped_platform_is_a_real_platform_shape():
    for slug, core in play_page.CORES.items():
        assert re.match(r"^[a-z0-9][a-z0-9_-]{0,15}$", slug)
        assert core.system and core.core and core.exts


def test_the_mapped_systems_that_the_indexer_knows_are_the_wasm_ones():
    rules = RULES.read_text()
    known = set(re.findall(r"platform: (\w+)", rules))
    # Everything the indexer emits is either mapped or sent to the launcher; the
    # heavy ones are deliberately not mapped.
    for slug in ("gamecube",):
        assert slug in known and slug not in play_page.CORES
    for slug in ("nes", "snes", "gb", "gba", "genesis", "n64"):
        assert slug in known and slug in play_page.CORES
    assert play_page.CORES["gb"].system == play_page.CORES["gbc"].system


def test_every_platform_the_indexer_emits_is_browser_played_or_unsupported():
    from gotg.indexer.plan import DAT_DIR_PLATFORM, EXT_PLATFORM

    emitted = set(re.findall(r"platform: (\w+)", RULES.read_text()))
    emitted |= {platform for platform, _ in DAT_DIR_PLATFORM.values()} | set(EXT_PLATFORM.values())
    unplaced = emitted - set(play_page.CORES) - set(play_page.UNSUPPORTED)
    assert not unplaced, f"decide browser or unsupported for: {sorted(unplaced)}"
    assert {"gamecube", "wiiu", "switch"} <= set(play_page.UNSUPPORTED)
    assert not set(play_page.CORES) & set(play_page.UNSUPPORTED)


def test_unsupported_systems_get_the_launcher_and_a_reason():
    plan = play_page.play_plan("switch", "usa.x")
    assert plan["mode"] == "nix" and plan["unsupported"] is True
    assert "browser" in plan["reason"] and plan["nix"] == "nix run gotg#switch.usa.x"
    other = play_page.play_plan("ps2", "usa.x")
    assert other["mode"] == "nix" and other["unsupported"] is False and other["reason"]


def test_nix_packages_the_cores_from_the_same_file():
    systems = json.loads(play_page.SYSTEMS_FILE.read_text())
    assert set(systems) >= {"browser", "unsupported"}
    for slug, entry in systems["browser"].items():
        assert set(entry) <= {"system", "core", "exts", "note", "experimental"}, slug
        assert entry["core"] == play_page.CORES[slug].core
    nix = (Path(__file__).parents[2] / "nix/emulatorjs.nix").read_text()
    assert "../src/gotg/service/play_systems.json" in nix and "systems.browser" in nix


def test_gbc_is_a_platform_the_controller_config_names():
    assert "gbc" in (CONFIG / "gameboy.yaml").read_text()


def test_plan_for_a_browser_system():
    plan = play_page.play_plan("snes", "usa.super_metroid")
    assert plan["mode"] == "browser"
    assert plan["system"] == "snes"
    assert plan["nix"] == "nix run gotg#snes.usa.super_metroid"
    assert ATTR_RE.match(plan["save_attr"])
    assert plan["save_attr"] == "env-web-snes-usa_super_metroid"


def test_plan_for_a_launcher_system():
    plan = play_page.play_plan("gamecube", "usa.luigis_mansion")
    assert plan == {
        "platform": "gamecube",
        "id": "usa.luigis_mansion",
        "nix": "nix run gotg#gamecube.usa.luigis_mansion",
        "mode": "nix",
        "unsupported": True,
        "reason": play_page.UNSUPPORTED["gamecube"],
    }


@pytest.mark.parametrize(
    "platform,game_id", [("../x", "usa.a"), ("snes", "nodot"), ("SNES", "usa.a"), ("snes", "usa.a/b")]
)
def test_plan_refuses_names_that_are_not_catalog_shaped(platform, game_id):
    assert play_page.play_plan(platform, game_id) is None


@pytest.fixture
def served(tmp_path):
    ejs = tmp_path / "ejs"
    (ejs / "data").mkdir(parents=True)
    (ejs / "data" / "loader.js").write_text("// loader")
    (tmp_path / "secret.txt").write_text("nope")
    with serve(Config(token=CLIENT_TOKEN), ejs_dir=ejs) as s:
        yield s


def test_plan_route_needs_a_token(served):
    assert served.call("GET", "/play/plan/snes/usa.x", token=None)[0] == 401


def test_plan_route_answers(served):
    status, _, body = served.call("GET", "/play/plan/n64/usa.x")
    assert status == 200
    plan = json.loads(body)
    assert plan["mode"] == "browser" and plan["experimental"] is True
    assert json.loads(served.call("GET", "/play/plan/switch/usa.x")[2])["mode"] == "nix"
    assert served.call("GET", "/play/plan/n64/bad")[0] == 404
    assert served.call("POST", "/play/plan/n64/usa.x")[0] == 404


def test_page_is_served_without_a_token_with_a_strict_policy(served):
    import http.client

    conn = http.client.HTTPConnection(served.url.removeprefix("http://"), timeout=10)
    conn.request("GET", "/play?platform=nes&id=usa.x")
    reply = conn.getresponse()
    reply.read()
    # Relative, so a path prefix in front of the service survives the redirect.
    assert (reply.status, reply.getheader("Location")) == (302, "play/?platform=nes&id=usa.x")
    for path in ("/play/", "/play/app.js", "/play/app.css"):
        status, headers, body = served.call(
            "GET", path + ("?platform=nes&id=usa.x" if path == "/play/" else ""), token=None
        )
        assert status == 200 and body
        assert "script-src 'self'" in headers["Content-Security-Policy"]
        assert "http" not in headers["Content-Security-Policy"]


def test_policy_allows_the_eval_that_emulatorjs_core_extraction_needs():
    # Every EmulatorJS core is a 7z; its extractor (compression/extract7z.js)
    # calls eval() in a blob worker that inherits this policy. Without
    # 'unsafe-eval' Chromium hangs at "Decompress Game Core" (seen in a browser).
    script_src = next(d for d in play_page.POLICY.split(";") if d.strip().startswith("script-src"))
    assert "'unsafe-eval'" in script_src and "'wasm-unsafe-eval'" in script_src
    assert "http" not in play_page.POLICY


def test_script_saves_through_emulatorjs_events_that_exist():
    # EmulatorJS 4.2.3 has no EJS_onSaveUpdate; battery saves arrive on the
    # emulator's "saveSaveFiles" event, the only one still firing on unload.
    script = play_page.routes()["play/app.js"][0].decode()
    assert "EJS_onSaveUpdate" not in script
    assert 'on("saveSaveFiles"' in script
    assert "plan.core" in script


def test_player_runs_on_the_session_cookie_and_offers_sign_in():
    # No token is required up front: same-origin fetches carry a signed-in
    # browser's cookie, and only a 401 asks for sign-in (link) or a token.
    script = play_page.routes()["play/app.js"][0].decode()
    page = play_page.routes()["play/"][0].decode()
    assert "if (!token)" not in script
    assert '(token ? { Authorization: "Bearer " + token } : {})' in script
    assert 'at("/auth/login?next=") + encodeURIComponent(location.pathname + location.search)' in script
    assert 'sessionStorage.getItem("gotg.bearer")' in script  # a token pasted in the web UI carries over
    assert 'id="sso"' in page


def test_policy_adds_only_the_origins_of_http_byte_hosts():
    connect = lambda hosts: next(d for d in play_page.policy(hosts).split(";") if "connect-src" in d)  # noqa: E731
    assert play_page.policy([]) == play_page.POLICY
    assert "https://files.example:45581 " in connect(["https://files.example:45581/some/path"])
    assert "http://100.70.0.1:30780 " in connect(["http://100.70.0.1:30780"])
    for junk in ("javascript:alert(1)", "https://evil.test; script-src *", "ftp://x.test", "https://"):
        assert play_page.policy([junk]) == play_page.POLICY, junk


def test_page_policy_allows_the_advertised_byte_host(tmp_path):
    config = Config(token=CLIENT_TOKEN, files_url="https://files.example:45581")
    with serve(config) as s:
        _, headers, _ = s.call("GET", "/play/?platform=nes&id=usa.x", token=None)
    assert "connect-src 'self' https://files.example:45581 " in headers["Content-Security-Policy"]


def test_script_fetches_the_rom_from_the_catalogs_byte_hosts():
    script = play_page.routes()["play/app.js"][0].decode()
    assert "catalog.files_urls" in script
    assert '"/auth/bearer"' in script  # a cookie session's bearer for a cross-origin host
    assert 'credentials: base ? "omit"' in script  # this site's cookie never goes to a byte host


def test_player_reaches_the_service_through_its_base_only():
    script = play_page.routes()["play/app.js"][0].decode()
    page = play_page.routes()["play/"][0].decode()
    assert "const BASE = location.pathname.replace(" in script
    for match in re.finditer(r"""["'`](/(?:catalog|games|saves|art|auth|play)\b[^"'`]*)""", script):
        context = script.splitlines()[script[: match.start()].count("\n")]
        # "/games/..." is built into `path` first, then fetched as at(path) or base + path.
        assert "at(" in context or "const path = " in context, context.strip()
    assert 'src="app.js"' in page and 'href="app.css"' in page and 'href="/' not in page


def test_script_uses_the_saves_api_with_parent_and_pull_first():
    script = play_page.routes()["play/app.js"][0].decode()
    assert "X-Gotg-Parent" in script and "/saves/" in script and "/games/" in script
    assert script.index("lastFiles = await pull()") < script.index("start(await download")


def test_ejs_assets_are_served_from_the_directory_only(served):
    assert served.call("GET", "/play/ejs/data/loader.js", token=None)[2] == b"// loader"
    for bad in ("/play/ejs/../secret.txt", "/play/ejs/%2e%2e/secret.txt", "/play/ejs/data", "/play/ejs/nothing.js"):
        assert served.call("GET", bad, token=None)[0] == 404


def test_without_an_ejs_directory_the_assets_say_so():
    with serve(Config(token=CLIENT_TOKEN)) as s:
        status, _, body = s.call("GET", "/play/ejs/data/loader.js", token=None)
        assert status == 404 and b"browser-play" in body


def test_admin_listener_has_no_player():
    with serve(Config(token=CLIENT_TOKEN, admin_token="a" * 20), listener="admin") as s:
        assert s.call("GET", "/play/", token=None)[0] == 404


def test_player_names_the_control_scheme_and_follows_a_phone_turned_sideways():
    script = (Path(play_page.__file__).parent / "static/play/app.js").read_text()
    # EmulatorJS would guess the system from the core, and genesis_plus_gx is
    # listed under the Master System first: no Start button for Genesis games.
    assert "window.EJS_controlScheme = plan.system;" in script
    assert {"genesis": "segaMD"}.items() <= {p: c.system for p, c in play_page.CORES.items()}.items()
    # On-screen buttons on any touch screen, not just phone-looking user agents.
    assert '"virtual-gamepad"] = "enabled"' in script
    # Sideways on a touch screen: the game is the whole viewport, then fullscreen.
    assert "@media (orientation: landscape) and (pointer: coarse)" in play_page.STYLE
    assert "body.playing #game" in play_page.STYLE
    assert 'requestFullscreen({ navigationUI: "hide" })' in script
