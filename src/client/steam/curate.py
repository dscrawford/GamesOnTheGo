#!/usr/bin/env python3
"""Fixing what the warmer got wrong.

The service's art cache is the source of truth: one answer per game, held in
one place, and every grid draws what it says — including the misses, which are
recorded once for the whole fleet on purpose. That is the right trade for
nine thousand games, and it makes being wrong a shared problem. A tile with
somebody else's box art, or a miss on a game SteamGridDB files under a name
the matcher did not recognize, is wrong everywhere at once and cannot be
fixed by the person looking at it.

So it is fixed here instead, by whoever holds the index token:

    search   what SteamGridDB has under a name, and what the assets look like
    set      this picture, from a file or a url, is the one
    show     what the cache currently holds for a game
    forget   drop it, so the next warm looks again

Searching goes through the service's proxy, so this needs no SteamGridDB key
of its own — the same reverse proxy the warmer and `gotg steam art` use.

Only the standard library.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import artwork

USER_AGENT = "gotg-curate/0.1.0"
TIMEOUT = 30

# The two the grid draws, portrait first — the warmer's order (steam/warm.py),
# because what is curated here has to look like what warming would have put
# there.
KINDS = ("grids_portrait", "grids")

PNG = b"\x89PNG\r\n\x1a\n"
JPEG = b"\xff\xd8\xff"
RIFF = b"RIFF"


def is_image(body: bytes) -> bool:
    """The service checks this too, and refuses with a 415. Checked here as
    well so a mistyped path is a sentence rather than a status code."""
    return body.startswith((PNG, JPEG)) or (body.startswith(RIFF) and body[8:12] == b"WEBP")


def call(url: str, token: str, *, data: bytes | None = None, method: str | None = None) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("User-Agent", USER_AGENT)
    if data is not None:
        request.add_header("Content-Type", "application/octet-stream")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def art_url(service: str, platform: str, game_id: str) -> str:
    return f"{service}/art/{urllib.parse.quote(platform)}/{urllib.parse.quote(game_id)}"


def cmd_search(args, service: str, token: str) -> int:
    """Every match for a name, and the assets behind each — the two questions
    somebody asks in the same breath when a tile is wrong."""
    # The proxy *is* the API under that prefix, so this needs no key of its
    # own. The override is the warmer's (steam/warm.py), and the reason the
    # tests can stand a mock in front of it.
    base = os.environ.get("GOTG_STEAMGRIDDB_URL") or f"{service}/steamgriddb"
    status, body = call(f"{base}/api/v2/search/autocomplete/{urllib.parse.quote(args.title)}", token)
    if status != 200:
        print(f"search failed: http {status} {body[:200].decode(errors='replace')}", file=sys.stderr)
        return 1
    games = (json.loads(body).get("data") or [])[: args.limit]
    if not games:
        print(f"no match for {args.title!r}", file=sys.stderr)
        return 1
    found = []
    for game in games:
        entry = {"game_id": game.get("id"), "name": game.get("name"), "assets": []}
        if args.assets:
            for kind in KINDS:
                endpoint, params = artwork.ENDPOINT[kind]
                url = f"{base}/api/v2/{endpoint}/game/{game['id']}"
                if params:
                    url += "?" + urllib.parse.urlencode(params)
                status, body = call(url, token)
                if status != 200:
                    continue
                for asset in sorted(json.loads(body).get("data") or [], key=lambda a: a.get("score", 0), reverse=True)[
                    : args.assets
                ]:
                    entry["assets"].append({"kind": kind, "score": asset.get("score"), "url": asset.get("url")})
        found.append(entry)
    print(json.dumps(found, indent=2))
    return 0


def picture_from(source: str) -> bytes:
    """A file or a url. Unlike everything the warmer does, this one is not
    best-effort: somebody typed a path, so a path that cannot be used is an
    error rather than a warning to read past."""
    if source.startswith(("http://", "https://")):
        request = urllib.request.Request(source)
        request.add_header("User-Agent", USER_AGENT)
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310
            return response.read()
    return Path(source).read_bytes()


def cmd_set(args, service: str, token: str) -> int:
    try:
        body = picture_from(args.source)
    except (OSError, urllib.error.URLError, ValueError) as error:
        print(f"cannot read {args.source}: {error}", file=sys.stderr)
        return 1
    if not is_image(body):
        print(f"{args.source} is not a png, jpeg or webp", file=sys.stderr)
        return 1
    status, reply = call(art_url(service, args.platform, args.id), token, data=body, method="PUT")
    if status != 200:
        print(f"the service refused it: http {status} {reply[:200].decode(errors='replace')}", file=sys.stderr)
        return 1
    print(reply.decode())
    return 0


def cmd_miss(args, service: str, token: str) -> int:
    """Say outright that nobody has art for this one — the other half of
    curating, for a game whose only matches are wrong."""
    status, reply = call(f"{art_url(service, args.platform, args.id)}?miss=1", token, data=b"", method="PUT")
    if status != 200:
        print(f"the service refused it: http {status} {reply[:200].decode(errors='replace')}", file=sys.stderr)
        return 1
    print(reply.decode())
    return 0


def cmd_show(args, service: str, token: str) -> int:
    status, body = call(art_url(service, args.platform, args.id), token)
    if status == 200:
        print(json.dumps({"held": f"{args.platform}/{args.id}", "bytes": len(body)}))
        if args.out:
            Path(args.out).write_bytes(body)
            print(f"written to {args.out}", file=sys.stderr)
        return 0
    if status == 404:
        # miss and absent are different answers: one is the fleet's "nobody
        # has this", the other only means no warm has reached it yet.
        print(json.dumps(json.loads(body or b"{}")))
        return 0
    print(f"http {status}: {body[:200].decode(errors='replace')}", file=sys.stderr)
    return 1


def cmd_forget(args, service: str, token: str) -> int:
    status, reply = call(art_url(service, args.platform, args.id), token, method="DELETE")
    if status != 200:
        print(f"http {status}: {reply[:200].decode(errors='replace')}", file=sys.stderr)
        return 1
    print(reply.decode())
    return 0


def entry(value: str) -> tuple[str, str]:
    """<platform>/<id>, which is how the catalog, the cache and every URL
    here spell a game."""
    platform, _, game_id = value.partition("/")
    if not platform or not game_id:
        raise argparse.ArgumentTypeError(f"name a game as <platform>/<id>, not {value!r}")
    return platform, game_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--service", required=True)
    subs = parser.add_subparsers(dest="command", required=True)

    search = subs.add_parser("search", help="what SteamGridDB has under a name")
    search.add_argument("title")
    search.add_argument("--limit", type=int, default=5)
    search.add_argument("--assets", type=int, default=0, metavar="N", help="also list the top N assets per kind")
    search.set_defaults(run=cmd_search)

    put = subs.add_parser("set", help="this picture is the one")
    put.add_argument("game", type=entry, metavar="<platform>/<id>")
    put.add_argument("source", metavar="<file|url>")
    put.set_defaults(run=cmd_set)

    miss = subs.add_parser("miss", help="record that nobody has art for it")
    miss.add_argument("game", type=entry, metavar="<platform>/<id>")
    miss.set_defaults(run=cmd_miss)

    show = subs.add_parser("show", help="what the cache holds for it")
    show.add_argument("game", type=entry, metavar="<platform>/<id>")
    show.add_argument("--out", help="write the picture here")
    show.set_defaults(run=cmd_show)

    forget = subs.add_parser("forget", help="drop it, so the next warm looks again")
    forget.add_argument("game", type=entry, metavar="<platform>/<id>")
    forget.set_defaults(run=cmd_forget)

    args = parser.parse_args(argv)
    if getattr(args, "game", None):
        args.platform, args.id = args.game
    # From the environment, never a flag: /proc/<pid>/cmdline is
    # world-readable. Stripped, because a token read out of a Kubernetes
    # secret arrives with whatever whitespace the secret was written with.
    token = os.environ.get("GOTG_INDEX_TOKEN", "").strip()
    if not token:
        parser.error("GOTG_INDEX_TOKEN is not set; curating the cache is the index token's job")
    return args.run(args, args.service.rstrip("/"), token)


if __name__ == "__main__":
    sys.exit(main())
