"""No-Intro / Redump / scene filename -> GOTG entry id.

The GOTG entry id (a game's path minus extension) must match
``^[a-z]{3,5}\\.[a-z0-9][a-z0-9_]*$`` and is used verbatim as server path,
manifest id, launcher name and CLI arg. This module turns a source filename
into that id deterministically. Anything it cannot confidently parse returns a
low-confidence result so the caller can quarantine it for human review rather
than guessing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

ENTRY_RE = re.compile(r"^[a-z]{3,5}\.[a-z0-9][a-z0-9_]*$")

# No-Intro region tag -> one of the four GOTG-allowed prefixes.
# Only usa/eur/jpn/world are legal; single-country PAL regions collapse to eur.
_REGION_MAP = {
    "usa": "usa",
    "world": "world",
    "europe": "eur",
    "japan": "jpn",
    "germany": "eur",
    "france": "eur",
    "spain": "eur",
    "italy": "eur",
    "netherlands": "eur",
    "sweden": "eur",
    "australia": "eur",
    "uk": "eur",
    "united kingdom": "eur",
    "scandinavia": "eur",
    "korea": "world",
    "china": "world",
    "taiwan": "world",
    "asia": "world",
    "brazil": "world",
    "canada": "usa",
    "latin america": "world",
}

# Region preference for 1G1R selection (lower index = preferred).
_REGION_PRIORITY = ["usa", "world", "eur", "jpn"]

# Parenthetical tags that mark a non-retail dump -> excluded from a 1G1R view.
_NON_RETAIL = {
    "beta", "proto", "prototype", "demo", "sample", "kiosk", "program",
    "test program", "unl", "pirate", "aftermarket", "debug", "competition",
    "promo", "preview", "alpha", "dev", "hack", "enhancement chip",
}

# Leading English articles to drop (No-Intro puts them in trailing ", The" form).
_ARTICLES = {"the", "a", "an"}

_REV_RE = re.compile(r"^(?:rev\s*([0-9a-z.]+)|v\s*([0-9]+(?:\.[0-9]+)*))$", re.I)

_LANG_RE = re.compile(r"^[A-Z][a-z](,[A-Z][a-z])+$")
_DATE_RE = re.compile(r"^\d{4}(-\d{2}){0,2}$")


def _normalize_status(tag: str) -> str:
    """'Beta 1' -> 'beta'. No-Intro numbers repeated dumps of the same kind, and
    an exact-match lookup would let every one of those through as retail."""
    return re.sub(r"\s*\d+$", "", tag.strip().lower())


def _variant_tags(tags: list[str]) -> list[str]:
    """Tags that mark an alternate release rather than describing the dump.

    Region, languages, revision and dates say *which* copy this is; anything left
    ("LodgeNet", "GameCube", "Arcade", "Virtual Console") says it is a different
    distribution of the same game.
    """
    out = []
    for tag in tags:
        low = tag.strip().lower()
        if low in _REGION_MAP or _LANG_RE.match(tag.strip()) or _DATE_RE.match(tag.strip()):
            continue
        if _REV_RE.match(tag.strip()):
            continue
        if all(part.strip().lower() in _REGION_MAP for part in tag.split(",")):
            continue  # multi-region tag, e.g. "USA, Europe"
        out.append(tag.strip())
    return out


@dataclass
class ParsedRom:
    base_title: str          # human title, article restored to front, no tags
    region: str              # usa | eur | jpn | world
    slug: str                # title_slug (no region, no revision)
    revision: str            # e.g. "rev1", "v1_2", or ""
    languages: list[str] = field(default_factory=list)
    statuses: list[str] = field(default_factory=list)
    variants: list[str] = field(default_factory=list)  # tags that are not region/language/revision
    ext: str = ""
    is_retail: bool = True
    confident: bool = True    # False -> caller should quarantine for review

    @property
    def entry_id(self) -> str:
        core = self.slug + (f"_{self.revision}" if self.revision else "")
        return f"{self.region}.{core}"

    @property
    def valid(self) -> bool:
        return bool(ENTRY_RE.match(self.entry_id))


def _split_tags(name: str) -> tuple[str, list[str]]:
    """Return (base title before first '(', [tag, ...])."""
    idx = name.find("(")
    if idx == -1:
        return name.strip(), []
    base = name[:idx].strip()
    tags = re.findall(r"\(([^)]*)\)", name[idx:])
    return base, [t.strip() for t in tags]


def _restore_article(base: str) -> str:
    """'Legend of Zelda, The - Majora's Mask' -> 'Legend of Zelda - Majora's Mask'
    (drop the trailing-comma article). Also handles a leading 'The '."""
    m = re.match(r"^(.*?),\s+(The|A|An)\b(.*)$", base, re.I)
    if m and m.group(2).lower() in _ARTICLES:
        base = (m.group(1) + m.group(3)).strip()
    else:
        lead = re.match(r"^(The|A|An)\s+(.*)$", base, re.I)
        if lead and lead.group(1).lower() in _ARTICLES:
            base = lead.group(2).strip()
    return base


def title_slug(base: str) -> str:
    s = _restore_article(base)
    s = s.replace("&", " and ")
    s = s.lower()
    s = s.replace("'", "").replace("’", "")   # Majora's -> majoras
    s = re.sub(r"[^a-z0-9]+", "_", s)               # everything else -> _
    s = re.sub(r"_+", "_", s).strip("_")
    return s


def _parse_region(tags: list[str]) -> tuple[str, bool]:
    for t in tags:
        parts = [p.strip().lower() for p in t.split(",")]
        mapped = [_REGION_MAP[p] for p in parts if p in _REGION_MAP]
        if mapped:
            if len(set(mapped)) > 1:
                return "world", True          # multi-region -> world
            return mapped[0], True
    return "world", False                     # no region tag -> unknown/low-confidence


def _parse_revision(tags: list[str]) -> str:
    revs: list[str] = []
    for t in tags:
        m = _REV_RE.match(t.strip())
        if not m:
            continue
        if m.group(1) is not None:            # Rev X
            revs.append("rev" + m.group(1).lower().replace(".", "_"))
        else:                                  # vN.M
            revs.append("v" + m.group(2).lower().replace(".", "_"))
    return "_".join(revs)


def parse(filename: str) -> ParsedRom:
    name = filename
    ext = ""
    m = re.search(r"\.([A-Za-z0-9]{1,4})$", name)
    if m:
        ext = m.group(1).lower()
        name = name[: m.start()]

    base, tags = _split_tags(name)
    region, region_ok = _parse_region(tags)
    revision = _parse_revision(tags)

    lower_tags = [t.lower() for t in tags]
    statuses = [t for t in lower_tags if _normalize_status(t) in _NON_RETAIL]
    languages = next((t for t in tags if re.match(r"^[A-Z][a-z](,[A-Z][a-z])+$", t)), "")
    languages = languages.split(",") if languages else []

    slug = title_slug(base)
    is_retail = not statuses

    parsed = ParsedRom(
        base_title=_restore_article(base),
        region=region,
        slug=slug,
        revision=revision,
        languages=languages,
        statuses=statuses,
        variants=_variant_tags(tags),
        ext=ext,
        is_retail=is_retail,
        confident=region_ok and bool(slug),
    )
    if not parsed.valid:
        parsed.confident = False
    return parsed


def select_1g1r(candidates: list[ParsedRom]) -> ParsedRom | None:
    """From all dumps sharing one title, pick the single retail keeper.

    Preference: retail only; then region priority (usa>world>eur>jpn); then the
    highest revision; then the fewest variant tags. Returns None if no retail
    candidate exists.

    That last step matters: a title often has several equally-ranked dumps that
    differ only by distribution — "(USA) (LodgeNet)" is the hotel-rental unit,
    "(USA) (GameCube)" is ripped from a bonus disc, "(USA) (Arcade)" is the
    Nintendo Super System board. Without it the winner came down to filename
    order, which picked a variant over the standard cartridge for 135 titles in
    this library, including Majora's Mask.
    """
    retail = [c for c in candidates if c.is_retail and c.valid]
    if not retail:
        return None

    def rank(c: ParsedRom):
        region_idx = _REGION_PRIORITY.index(c.region) if c.region in _REGION_PRIORITY else len(_REGION_PRIORITY)
        return (region_idx, -_rev_sort_key(c.revision), len(c.variants))

    return sorted(retail, key=rank)[0]


def _rev_sort_key(revision: str) -> int:
    nums = re.findall(r"\d+", revision)
    return int("".join(nums)) if nums else 0
