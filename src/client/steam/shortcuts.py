#!/usr/bin/env python3
"""Read and write Steam's non-Steam shortcuts.

``shortcuts.vdf`` is binary, which is the whole reason this is not in bash. The
field names and shapes below were read out of a real file written by Steam
itself rather than from documentation — including the two details a hand-written
entry gets wrong: ``Exe`` is quoted, and ``StartDir`` keeps its trailing slash.

Steam rewrites this file when it exits, so anything written while it is running
is discarded. The caller checks for that; this only reports what it did.
"""

from __future__ import annotations

import argparse
import binascii
import json
import os
import shutil
import sys
from pathlib import Path

import vdf

# What Steam wrote for an entry we did not create. Anything absent from a
# shortcut we add is a field Steam is happy to default, but writing the full set
# keeps our entries indistinguishable from its own.
DEFAULTS: dict[str, object] = {
    "icon": "",
    "ShortcutPath": "",
    "LaunchOptions": "",
    "IsHidden": 0,
    "AllowDesktopConfig": 1,
    "AllowOverlay": 1,
    "OpenVR": 0,
    "Devkit": 0,
    "DevkitGameID": "",
    "DevkitOverrideAppID": 0,
    "LastPlayTime": 0,
    "FlatpakAppID": "",
    "sortas": "",
    "tags": {},
}


def load(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("rb") as handle:
        return vdf.binary_load(handle).get("shortcuts", {})


def save(path: Path, shortcuts: dict) -> None:
    """Write the file back, keeping a copy of what was there.

    Steam's own list is in here too, so a bug that truncates this file loses
    every non-Steam game the user has, not only ours.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_suffix(".vdf.gotg-bak"))

    # Reindexed from zero: Steam keys entries by position, and a gap left by a
    # removal makes it drop everything after it.
    renumbered = {str(i): entry for i, entry in enumerate(shortcuts.values())}

    tmp = path.with_suffix(".vdf.gotg-tmp")
    with tmp.open("wb") as handle:
        vdf.binary_dump({"shortcuts": renumbered}, handle)
    os.replace(tmp, path)


def app_ids(exe: str, name: str) -> tuple[int, int]:
    """The shortcut id, and the long form Big Picture files artwork under.

    Transcribed from Steam ROM Manager's generate-app-id.ts, which EmuDeck
    follows too. Deterministic on purpose: every piece of artwork for a
    non-Steam game lives in userdata/<user>/config/grid named after this id, so
    an id chosen at random orphans the art the moment anything is rewritten.

    Steam's own "Add a Non-Steam Game" does choose randomly — an entry it made
    will not reproduce under this — which is precisely why the tools compute it
    instead: it lets artwork be put in place without asking Steam anything.

    The path is the bare one, not the quoted form stored in Exe, matching what
    EmuDeck hashes.
    """
    top = binascii.crc32((exe + name).encode()) | 0x80000000
    long_id = (top << 32) | 0x02000000
    return (long_id >> 32) - 0x100000000, long_id


def find_entry(shortcuts: dict, exe: str) -> str | None:
    """The index of the shortcut pointing at this executable, if any.

    Matched on Exe rather than name: the name is what a person edits in Steam,
    and renaming a shortcut should not cause a second one to appear.
    """
    for index, entry in shortcuts.items():
        if entry.get("Exe", "").strip('"') == exe.strip('"'):
            return index
    return None


def cmd_add(args: argparse.Namespace) -> int:
    path = Path(args.file)
    shortcuts = load(path)

    # Steam stores the executable quoted, and the directory with a trailing
    # separator. Copied from what it wrote itself.
    exe = f'"{args.exe}"'
    start_dir = str(args.start_dir).rstrip("/") + "/"

    index = find_entry(shortcuts, exe)
    if index is not None:
        # Only the fields we own are rewritten; IsHidden, AllowOverlay,
        # LastPlayTime and anything else Steam put there survive, which is the
        # rule Steam ROM Manager follows too.
        entry = dict(shortcuts[index])
        action = "updated"
    else:
        entry = dict(DEFAULTS)
        index = str(len(shortcuts))
        action = "added"

    appid, grid_appid = app_ids(args.exe, args.name)
    entry["appid"] = appid
    entry["AppName"] = args.name
    entry["Exe"] = exe
    entry["StartDir"] = start_dir
    if args.launch_options is not None:
        entry["LaunchOptions"] = args.launch_options
    if args.icon is not None:
        entry["icon"] = args.icon
    # A tag is a Steam collection. EmuDeck tags everything it adds so the games
    # arrive grouped rather than scattered through the library.
    if args.tag:
        entry["tags"] = {"0": args.tag}

    shortcuts[index] = entry
    save(path, shortcuts)
    print(
        json.dumps(
            {
                "action": action,
                "name": args.name,
                "appid": appid,
                # What artwork is filed under, for whoever adds it later.
                "grid_appid": str(grid_appid),
            }
        )
    )
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    path = Path(args.file)
    shortcuts = load(path)
    index = find_entry(shortcuts, args.exe)
    if index is None:
        print(json.dumps({"action": "absent"}))
        return 0
    name = shortcuts[index].get("AppName", "")
    del shortcuts[index]
    save(path, shortcuts)
    print(json.dumps({"action": "removed", "name": name}))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    shortcuts = load(Path(args.file))
    print(
        json.dumps(
            [
                {
                    "name": e.get("AppName", ""),
                    "exe": e.get("Exe", "").strip('"'),
                    "options": e.get("LaunchOptions", ""),
                    "appid": e.get("appid", 0),
                    "icon": e.get("icon", ""),
                    "tags": list((e.get("tags") or {}).values()),
                }
                for e in shortcuts.values()
            ]
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, help="path to shortcuts.vdf")
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add")
    add.add_argument("--name", required=True)
    add.add_argument("--exe", required=True)
    add.add_argument("--start-dir", required=True)
    add.add_argument("--launch-options")
    add.add_argument("--tag", help="Steam collection to file it under")
    # The list icon is not grid art: Steam reads it off the shortcut itself,
    # so a fetched _icon.ico does nothing until this field points at it.
    add.add_argument("--icon", help="path to the icon the shortcut shows")
    add.set_defaults(func=cmd_add)

    remove = sub.add_parser("remove")
    remove.add_argument("--exe", required=True)
    remove.set_defaults(func=cmd_remove)

    sub.add_parser("list").set_defaults(func=cmd_list)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
