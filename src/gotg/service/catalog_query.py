"""Search, filter, sort and paginate the catalog for a browser.

Pure functions over the games of `CatalogStore.view()`, so the HTTP layer only
parses a query string. A request with none of these parameters keeps the
original unpaginated reply (older clients and Nix fetches depend on it).
"""

from __future__ import annotations

import urllib.parse

DEFAULT_LIMIT = 50
MAX_LIMIT = 500
SORTS = {"title": "title", "id": "id", "size": "size_bytes", "platform": "platform"}
# Parameters that switch /catalog into its paged form.
PAGED_PARAMS = frozenset(
    {"limit", "offset", "q", "platform", "region", "handler", "sort", "order", "min_size", "max_size", "facets"}
)


def region_of(game_id: str) -> str:
    """The region prefix of an entry id (`usa.zelda` -> `usa`)."""
    return game_id.partition(".")[0]


def parse_query(raw: str) -> dict[str, list[str]]:
    return urllib.parse.parse_qs(raw, keep_blank_values=False)


def is_paged(params: dict[str, list[str]]) -> bool:
    return bool(PAGED_PARAMS & params.keys())


def _int(params: dict[str, list[str]], name: str, default: int, low: int, high: int | None = None) -> int:
    if name not in params:
        return default
    try:
        value = int(params[name][-1])
    except ValueError:
        raise ValueError(f"{name} must be an integer") from None
    if value < low or (high is not None and value > high):
        bound = f"between {low} and {high}" if high is not None else f"at least {low}"
        raise ValueError(f"{name} must be {bound}")
    return value


def _multi(params: dict[str, list[str]], name: str) -> set[str]:
    """Repeated or comma-separated values, lower-cased."""
    return {v.strip().lower() for raw in params.get(name, []) for v in raw.split(",") if v.strip()}


def summarize(game: dict) -> dict:
    """The game plus the fields a list view wants without summing files itself."""
    files = game.get("files", [])
    return {
        **game,
        "region": region_of(game["id"]),
        "file_count": len(files),
        "size_bytes": sum(f.get("size_bytes") or 0 for f in files),
    }


def select(games: list[dict], params: dict[str, list[str]]) -> dict:
    """The paged reply: `{games, total, offset, limit, [facets]}`.

    Raises ValueError (a 400 at the door) for a parameter that cannot be used.
    """
    limit = _int(params, "limit", DEFAULT_LIMIT, 1, MAX_LIMIT)
    offset = _int(params, "offset", 0, 0)
    min_size = _int(params, "min_size", 0, 0)
    max_size = _int(params, "max_size", -1, 0) if "max_size" in params else None
    sort = params.get("sort", ["title"])[-1]
    if sort not in SORTS:
        raise ValueError(f"sort must be one of {', '.join(sorted(SORTS))}")
    order = params.get("order", ["asc"])[-1]
    if order not in ("asc", "desc"):
        raise ValueError("order must be asc or desc")

    platforms, regions, handlers = _multi(params, "platform"), _multi(params, "region"), _multi(params, "handler")
    needle = params["q"][-1].strip().lower() if "q" in params else ""

    rows = [summarize(g) for g in games]
    facets = {
        "platform": _count(rows, "platform"),
        "region": _count(rows, "region"),
        "handler": _count(rows, "handler"),
    }

    def keep(game: dict) -> bool:
        if platforms and game["platform"] not in platforms:
            return False
        if regions and game["region"] not in regions:
            return False
        if handlers and game["handler"] not in handlers:
            return False
        if game["size_bytes"] < min_size or (max_size is not None and game["size_bytes"] > max_size):
            return False
        return not needle or all(t in f"{game['title']} {game['id']}".lower() for t in needle.split())

    matched = [g for g in rows if keep(g)]
    key = SORTS[sort]
    matched.sort(
        key=lambda g: (str(g[key]).lower() if key != "size_bytes" else g[key], g["platform"], g["id"]),
        reverse=order == "desc",
    )
    reply = {
        "version": 2,
        "total": len(matched),
        "offset": offset,
        "limit": limit,
        "games": matched[offset : offset + limit],
    }
    if params.get("facets", ["0"])[-1] in ("1", "true"):
        reply["facets"] = facets
    return reply


def _count(rows: list[dict], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row[field]] = counts.get(row[field], 0) + 1
    return dict(sorted(counts.items()))
