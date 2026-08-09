#!/usr/bin/env python3
"""Fetch artwork for a Steam shortcut.

Steam finds a non-Steam game's pictures by filename, keyed on the shortcut's
appid, in ``userdata/<user>/config/grid``. The five names below are transcribed
from EmuDeck's ``copy_steam_images()`` — the same set Steam ROM Manager writes —
rather than from documentation. A name Steam does not recognise is ignored in
silence, so these are the whole contract.

EmuDeck itself does not talk to SteamGridDB: it copies out of Steam ROM
Manager's cache, having had SRM do the fetching. This does that half directly,
which is a smaller thing than either of them and needs no GUI.

Two sources, tried in that order:

  * SteamGridDB, which has assets cut to Steam's own shapes and is the better
    answer whenever it has the game — but it needs an API key.
  * libretro-thumbnails, which needs nothing at all and is keyed by No-Intro
    filenames. See libretro.py. It fills whatever the first source left, so a
    machine with no key still gets pictures, and Switch titles — which libretro
    has none of, and whose SteamGridDB artwork Nintendo has largely had taken
    down — are the gap between them.

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

import libretro

DEFAULT_BASE_URL = "https://www.steamgriddb.com"

# Shipped beside this file, so the table travels with the code that reads it.
DEFAULT_PLAYLISTS = Path(__file__).resolve().parent.parent / "data" / "libretro-playlists.json"

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


def _write(dest: Path, body: bytes) -> None:
    """Written whole and moved, so a half-downloaded file is never left looking
    like artwork Steam should use."""
    tmp = dest.with_suffix(dest.suffix + ".gotg-tmp")
    tmp.write_bytes(body)
    tmp.replace(dest)


def from_steamgriddb(args, grid: Path, taken: set[str]) -> tuple[list[str], list[str], dict]:
    """The first source. Returns what it wrote, what it left, and why if it
    could not run at all."""
    # No key is the ordinary case on a fresh machine, not a fault. An
    # unauthenticated request to the real API is a 401, so there is nothing to
    # try without one.
    if not args.api_key:
        return [], [], {"skipped": "no api key"}

    try:
        game = find_game(args.base_url, args.api_key, args.name)
    except urllib.error.HTTPError as error:
        reason = "the api key was refused" if error.code == 401 else f"http {error.code}"
        return [], [], {"skipped": reason}
    except (urllib.error.URLError, OSError, ValueError) as error:
        return [], [], {"skipped": f"steamgriddb unreachable: {error}"}

    if not game:
        return [], [], {"skipped": "no match", "name": args.name}

    downloaded, kept = [], []
    for kind, template in ARTWORK.items():
        dest = grid / template.format(appid=args.appid)
        if dest.exists() and not args.force:
            kept.append(dest.name)
            taken.add(kind)
            continue
        try:
            url = best_asset(args.base_url, args.api_key, game["id"], kind)
            if not url:
                continue
            body = _get(url, None)
        except (urllib.error.URLError, OSError, ValueError):
            # One missing picture is not a reason to abandon the other four.
            continue
        _write(dest, body)
        downloaded.append(dest.name)
        taken.add(kind)

    return downloaded, kept, {"game": game.get("name"), "game_id": game.get("id")}


def from_libretro(args, grid: Path, taken: set[str]) -> dict:
    """The second source, filling only what the first did not."""
    if not args.platform or not args.id:
        return {"skipped": "no platform or id given"}

    playlists_file = Path(args.playlists) if args.playlists else DEFAULT_PLAYLISTS
    try:
        playlists = libretro.load_playlists(playlists_file)
    except (OSError, ValueError) as error:
        return {"skipped": f"cannot read {playlists_file}: {error}"}

    cache = Path(args.cache_dir) if args.cache_dir else None
    images, note = libretro.artwork(
        args.libretro_url, playlists, args.platform, args.name, args.id, cache
    )

    downloaded, kept = [], []
    for kind, body in images.items():
        dest = grid / ARTWORK[kind].format(appid=args.appid)
        # SteamGridDB already covered this shape, or a picture is already there.
        if kind in taken:
            continue
        if dest.exists() and not args.force:
            kept.append(dest.name)
            continue
        _write(dest, body)
        downloaded.append(dest.name)

    return {**note, "downloaded": downloaded, "kept": kept}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grid-dir", required=True)
    parser.add_argument("--appid", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--api-key")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    # The second source needs to know which system to look under and which
    # region the id declares; without them it simply does not run.
    parser.add_argument("--id", help="the gotg id, whose prefix names the region")
    parser.add_argument("--platform", help="the gotg platform, e.g. n64")
    parser.add_argument("--libretro-url", default=libretro.DEFAULT_BASE_URL)
    parser.add_argument("--playlists", help="platform -> libretro system table")
    parser.add_argument("--cache-dir", help="where to keep the fetched name lists")
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace artwork that is already there",
    )
    args = parser.parse_args(argv)

    grid = Path(args.grid_dir)
    grid.mkdir(parents=True, exist_ok=True)

    # Which kinds are settled, so the second source only fills the gaps.
    taken: set[str] = set()
    downloaded, kept, note = from_steamgriddb(args, grid, taken)
    report = {**note, "downloaded": downloaded, "kept": kept}
    report["libretro"] = from_libretro(args, grid, taken)

    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
