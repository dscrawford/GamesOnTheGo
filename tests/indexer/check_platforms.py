"""Every platform the importer can name must have an environment to run in.

A DAT directory or extension mapped to a platform with no `client/env/<x>.nix`
imports games nobody can launch — the failure is invisible until somebody tries
to play one. Run from the flake check, where both trees exist.
"""

import os
import pathlib
import sys

from gotg.indexer.plan import DAT_DIR_PLATFORM, EXT_PLATFORM

envs = pathlib.Path(os.environ["GOTG_ENV_DIR"])
missing = []

for name, (platform, handler) in DAT_DIR_PLATFORM.items():
    if handler != "excluded" and not (envs / f"{platform}.nix").is_file():
        missing.append(f"{name} -> {platform}: no client/env/{platform}.nix")

for ext, platform in EXT_PLATFORM.items():
    if not (envs / f"{platform}.nix").is_file():
        missing.append(f".{ext} -> {platform}: no client/env/{platform}.nix")

if missing:
    print("\n".join(missing), file=sys.stderr)
    sys.exit(1)

print(f"{len(DAT_DIR_PLATFORM)} DAT dirs, {len(EXT_PLATFORM)} extensions, every platform launchable")
