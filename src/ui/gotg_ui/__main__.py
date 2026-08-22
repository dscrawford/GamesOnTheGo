"""`gotg-ui` — the grid, from the catalog the client already cached."""

from __future__ import annotations

import argparse
import sys

from .catalog import CatalogError, Library, default_cache_path, load


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gotg-ui",
        description="Pick a game from a grid, and play it.",
    )
    parser.add_argument(
        "--catalog",
        metavar="FILE",
        default=None,
        help=f"the cached catalog (default: {default_cache_path()})",
    )
    parser.add_argument("--platform", metavar="P", default=None, help="only this platform")
    parser.add_argument("--search", metavar="TEXT", default=None, help="only games matching this, in id or title")
    parser.add_argument(
        "--region",
        metavar="R",
        default=None,
        choices=["usa", "eur", "jpn", "world"],
        help="only this region — world releases are region-free and always included",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="print the first page and exit, without opening a window",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="forget the cached art for these games, misses included, and look again",
    )
    parser.add_argument(
        "--play",
        metavar="ID",
        default=None,
        help="skip the grid and hand this id straight to the client, as picking it would",
    )
    args = parser.parse_args(argv)

    try:
        games = load(args.catalog)
    except CatalogError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    library = Library(games).filter(platform=args.platform, search=args.search, region=args.region)

    if args.refresh:
        # Nothing here expires on its own — a game with no art is remembered
        # as having none for good — so this is the only way back to the
        # network, and it is deliberately scoped by the same filters.
        from .art import ArtStore

        store = ArtStore()
        for game in library.games:
            store.forget(game)
        print(f"forgot cached art for {len(library)} game(s)", file=sys.stderr)

    # A way to see what the grid would show from a terminal — over ssh, in a
    # test, or on a machine with no display at all, which is every CI runner.
    if args.list:
        for game in library.page(0):
            print(f"{game.platform:<9} {game.id:<40} {game.title}")
        print(f"page 1 of {library.pages} · {len(library)} games", file=sys.stderr)
        return 0

    if args.play:
        # The same handoff picking a game makes, reachable without a screen.
        found = [g for g in library.games if args.play in (g.id, f"{g.platform}/{g.id}")]
        if not found:
            print(f"error: no game in the catalog is {args.play!r}", file=sys.stderr)
            return 2
        if len({g.platform for g in found}) > 1:
            names = ", ".join(sorted(f"{g.platform}/{g.id}" for g in found))
            print(f"error: {args.play!r} is on more than one platform — say which: {names}", file=sys.stderr)
            return 2
        return _play(found[0])

    from .app import run  # imported here so --list and --play need no display

    chosen = run(library)
    if chosen is None:
        return 0
    return _play(*chosen)


def _play(game, verb: str = "play") -> int:
    """Hand off, and only come back if the client could not be started."""
    from .launch import LaunchError, play

    try:
        play(game, verb)
    except LaunchError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0  # unreachable: play() replaced this process


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
