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
import json
import os
import random
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


def new_appid() -> int:
    """A fresh shortcut id, as a signed 32-bit with the high bit set.

    Not derived from the name or path: Steam's own ids do not reproduce under
    any of the published crc32 formulas, so it evidently assigns them randomly
    now. Ours only has to be unique and stable once written — which it is,
    because an update keeps whatever id the entry already had.
    """
    return random.randint(0x80000000, 0xFFFFFFFF) - 0x100000000


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
        entry = dict(shortcuts[index])
        action = "updated"
    else:
        entry = dict(DEFAULTS)
        entry["appid"] = new_appid()
        index = str(len(shortcuts))
        action = "added"

    entry["AppName"] = args.name
    entry["Exe"] = exe
    entry["StartDir"] = start_dir
    if args.launch_options is not None:
        entry["LaunchOptions"] = args.launch_options

    shortcuts[index] = entry
    save(path, shortcuts)
    print(json.dumps({"action": action, "name": args.name, "appid": entry["appid"]}))
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
    add.set_defaults(func=cmd_add)

    remove = sub.add_parser("remove")
    remove.add_argument("--exe", required=True)
    remove.set_defaults(func=cmd_remove)

    sub.add_parser("list").set_defaults(func=cmd_list)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
