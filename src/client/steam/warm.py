#!/usr/bin/env python3
"""Fill the service's art cache, once, for everybody.

Every machine used to resolve its own tile pictures: a SteamGridDB search, an
asset query and a download, per game, per laptop. On this library that is 5674
games times however many machines, against one shared key — for answers that
are the same every time, and are "no" for most of the set because libretro has
no Switch playlist and plenty of No-Intro names match nothing.

So one run of this resolves the whole catalog slowly and deliberately and
writes each result to the service (`PUT /art/...`), including the misses.
After that a client's grid is a download from our own service and no upstream
hears from it at all.

**Being slow is the feature.** The pace is one game at a time with a delay
between them — SteamGridDB's key is not ours to spend, and libretro's server
is somebody's donated bandwidth. A run that takes three hours and is resumable
beats one that takes ten minutes and gets the key banned. Everything already
in the service's index is skipped, so a run picks up where the last one
stopped, and `--limit` makes a long warm into a series of short ones.

**A miss is only written down when the answer was really "no".** A 429, a
timeout or a refused key are not answers — recording them as misses would
poison the cache for every client permanently, and a bad ten minutes would
cost the library its art. Those defer instead, and the next run tries again.

Resolution itself is artwork.py's, unchanged: the same two sources in the same
order that `gotg steam art` and the UI grid use. A second implementation would
be a second thing to keep in step with an API neither of us controls.

Only the standard library.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import artwork
import libretro

# Portrait first, then the wide capsule — the grid's order (gotg_ui/fetch.py),
# because these are the pictures the grid will draw. libretro files box art by
# its shape and a cartridge box is landscape, so asking only for the portrait
# would find nothing for most of a cartridge library.
KINDS = ("grids_portrait", "grids")

USER_AGENT = "gotg-warm/0.1.0"
TIMEOUT = 30

# Games per second, not requests: each one is a search, an asset query and a
# download, so the request rate is a few times this. Slow on purpose.
DEFAULT_RATE = 0.5

# Consecutive failures that mean the run is broken rather than unlucky — a
# refused key, a service that is down — at which point stopping is better than
# grinding through 5000 games writing nothing.
GIVE_UP_AFTER = 10

# What a source says when it did not get an answer, as opposed to getting the
# answer "no". Substrings of artwork.py's and libretro.py's own notes.
#
# A 503 is deliberately not here: it is this proxy saying it holds no
# SteamGridDB key at all, which is a deployment that warms from libretro
# alone, not a bad minute to wait out.
TRANSIENT = ("unreachable", "http 429", "http 500", "http 502", "http 504")

# A key the upstream will not take. Not transient and not an answer either:
# carrying on would write "nobody has art for this" across the whole library
# on the strength of a credential problem, and those misses are permanent for
# every client. So the run stops instead.
# artwork.py's own words for a 401, matched in full: "refused" alone also
# matches "Connection refused", which is the transient case and the opposite
# decision.
FATAL = "the api key was refused"


def clean_no(sources) -> bool:
    """Whether "no picture" was an answer rather than a failure.

    The distinction is the whole reason this is not two lines: a miss written
    here is permanent for the entire fleet, so it is only written when every
    source actually looked and actually found nothing.
    """
    for source in sources:
        skipped = str(source.note().get("skipped", ""))
        if FATAL in skipped:
            raise SystemExit(f"stopping: {skipped}. No misses were written for what was not asked.")
        if any(mark in skipped for mark in TRANSIENT):
            return False
    return True


def request(url: str, token: str, *, data: bytes | None = None, method: str | None = None) -> tuple[int, bytes, dict]:
    """One call to the service. Never raises for an HTTP status: a 404 from
    /art is an ordinary answer here, not an exception."""
    call = urllib.request.Request(url, data=data, method=method)
    call.add_header("Authorization", f"Bearer {token}")
    call.add_header("User-Agent", USER_AGENT)
    if data is not None:
        call.add_header("Content-Type", "application/octet-stream")
    try:
        with urllib.request.urlopen(call, timeout=TIMEOUT) as response:  # noqa: S310
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as error:
        return error.code, error.read(), dict(error.headers or {})


# The three ways /art says no, and what each one actually means. A bare status
# code sends somebody looking at the wrong thing: 404 here is not a missing
# game, it is a service too old to have an art cache at all.
REFUSALS = {
    401: "the index token was refused. Check it against the secret: kubectl get secret gotg-api -o jsonpath='{.data.index-token}' | base64 -d",
    403: "that token may read art but not write it; warming needs the index token",
    404: "this service has no /art at all — it is running a build from before the art cache. Deploy the current service first",
    503: "this service holds no art cache. Give it a volume and set GOTG_ART_DIR",
}


def known(service: str, token: str) -> set[tuple[str, str]]:
    """Everything the service already has an answer for — pictures and misses
    alike. One request, so resuming costs nothing."""
    status, body, _ = request(f"{service}/art", token)
    if status != 200:
        detail = REFUSALS.get(status) or body[:200].decode(errors="replace")
        raise SystemExit(f"the service would not give its art index (http {status}): {detail}")
    index = json.loads(body)
    seen = set(index.get("art", {})) | set(index.get("misses", []))
    return {tuple(key.split("/", 1)) for key in seen if "/" in key}


def catalog(service: str, token: str, path: Path | None) -> list[dict]:
    """The games to warm: the service's own catalog, or a manifest file.

    The service's is the default because it is the one that is definitely
    current — warming from a laptop's stale `gotg refresh` cache would quietly
    skip everything imported since.
    """
    if path is not None:
        return json.loads(path.read_text()).get("games", [])
    status, body, _ = request(f"{service}/catalog", token)
    if status != 200:
        raise SystemExit(f"the service would not give its catalog: http {status} {body[:200].decode(errors='replace')}")
    return json.loads(body).get("games", [])


def resolve(game: dict, service: str, token: str, names_cache: Path, playlists: Path) -> tuple[bytes | None, bool]:
    """One game's picture, and whether "none" was an answer.

    (bytes, True)  — got one
    (None,  True)  — nobody has art for this game; write the miss down
    (None,  False) — we could not find out; leave it for the next run
    """
    sources = [
        # Through the service, which holds the real key and caches the answer
        # for the fleet — so even this run's own questions are asked once. The
        # override is the same knob the grid reads, and the reason the tests
        # can stand a mock in front of either source.
        artwork.SteamGridDBSource(
            os.environ.get("GOTG_STEAMGRIDDB_URL") or f"{service}/steamgriddb",
            token,
            [game["title"]],
            prefer_thumb=True,
        ),
        artwork.LibretroSource(
            base_url=os.environ.get("GOTG_LIBRETRO_URL") or libretro.DEFAULT_BASE_URL,
            playlists_file=playlists,
            platform=game["platform"],
            name=game["title"],
            game_id=game["id"],
            cache_dir=names_cache,
        ),
    ]
    facade = artwork.Artwork(sources=sources)
    for kind in KINDS:
        try:
            found = facade.fetch(kind)
        except Exception as error:  # noqa: BLE001 — one odd game must not end the run
            print(f"  ! {game['platform']}/{game['id']}: {error}", file=sys.stderr)
            return None, False
        if found:
            return found[0], True
    return None, clean_no(sources)


def store(service: str, token: str, game: dict, body: bytes | None) -> tuple[bool, str]:
    """Write one answer to the service, waiting out a 429 rather than
    dropping it: being told to slow down is the system working."""
    where = f"{service}/art/{urllib.parse.quote(game['platform'])}/{urllib.parse.quote(game['id'])}"
    url = where if body is not None else f"{where}?miss=1"
    for attempt in range(3):
        status, reply, headers = request(url, token, data=body if body is not None else b"", method="PUT")
        if status == 200:
            return True, "art" if body is not None else "miss"
        if status == 429:
            time.sleep(min(float(headers.get("Retry-After", 1) or 1), 30) + attempt)
            continue
        return False, f"http {status}: {reply[:120].decode(errors='replace')}"
    return False, "throttled"


def warm(args) -> dict:
    service = args.service.rstrip("/")
    names_cache = Path(args.names_cache)
    names_cache.mkdir(parents=True, exist_ok=True)

    games = catalog(service, args.token, Path(args.catalog) if args.catalog else None)
    if args.platform:
        games = [game for game in games if game["platform"] in args.platform]
    skip = set() if args.refresh else known(service, args.token)
    todo = [game for game in games if (game["platform"], game["id"]) not in skip]
    if args.limit:
        todo = todo[: args.limit]

    tally = {"catalog": len(games), "known": len(games) - len(todo), "art": 0, "misses": 0, "deferred": 0, "errors": 0}
    if args.dry_run:
        print(f"would warm {len(todo)} of {len(games)} games", file=sys.stderr)
        return tally

    # The pace, kept as a deadline rather than a flat sleep so the time a slow
    # upstream already took counts towards it.
    interval = 1.0 / args.rate if args.rate > 0 else 0.0
    next_at = time.monotonic()
    consecutive = 0
    for done, game in enumerate(todo, start=1):
        delay = next_at - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        # A little jitter, so a warm started by a cron on three machines does
        # not arrive at the upstream in lockstep.
        next_at = time.monotonic() + interval * random.uniform(0.9, 1.3)  # noqa: S311 — pacing, not secrets

        body, answered = resolve(game, service, args.token, names_cache, Path(args.playlists))
        if not answered:
            tally["deferred"] += 1
            consecutive += 1
            if consecutive >= GIVE_UP_AFTER:
                print(f"giving up after {consecutive} in a row that could not be answered", file=sys.stderr)
                break
            continue
        stored, note = store(service, args.token, game, body)
        if not stored:
            tally["errors"] += 1
            consecutive += 1
            print(f"  ! {game['platform']}/{game['id']}: {note}", file=sys.stderr)
            if consecutive >= GIVE_UP_AFTER:
                print(f"giving up after {consecutive} failures in a row", file=sys.stderr)
                break
            continue
        consecutive = 0
        tally["art" if body is not None else "misses"] += 1
        if args.verbose or done % 50 == 0:
            print(f"  {done}/{len(todo)} {game['platform']}/{game['id']}: {note}", file=sys.stderr)
    return tally


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--service", required=True, help="the GOTG service url")
    parser.add_argument("--catalog", help="a manifest.json to read instead of asking the service")
    parser.add_argument("--playlists", default=str(artwork.DEFAULT_PLAYLISTS))
    parser.add_argument(
        "--names-cache",
        required=True,
        help="where libretro's per-system name indexes are kept, so one system is listed once per run",
    )
    parser.add_argument("--rate", type=float, default=DEFAULT_RATE, help=f"games per second (default {DEFAULT_RATE})")
    parser.add_argument("--limit", type=int, default=0, help="stop after this many, for a run on a timer")
    parser.add_argument("--platform", action="append", help="only this platform; repeatable")
    parser.add_argument("--refresh", action="store_true", help="look again at games the service already knows")
    parser.add_argument("--dry-run", action="store_true", help="say how much there is to do, ask nobody")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    if args.rate < 0:
        parser.error("--rate cannot be negative")
    # From the environment, never a flag: /proc/<pid>/cmdline is
    # world-readable, which is the same reason the client hands curl its
    # bearer on stdin.
    # Stripped: a token read out of a Kubernetes secret arrives with whatever
    # whitespace the secret was written with, and a trailing newline is an
    # unrecognized bearer rather than a recognizable mistake.
    args.token = os.environ.get("GOTG_INDEX_TOKEN", "").strip()
    if not args.token:
        parser.error("GOTG_INDEX_TOKEN is not set; writing art is the index token's job")

    try:
        tally = warm(args)
    except KeyboardInterrupt:
        # Resumable by construction, so an interrupted run is a finished run
        # that did less.
        print("\nstopped; the service keeps what was already written", file=sys.stderr)
        return 130
    print(json.dumps(tally))
    return 0


if __name__ == "__main__":
    sys.exit(main())
