#!/usr/bin/env python3
"""Artwork from libretro-thumbnails, which needs no API key.

SteamGridDB is the better source when you have a key and it has the game, but
it needs a key, and Nintendo has had a good deal of Switch-era artwork taken
down from it. This is the fallback: a plain HTTP server of PNGs, no
registration, keyed by No-Intro filenames — which is how this library is
already named, so matching a catalog title to one is tractable.

There is no API. The Apache directory index *is* the search, so finding a game
means fetching the list of names for its system and matching against it. That
list is a few hundred kilobytes, so it is cached on disk.

Three things make the matching work, and all three were checked against the
live index rather than assumed:

  * No-Intro moves the article to the end — "Legend of Zelda, The - Majora's
    Mask" — so articles are dropped from both sides rather than reordered.
  * Region and revision live in parentheses, so those are stripped for
    comparison and read back afterwards to choose between the releases that
    remain.
  * The id already carries the region: usa.legend_of_zelda_majoras_mask. That
    is a better signal than anything in the title, and it is free.

Only the standard library.
"""

from __future__ import annotations

import html
import json
import re
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_BASE_URL = "https://thumbnails.libretro.com"

# The four folders every system has. The Steam name each becomes is decided
# later for box art, which is the only one whose shape varies.
BOXARTS, LOGOS, SNAPS, TITLES = (
    "Named_Boxarts",
    "Named_Logos",
    "Named_Snaps",
    "Named_Titles",
)

# The id's region prefix -> what No-Intro calls that region.
REGIONS = {
    "usa": "usa",
    "eur": "europe",
    "jpn": "japan",
    "world": "world",
    "aus": "australia",
}

# Releases that are not the one somebody means by the game's name.
ASIDES = (
    "beta",
    "debug",
    "demo",
    "proto",
    "sample",
    "kiosk",
    "virtual console",
    "gamecube",
    "aftermarket",
    "unl",
    "pirate",
)

ARTICLES = {"the", "a", "an"}

INDEX_TTL = 7 * 24 * 60 * 60  # a week; new box art is not urgent
TIMEOUT = 20


def _get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=TIMEOUT) as response:  # noqa: S310
        return response.read()


def normalize(name: str) -> str:
    """A name reduced to what two sources can be expected to agree on.

    Parentheses and brackets carry region, revision and language, none of which
    the catalog title has; articles are dropped rather than reordered because
    No-Intro moves them to the end and gotg's titles drop them entirely.
    """
    name = re.sub(r"\([^)]*\)|\[[^]]*\]", " ", name)
    name = name.lower().replace("&", " and ")
    name = re.sub(r"[^a-z0-9]+", " ", name)
    return " ".join(w for w in name.split() if w not in ARTICLES)


def rank(name: str, region: str) -> int:
    """How much this release looks like the one somebody means.

    Higher is better. Every candidate has already matched on name, so this is
    only choosing between releases of the same game.
    """
    tags = [t.lower() for t in re.findall(r"\(([^)]*)\)", name)]
    blob = " ".join(tags)
    score = 0
    if region and region in blob:
        score += 100
    elif region == "world" and ("usa" in blob or "world" in blob):
        # A gotg "world." id is a multi-region release, which No-Intro usually
        # files as "(Japan, USA)" or "(World)" rather than "(World)" alone.
        # Without this, Super Metroid takes the PAL box on a tie.
        score += 80
    elif "world" in blob:
        score += 50
    score -= 40 * sum(aside in blob for aside in ASIDES)
    # Fewest parenthesised qualifiers wins the tie: the plainest release of a
    # game is the one its box art belongs to.
    score -= len(tags)
    return score


def region_of(game_id: str) -> str:
    """The region the id already declares, if it declares one."""
    prefix = game_id.split(".")[0] if "." in game_id else ""
    return REGIONS.get(prefix, "")


def parse_index(body: str) -> list[str]:
    """The .png names out of an Apache directory listing.

    Percent-decoded and HTML-unescaped, in that order — Apache emits the href
    percent-encoded and the link text escaped, and a name like
    "Bomberman 64 - The Second Attack! (USA)" needs both undone to match.
    """
    names = []
    for match in re.finditer(r'<a href="([^"]+\.png)"', body):
        names.append(urllib.parse.unquote(html.unescape(match.group(1)))[:-4])
    return names


def load_index(base_url: str, system: str, cache_dir: Path | None) -> list[str]:
    """The names for one system, from disk when it was fetched recently."""
    cache = None
    if cache_dir is not None:
        cache = cache_dir / (re.sub(r"[^A-Za-z0-9]+", "_", system) + ".txt")
        if cache.exists() and time.time() - cache.stat().st_mtime < INDEX_TTL:
            return cache.read_text().splitlines()

    url = f"{base_url}/{urllib.parse.quote(system)}/{BOXARTS}/"
    names = parse_index(_get(url).decode("utf-8", "replace"))

    if cache is not None and names:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text("\n".join(names))
    return names


def find(names: list[str], title: str, game_id: str) -> str | None:
    """The best release whose name matches this title, or nothing."""
    wanted = normalize(title)
    candidates = [n for n in names if normalize(n) == wanted]
    if not candidates:
        return None
    return max(candidates, key=lambda n: rank(n, region_of(game_id)))


def png_size(body: bytes) -> tuple[int, int] | None:
    """Width and height out of a PNG header, or nothing if it is not one."""
    if len(body) < 24 or not body.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    width, height = struct.unpack(">II", body[16:24])
    return width, height


def fetch(
    base_url: str,
    system: str,
    matched: str,
    kinds: tuple[str, ...] = (BOXARTS, LOGOS, SNAPS, TITLES),
) -> dict[str, bytes]:
    """The bytes of each kind that exists for this release."""
    out: dict[str, bytes] = {}
    for kind in kinds:
        url = "{}/{}/{}/{}.png".format(
            base_url,
            urllib.parse.quote(system),
            kind,
            urllib.parse.quote(matched),
        )
        try:
            out[kind] = _get(url)
        except (urllib.error.URLError, OSError):
            # One missing picture is not a reason to abandon the others.
            continue
    return out


def plan(images: dict[str, bytes]) -> dict[str, bytes]:
    """Which Steam artwork kind each picture should become.

    Box art is the interesting one: a cartridge box is landscape and belongs in
    the wide capsule, a disc case is portrait and belongs in the library tile.
    Filing a 512x357 N64 box as a 600x900 tile would pillarbox it down the
    middle of the library, so the shape decides rather than the platform.
    """
    chosen: dict[str, bytes] = {}

    box = images.get(BOXARTS)
    if box:
        size = png_size(box)
        portrait = size is not None and size[1] > size[0]
        chosen["grids_portrait" if portrait else "grids"] = box

    if LOGOS in images:
        chosen["logos"] = images[LOGOS]
    # A screenshot is the closest thing here to a hero banner; the title screen
    # is the fallback, being the same shape and usually less representative.
    for kind in (SNAPS, TITLES):
        if kind in images:
            chosen["heroes"] = images[kind]
            break
    return chosen


def artwork(
    base_url: str,
    playlists: dict[str, str],
    platform: str,
    title: str,
    game_id: str,
    cache_dir: Path | None = None,
) -> tuple[dict[str, bytes], dict]:
    """Everything libretro has for one game, and why if it has nothing.

    Never raises: a shortcut with no picture is a working shortcut, and a
    failed download that took the shortcut with it would not be.
    """
    system = playlists.get(platform)
    if not system:
        return {}, {"skipped": f"libretro has no thumbnails for {platform}"}

    try:
        names = load_index(base_url, system, cache_dir)
    except (urllib.error.URLError, OSError, ValueError) as error:
        return {}, {"skipped": f"libretro unreachable: {error}"}

    matched = find(names, title, game_id)
    if not matched:
        return {}, {"skipped": "no match", "system": system}

    return plan(fetch(base_url, system, matched)), {"matched": matched, "system": system}


def load_playlists(path: Path) -> dict[str, str]:
    return json.loads(path.read_text()).get("playlists", {})
