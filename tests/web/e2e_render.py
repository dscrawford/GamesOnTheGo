"""Headless-browser check of the SPA against the mock server (not collected by pytest: needs playwright).

    PLAYWRIGHT_BROWSERS_PATH=<browsers> PYTHONPATH=src:tests/web python tests/web/e2e_render.py [outdir]
"""

from __future__ import annotations

import sys
from pathlib import Path

from mock_server import serve
from playwright.sync_api import expect, sync_playwright

out = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/webui-shots")
out.mkdir(parents=True, exist_ok=True)
server = serve()
base = f"http://127.0.0.1:{server.server_port}"
errors: list[str] = []

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1100, "height": 800})
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: m.type == "error" and "Failed to load resource" not in m.text and errors.append(m.text))

    page.goto(f"{base}/ui/")
    expect(page.locator("#login")).to_be_visible()  # 401 -> sign-in prompt
    page.screenshot(path=out / "0-signin.png")

    page.goto(f"{base}/auth/login?as=admin&next=/ui/")
    expect(page.locator(".tile").first).to_be_visible()
    expect(page.locator("#count")).to_have_text("63 games")
    expect(page.locator(".tile")).to_have_count(48)  # one page
    expect(page.locator('nav a[data-nav="admin"]')).to_be_visible()
    expect(page.locator(".tile img").first).to_be_visible()  # art loaded
    page.screenshot(path=out / "1-browse.png")

    page.select_option("select[name=region]", "eur")  # eur + world
    expect(page.locator("#count")).to_have_text("2 games")
    page.select_option("select[name=region]", "")
    page.select_option("select[name=platform]", "snes")
    expect(page.locator("#count")).to_have_text("1 game")
    page.select_option("select[name=platform]", "")
    page.fill("input[aria-label=search]", "tetris")
    expect(page.locator("#count")).to_have_text("1 game")

    page.locator(".tile").first.click()
    expect(page.locator("h1")).to_have_text("Tetris")
    page.screenshot(path=out / "2-detail.png")

    page.goto(f"{base}/ui/#/game/n64/usa.game_00")
    expect(page.locator(".detail img")).to_be_visible()
    page.get_by_text("Saves for this game").click()
    expect(page.locator("[data-attr=env-n64-usa_game_00] tr[data-gen]")).to_have_count(2)
    page.screenshot(path=out / "3-saves.png")

    # Conflict: upload based on the stale head? The page's head is h2; make the mock's head move first.
    page.set_input_files("[data-attr=env-n64-usa_game_00] input[type=file]", {"name": "b.tar.zst", "mimeType": "application/zstd", "buffer": b"zz"})
    page.evaluate("fetch('/saves/env-n64-usa_game_00', {method:'PUT', body:'other', headers:{'X-Gotg-Parent':'h2'}})")
    page.get_by_text("Upload as new head").first.click()
    expect(page.get_by_role("alert")).to_contain_text("Conflict")
    page.screenshot(path=out / "4-conflict.png")
    page.get_by_text("Overwrite with mine").click()
    expect(page.locator("[data-attr=env-n64-usa_game_00] tr[data-gen]")).to_have_count(4)

    page.goto(f"{base}/ui/#/admin")
    expect(page.locator("#tokens tr")).to_have_count(3)
    expect(page.locator("#users tr")).to_have_count(3)
    page.fill("input[name=name]", "newbie")
    page.get_by_text("Mint invite").click()
    expect(page.locator("#code")).to_have_text("gotgi_MOCKCODE")
    page.once("dialog", lambda d: d.accept())
    page.get_by_role("button", name="Revoke").click()
    expect(page.locator("#tokens .bad")).to_have_count(2)
    page.screenshot(path=out / "5-admin.png")

    # A non-admin session sees no Admin tab and is refused the screen.
    page.goto(f"{base}/auth/login?as=bob&next=/ui/")
    expect(page.locator("#who")).to_have_text("bob")
    expect(page.locator('nav a[data-nav="admin"]')).to_be_hidden()
    page.goto(f"{base}/ui/#/admin")
    expect(page.locator(".banner")).to_contain_text("needs an admin")
    browser.close()

server.shutdown()
assert not errors, errors
print("ok; screenshots in", out)
