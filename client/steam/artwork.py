#!/usr/bin/env python3
"""Fetch artwork for a Steam shortcut from SteamGridDB.

Steam finds a non-Steam game's pictures by filename, keyed on the shortcut's
appid, in ``userdata/<user>/config/grid``. The five names below are transcribed
from EmuDeck's ``copy_steam_images()`` — the same set Steam ROM Manager writes —
rather than from documentation. A name Steam does not recognise is ignored in
silence, so these are the whole contract.

EmuDeck itself does not talk to SteamGridDB: it copies out of Steam ROM
Manager's cache, having had SRM do the fetching. This does that half directly,
which is a smaller thing than either of them and needs no GUI.

Everything here is best-effort. A shortcut with no picture is a working
shortcut; a failed download that took the shortcut with it would not be. Every
failure path prints why and exits 0.

Only the standard library, so the client gains no dependency for this.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_BASE_URL = "https://www.steamgriddb.com"

# kind on the API -> the filename Steam looks for, relative to the grid dir.
# The extension is what Steam expects, not what the download happens to be;
# it reads the bytes, and both PNG and JPEG work under either name.
ARTWORK = {
    "grids": "{appid}.jpg",  # banner / wide capsule
    "grids_portrait": "{appid}p.png",  # the library tile
    "heroes": "{appid}_hero.jpg",
    "logos": "{appid}_logo.png",
    "icons": "{appid}_icon.ico",
}

# The portrait tile is a grid too — the API distinguishes them by dimensions,
# not by endpoint, so it is one request with a filter rather than two kinds.
ENDPOINT = {
    "grids": ("grids", {"dimensions": "920x430,460x215"}),
    "grids_portrait": ("grids", {"dimensions": "600x900"}),
    "heroes": ("heroes", {}),
    "logos": ("logos", {}),
    "icons": ("icons", {}),
}

TIMEOUT = 20


def _get(url: str, api_key: str | None) -> bytes:
    request = urllib.request.Request(url)
    if api_key:
        request.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310
        return response.read()


def _get_json(url: str, api_key: str) -> dict:
    return json.loads(_get(url, api_key))


def find_game(base_url: str, api_key: str, name: str) -> dict | None:
    """The top search result, which is what "the top pick" means here."""
    url = f"{base_url}/api/v2/search/autocomplete/{urllib.parse.quote(name)}"
    data = _get_json(url, api_key).get("data") or []
    return data[0] if data else None


def best_asset(base_url: str, api_key: str, game_id: int, kind: str) -> str | None:
    """The highest-scoring asset of one kind.

    Sorted here rather than trusted from the response: the ordering is not
    promised by the API, and "first in the list" and "best" are not the same
    claim.
    """
    endpoint, params = ENDPOINT[kind]
    url = f"{base_url}/api/v2/{endpoint}/game/{game_id}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = _get_json(url, api_key).get("data") or []
    if not data:
        return None
    return max(data, key=lambda a: a.get("score", 0)).get("url")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grid-dir", required=True)
    parser.add_argument("--appid", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--api-key")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace artwork that is already there",
    )
    args = parser.parse_args(argv)

    def report(**fields) -> int:
        print(json.dumps(fields))
        return 0

    # No key is the ordinary case on a fresh machine, not a fault. An
    # unauthenticated request to the real API is a 401, so there is nothing to
    # try without one.
    if not args.api_key:
        return report(skipped="no api key")

    grid = Path(args.grid_dir)
    grid.mkdir(parents=True, exist_ok=True)

    try:
        game = find_game(args.base_url, args.api_key, args.name)
    except urllib.error.HTTPError as error:
        reason = "the api key was refused" if error.code == 401 else f"http {error.code}"
        return report(skipped=reason)
    except (urllib.error.URLError, OSError, ValueError) as error:
        return report(skipped=f"steamgriddb unreachable: {error}")

    if not game:
        return report(skipped="no match", name=args.name)

    downloaded, kept = [], []
    for kind, template in ARTWORK.items():
        dest = grid / template.format(appid=args.appid)
        if dest.exists() and not args.force:
            kept.append(dest.name)
            continue
        try:
            url = best_asset(args.base_url, args.api_key, game["id"], kind)
            if not url:
                continue
            body = _get(url, None)
        except (urllib.error.URLError, OSError, ValueError):
            # One missing picture is not a reason to abandon the other four.
            continue
        # Written whole and moved, so a half-downloaded file is never left
        # looking like artwork Steam should use.
        tmp = dest.with_suffix(dest.suffix + ".gotg-tmp")
        tmp.write_bytes(body)
        tmp.replace(dest)
        downloaded.append(dest.name)

    return report(
        game=game.get("name"),
        game_id=game.get("id"),
        downloaded=downloaded,
        kept=kept,
    )


if __name__ == "__main__":
    sys.exit(main())
