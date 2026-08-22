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
        "--list",
        action="store_true",
        help="print the first page and exit, without opening a window",
    )
    args = parser.parse_args(argv)

    try:
        games = load(args.catalog)
    except CatalogError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    library = Library(games).filter(platform=args.platform, search=args.search)

    # A way to see what the grid would show from a terminal — over ssh, in a
    # test, or on a machine with no display at all, which is every CI runner.
    if args.list:
        for game in library.page(0):
            print(f"{game.platform:<9} {game.id:<40} {game.title}")
        print(f"page 1 of {library.pages} · {len(library)} games", file=sys.stderr)
        return 0

    from .app import run  # imported here so --list needs no display and no pygame

    return run(library)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
