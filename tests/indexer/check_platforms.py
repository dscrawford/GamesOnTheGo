"""Every platform the importer can name must have an environment to run in.

A DAT directory or extension mapped to a platform with no `client/env/<x>.nix`
imports games nobody can launch — the failure is invisible until somebody tries
to play one. Run from the flake check, where both trees exist.

Both sources are checked, and that is the point. plan.py holds the validated
defaults; rules.yaml is where a platform is actually *added*, so checking only
plan.py would leave the newer half unguarded — which is the half more likely to
name a platform nobody has written an environment for.
"""

import os
import pathlib
import sys

from gotg.indexer import rules as rules_module
from gotg.indexer.plan import DAT_DIR_PLATFORM, EXT_PLATFORM

envs = pathlib.Path(os.environ["GOTG_ENV_DIR"])
missing = []

rules = rules_module.load()

# name -> (platform, handler), rules.yaml winning where the two overlap, since
# that is the precedence load() itself applies.
dat_dirs = {**DAT_DIR_PLATFORM, **rules.dat_dirs}
extensions = {**EXT_PLATFORM, **rules.extensions}

for name, (platform, handler) in dat_dirs.items():
    if handler != "excluded" and not (envs / f"{platform}.nix").is_file():
        missing.append(f"{name} -> {platform}: no client/env/{platform}.nix")

for ext, platform in extensions.items():
    if not (envs / f"{platform}.nix").is_file():
        missing.append(f".{ext} -> {platform}: no client/env/{platform}.nix")

if missing:
    print("\n".join(missing), file=sys.stderr)
    sys.exit(1)

print(f"{len(dat_dirs)} DAT dirs, {len(extensions)} extensions, every platform launchable")
