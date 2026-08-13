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

Where a picture comes from is deliberately not the caller's business: each
place is an ImageSource whose whole surface is fetch(kind) -> bytes, and the
Artwork facade asks them in order until one answers. Today that order is:

  * SteamGridDB, which has assets cut to Steam's own shapes and is the better
    answer whenever it has the game. Reached directly with a personal key, or
    through the GOTG service holding the real one — the API is identical, so
    the source cannot tell and does not care.
  * libretro-thumbnails, which needs nothing at all and is keyed by No-Intro
    filenames. See libretro.py. It fills whatever the first source left, so a
    machine with no key still gets pictures.

A new database is a new class with a fetch(), not a change to anything that
calls one.

And ``--from``, a picture named on the command line, for the handful of games
in neither: libretro has no Switch playlist at all, and Nintendo has had some
Switch-era artwork taken down from SteamGridDB.

Everything here is best-effort. A shortcut with no picture is a working
shortcut; a failed download that took the shortcut with it would not be. Every
failure path prints why and exits 0.

Only the standard library, so the client gains no dependency for this.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import libretro

DEFAULT_BASE_URL = "https://www.steamgriddb.com"

# Sent on every request, because www.steamgriddb.com sits behind Cloudflare and
# Cloudflare answers the default "Python-urllib/3.x" with a 403 whatever the key
# says. A plain honest name is enough; nothing here pretends to be a browser.
USER_AGENT = "gotg/0.1.0"

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
    request.add_header("User-Agent", USER_AGENT)
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


# What a person calls each of the five, since "grids_portrait" is our word for
# it and not anybody else's.
KIND_NAMES = {
    "tile": "grids_portrait",
    "capsule": "grids",
    "hero": "heroes",
    "logo": "logos",
    "icon": "icons",
}


def jpeg_size(body: bytes) -> tuple[int, int] | None:
    """Width and height out of a JPEG's frame header.

    Walked marker by marker rather than guessed at an offset: the header is
    preceded by any number of variable-length segments, and only the SOF ones
    carry the dimensions.
    """
    if not body.startswith(b"\xff\xd8"):
        return None
    i = 2
    while i + 9 < len(body):
        if body[i] != 0xFF:
            i += 1
            continue
        marker = body[i + 1]
        # SOF0..SOF15, excluding the four that are not frame headers.
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height, width = struct.unpack(">HH", body[i + 5 : i + 9])
            return width, height
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        (length,) = struct.unpack(">H", body[i + 2 : i + 4])
        i += 2 + length
    return None


def image_size(body: bytes) -> tuple[int, int] | None:
    return libretro.png_size(body) or jpeg_size(body)


def kind_for_shape(width: int, height: int) -> str:
    """Which of the five a picture of this shape belongs in.

    Steam's own shapes are the boundaries: a 600x900 tile is 0.67 wide, a
    920x430 capsule is 2.14, and a 1920x620 hero is 3.1. Anything clearly
    taller than it is wide is a tile; anything much wider than a capsule is a
    hero; the broad middle is a capsule.
    """
    ratio = width / height
    if ratio < 0.9:
        return "grids_portrait"
    if ratio > 2.6:
        return "heroes"
    return "grids"


def read_source(source: str) -> bytes:
    """A local file or a URL — whichever the person had to hand."""
    if source.startswith(("http://", "https://")):
        return _get(source, None)
    return Path(source).read_bytes()


def from_manual(args, grid: Path) -> tuple[dict, int]:
    """A picture named on the command line.

    Unlike the two automatic sources this is not best-effort: somebody typed a
    path, so if it cannot be used they need to be told, not warned past.
    """
    try:
        body = read_source(args.source)
    except (urllib.error.URLError, OSError, ValueError) as error:
        return {"error": f"cannot read {args.source}: {error}"}, 1

    kind = KIND_NAMES.get(args.as_kind) if args.as_kind else None
    if kind is None:
        size = image_size(body)
        if size is None:
            return {
                "error": f"cannot tell the shape of {args.source}, so cannot tell "
                f"which picture it is. Say so with --as ({', '.join(KIND_NAMES)})."
            }, 1
        kind = kind_for_shape(*size)

    dest = grid / ARTWORK[kind].format(appid=args.appid)
    # No --force needed: naming a file is already the intent that --force
    # exists to express when a database chose for you.
    _write(dest, body)
    return {"source": args.source, "wrote": dest.name, "as": kind}, 0


def _write(dest: Path, body: bytes) -> None:
    """Written whole and moved, so a half-downloaded file is never left looking
    like artwork Steam should use."""
    tmp = dest.with_suffix(dest.suffix + ".gotg-tmp")
    tmp.write_bytes(body)
    tmp.replace(dest)


# --- where pictures come from ------------------------------------------------


class ImageSource(Protocol):
    """One place pictures come from.

    fetch() answers with the bytes of the best picture of that kind, or None —
    and None means "not from here", never an error: a source that cannot run
    at all says why in note() and answers None to everything. The caller's
    side of the contract is exactly these two methods; which database sits
    behind them is not its business.
    """

    def fetch(self, kind: str) -> bytes | None: ...

    def note(self) -> dict: ...


class SteamGridDBSource:
    """SteamGridDB — directly with a personal key, or through the GOTG service
    holding the real one. The API is identical either way, which is the point:
    the service *is* SteamGridDB as far as this class can tell."""

    def __init__(self, base_url: str, api_key: str | None, names: list[str]):
        self.base_url = base_url
        self.api_key = api_key
        # Tried in order: a mod's own title first, the game it is a mod of
        # behind it — rando grids when the database has them, the base game's
        # otherwise.
        self.names = names
        self._note: dict = {}
        self._game_id: int | None = None
        self._looked = False

    def _game(self) -> int | None:
        """The game, found once and remembered — including the not-found."""
        if self._looked:
            return self._game_id
        self._looked = True

        # No key is the ordinary case on a fresh machine, not a fault. An
        # unauthenticated request to the real API is a 401, so there is
        # nothing to try without one.
        if not self.api_key:
            self._note = {"skipped": "no api key"}
            return None
        game = None
        matched = None
        for name in self.names:
            try:
                game = find_game(self.base_url, self.api_key, name)
            except urllib.error.HTTPError as error:
                reason = "the api key was refused" if error.code == 401 else f"http {error.code}"
                self._note = {"skipped": reason}
                return None
            except (urllib.error.URLError, OSError, ValueError) as error:
                self._note = {"skipped": f"steamgriddb unreachable: {error}"}
                return None
            if game:
                matched = name
                break

        if not game:
            self._note = {"skipped": "no match", "names": self.names}
            return None
        self._note = {"game": game.get("name"), "game_id": game.get("id"), "matched": matched}
        self._game_id = game.get("id")
        return self._game_id

    def fetch(self, kind: str) -> bytes | None:
        game_id = self._game()
        if game_id is None or not self.api_key:
            return None
        try:
            url = best_asset(self.base_url, self.api_key, game_id, kind)
            if not url:
                return None
            return _get(url, None)
        except (urllib.error.URLError, OSError, ValueError):
            # One missing picture is not a reason to abandon the other four.
            return None

    def note(self) -> dict:
        return self._note


class LibretroSource:
    """libretro-thumbnails, which needs no key and knows games by their
    No-Intro names. Its images arrive as one batch, fetched on the first ask."""

    def __init__(
        self,
        base_url: str,
        playlists_file: Path,
        platform: str | None,
        name: str,
        game_id: str | None,
        cache_dir: Path | None,
    ):
        self.base_url = base_url
        self.playlists_file = playlists_file
        self.platform = platform
        self.name = name
        self.game_id = game_id
        self.cache_dir = cache_dir
        self._note: dict = {}
        self._images: dict[str, bytes] | None = None

    def _load(self) -> dict[str, bytes]:
        if self._images is not None:
            return self._images
        self._images = {}

        if not self.platform or not self.game_id:
            self._note = {"skipped": "no platform or id given"}
            return self._images
        try:
            playlists = libretro.load_playlists(self.playlists_file)
        except (OSError, ValueError) as error:
            self._note = {"skipped": f"cannot read {self.playlists_file}: {error}"}
            return self._images

        self._images, self._note = libretro.artwork(
            self.base_url, playlists, self.platform, self.name, self.game_id, self.cache_dir
        )
        return self._images

    def fetch(self, kind: str) -> bytes | None:
        return self._load().get(kind)

    def note(self) -> dict:
        return self._note


@dataclass
class Artwork:
    """The five pictures, from whichever source has each.

    fetch() asks the sources in order and returns the first answer along with
    who gave it — the caller needs the attribution only for its report, never
    to decide anything.
    """

    sources: list[ImageSource] = field(default_factory=list)

    def fetch(self, kind: str) -> tuple[bytes, ImageSource] | None:
        for source in self.sources:
            body = source.fetch(kind)
            if body is not None:
                return body, source
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grid-dir", required=True)
    # shortcuts.vdf carries the appid signed, but Steam keys the grid folder on
    # the unsigned 32-bit value — file art under the signed name and Steam
    # never finds it. Normalized here so every {appid} below is the name Steam
    # looks for, whichever way the shortcut spelled it.
    parser.add_argument("--appid", required=True, type=lambda s: int(s) & 0xFFFFFFFF)
    parser.add_argument("--name", required=True)
    parser.add_argument(
        "--fallback-name",
        help="the base game's title, tried when --name finds nothing",
    )
    parser.add_argument("--api-key")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    # The second source needs to know which system to look under and which
    # region the id declares; without them it simply does not run.
    parser.add_argument("--id", help="the gotg id, whose prefix names the region")
    parser.add_argument("--platform", help="the gotg platform, e.g. n64")
    parser.add_argument("--libretro-url", default=libretro.DEFAULT_BASE_URL)
    parser.add_argument("--playlists", help="platform -> libretro system table")
    parser.add_argument("--cache-dir", help="where to keep the fetched name lists")
    # The last resort, and the only one that always works: a picture found by
    # hand. Given one, neither source is consulted at all.
    parser.add_argument("--from", dest="source", help="a file or URL to use as-is")
    parser.add_argument(
        "--as",
        dest="as_kind",
        choices=sorted(KIND_NAMES),
        help="which picture --from is; inferred from its shape otherwise",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace artwork that is already there",
    )
    args = parser.parse_args(argv)

    grid = Path(args.grid_dir)
    grid.mkdir(parents=True, exist_ok=True)

    if args.source:
        report, code = from_manual(args, grid)
        print(json.dumps(report))
        return code

    names = [args.name]
    if args.fallback_name and args.fallback_name != args.name:
        names.append(args.fallback_name)
    sgdb = SteamGridDBSource(args.base_url, args.api_key, names)
    lr = LibretroSource(
        args.libretro_url,
        Path(args.playlists) if args.playlists else DEFAULT_PLAYLISTS,
        args.platform,
        # libretro is keyed by No-Intro names, which a mod's display title
        # never matches — the base game's title is always the right key.
        args.fallback_name or args.name,
        args.id,
        Path(args.cache_dir) if args.cache_dir else None,
    )
    artwork = Artwork([sgdb, lr])

    # The facade hides who answers; the report still says, because "where did
    # this picture come from" is a fair question for a person reading a log.
    downloaded: dict[int, list[str]] = {id(sgdb): [], id(lr): []}
    kept: list[str] = []
    for kind, template in ARTWORK.items():
        dest = grid / template.format(appid=args.appid)
        if dest.exists() and not args.force:
            kept.append(dest.name)
            continue
        found = artwork.fetch(kind)
        if found is None:
            continue
        body, source = found
        _write(dest, body)
        downloaded[id(source)].append(dest.name)

    report = {
        **sgdb.note(),
        "downloaded": downloaded[id(sgdb)],
        "kept": kept,
        "libretro": {**lr.note(), "downloaded": downloaded[id(lr)], "kept": []},
    }
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
