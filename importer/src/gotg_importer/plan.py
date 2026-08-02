"""Turn a classified torrent source into a concrete, side-effect-free plan.

A ``Plan`` is a list of ``Op`` records describing exactly what the importer
would do (hardlink / extract / decrypt / skip) without touching the filesystem.
The importer executes a plan; the dry-run just prints it. Keeping planning pure
makes the whole pipeline testable off-cluster.
"""

from __future__ import annotations

import collections
import re as _re
from dataclasses import dataclass

from .slugify import ParsedRom, parse, select_1g1r, title_slug

# Source directory name (No-Intro DAT dirname) -> platform slug + handler.
# Extend here to add platforms; this mirrors the ConfigMap `rules.yaml`.
DAT_DIR_PLATFORM = {
    "Nintendo - Nintendo 64 (BigEndian)": ("n64", "no_intro_set"),
    "Nintendo - Super Nintendo Entertainment System": ("snes", "no_intro_set"),
    # NES aftermarket/homebrew set is excluded by decision (2026-07-26).
    "Nintendo - Nintendo Entertainment System (Headered) (Aftermarket)": ("nes", "excluded"),
}

# Single-file extension -> platform (for loose ROM torrents).
EXT_PLATFORM = {
    "z64": "n64", "n64": "n64", "v64": "n64",
    "sfc": "snes", "smc": "snes",
    "nes": "nes",
    "nsp": "switch", "xci": "switch",
    "rvz": "gamecube", "gcm": "gamecube",
    "wua": "wiiu", "wux": "wiiu",
}

ACTION_HARDLINK = "hardlink"      # renamed hardlink, zero space, seeding-safe
ACTION_EXTRACT = "extract"        # derived copy (scene archive -> rom)
ACTION_ARCHIVE = "archive"        # derived copy (decrypted dir -> single .zip)
ACTION_CONVERT = "convert"        # derived conversion (source -> the format the emulator wants)
ACTION_DECRYPT = "decrypt"        # WiiU NUS -> code/content/meta (deferred)
ACTION_SKIP = "skip"              # excluded / non-retail / duplicate
ACTION_MANUAL = "manual"          # low-confidence -> quarantine for human review


@dataclass
class Op:
    action: str
    platform: str
    src: str                      # source path (file or dir)
    dst: str                      # target path under /Games (or "")
    entry_id: str                 # GOTG id (path minus ext) or ""
    title: str = ""               # human display title for the manifest
    reason: str = ""              # why skipped / flagged
    type: str = "file"            # "file" | "dir"


def _display_title(p: ParsedRom) -> str:
    words = p.base_title.replace("_", " ").split()
    return " ".join(w if w.isupper() else w.capitalize() for w in words)


def plan_no_intro_set(platform: str, games_root: str, src_dir: str,
                      filenames: list[str], one_g_one_r: bool = True) -> list[Op]:
    """Plan a No-Intro set: 1G1R-filter, slugify, hardlink retail keepers."""
    parsed = [(fn, parse(fn)) for fn in filenames]
    groups: dict[str, list[tuple[str, ParsedRom]]] = collections.defaultdict(list)
    for fn, p in parsed:
        groups[p.slug].append((fn, p))

    ops: list[Op] = []
    for slug, members in groups.items():
        candidates = [p for _, p in members]
        if one_g_one_r:
            winner = select_1g1r(candidates)
            if winner is None:
                # No retail dump for this title -> skip the whole group.
                fn0, p0 = members[0]
                ops.append(Op(ACTION_SKIP, platform, f"{src_dir}/{fn0}", "",
                              p0.entry_id, reason="no retail dump"))
                continue
            keep = [(fn, p) for fn, p in members if p is winner]
        else:
            keep = members

        for fn, p in keep:
            if not p.valid:
                ops.append(Op(ACTION_MANUAL, platform, f"{src_dir}/{fn}", "",
                              p.entry_id, reason="invalid/low-confidence id"))
                continue
            dst = f"{games_root}/{platform}/{p.entry_id}.{p.ext}"
            ops.append(Op(ACTION_HARDLINK, platform, f"{src_dir}/{fn}", dst,
                          p.entry_id, title=_display_title(p), type="file"))
    return ops


def plan_single_archive(platform: str, games_root: str, src_file: str,
                        inner_name: str, target_ext: str = "") -> Op:
    """Plan a lone archive: unpack the game inside, in the format the emulator wants.

    The archive's own name carries the title and region — it is an ordinary
    release name — so the id comes from that rather than from the member, whose
    name may be anything the packer chose.
    """
    stem = src_file.rsplit("/", 1)[-1]
    stem = _re.sub(r"\.[A-Za-z0-9]{1,4}$", "", stem)
    parsed = parse(f"{stem}.{_ext_of(inner_name)}")

    if not parsed.valid:
        return Op(ACTION_MANUAL, platform, src_file, "", parsed.entry_id,
                  reason="archive name does not yield a valid id")

    inner_ext = _ext_of(inner_name)
    # Convert only when the emulator wants something else; an archive that
    # already holds the target format just needs unpacking.
    if target_ext and inner_ext != target_ext:
        action, ext = ACTION_CONVERT, target_ext
    else:
        action, ext = ACTION_EXTRACT, inner_ext

    dst = f"{games_root}/{platform}/{parsed.entry_id}.{ext}"
    return Op(action, platform, src_file, dst, parsed.entry_id,
              title=_display_title(parsed), type="file")


def _ext_of(name: str) -> str:
    _, dot, ext = name.rpartition(".")
    return ext.lower() if dot else ""


def _clean_scene_name(name: str) -> str:
    """Strip scene noise: leading volume prefix (v-/s-), trailing -GROUP,
    and underscores, leaving a plain title for the slugifier."""
    name = _re.sub(r"\.[A-Za-z0-9]{1,4}$", "", name)   # drop extension
    name = _re.sub(r"^[a-z]{1,2}-", "", name)             # v- / s- volume prefix
    name = _re.sub(r"[-_]([A-Z0-9]{2,}(?:-[A-Z0-9]+)?)$", "", name)  # -NSW-VENOM group
    return name.replace("_", " ").strip()


def plan_scene_archive(platform: str, games_root: str, src_dir: str,
                       release_name: str, inner_name: str, region: str = "world") -> Op:
    """Plan a scene RAR -> single rom extraction. Scene names carry no region
    tag, so region defaults (world, or a caller-supplied override) and is flagged
    for confirmation; content metadata (nstool) can refine it at execution time."""
    title = _clean_scene_name(inner_name) or _clean_scene_name(release_name)
    slug = title_slug(title)
    ext = inner_name.rsplit(".", 1)[-1].lower() if "." in inner_name else "nsp"
    entry = f"{region}.{slug}"
    dst = f"{games_root}/{platform}/{entry}.{ext}"
    return Op(ACTION_EXTRACT, platform, f"{src_dir}/{release_name}", dst, entry,
              title=" ".join(w if w.isupper() else w.capitalize() for w in title.split()),
              type="file", reason="region defaulted; confirm")


def plan_wiiu_decrypted(games_root: str, src_dir: str, dir_name: str) -> Op:
    """A WiiU title that already carries its decrypted ``code/content/meta`` output.

    Nothing to decrypt, so this is importable today: pack the three decrypted dirs
    into one ``<id>.zip`` entry. A single file (rather than a hardlinked directory)
    buys the client HTTP resume and a stable checksum, which matters for multi-GB
    titles downloaded over the internet — at the cost of a derived copy on the
    server. The dry-run surfaces that cost before anything is written.
    """
    p = parse(dir_name)
    if not p.valid:
        return Op(ACTION_MANUAL, "wiiu", f"{src_dir}/{dir_name}", "", p.entry_id,
                  title=_display_title(p), reason="invalid/low-confidence id")
    dst = f"{games_root}/wiiu/{p.entry_id}.zip"
    return Op(ACTION_ARCHIVE, "wiiu", f"{src_dir}/{dir_name}", dst, p.entry_id,
              title=_display_title(p), type="file",
              reason="already decrypted; zip is a derived copy (costs disk)")


def plan_wiiu_nus(games_root: str, src_dir: str, dir_name: str) -> Op:
    """WiiU is deferred: decryption needs keys the user will supply later.
    Flag for manual post-processing; record the intended single-.zip target."""
    p = parse(dir_name)
    entry = p.entry_id if p.valid else ""
    dst = f"{games_root}/wiiu/{entry}.zip" if entry else ""
    return Op(ACTION_MANUAL, "wiiu", f"{src_dir}/{dir_name}", dst, entry,
              title=_display_title(p), type="file",
              reason="WiiU decrypt deferred (needs common key + titlekey)")
