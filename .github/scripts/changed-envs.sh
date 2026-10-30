#!/usr/bin/env bash
# Print the env-* packages whose derivation differs from <base-rev>'s, one per
# line: only those need building. Comparing drvPaths rather than file paths
# also catches what an environment reaches indirectly -- machinery, an
# emulator, flake.lock.
#
#   changed-envs.sh <base-rev> [system]
#
# No usable base (empty, all zeros, or it fails to evaluate) means every one.
set -euo pipefail

base="${1:-}"
system="${2:-x86_64-linux}"
apply='ps: builtins.mapAttrs (_: p: p.drvPath)
  (builtins.removeAttrs ps (builtins.filter
    (n: builtins.substring 0 4 n != "env-") (builtins.attrNames ps)))'

drvs() { nix eval --json "$1#packages.$system" --apply "$apply"; }

head_json="$(drvs .)"

base_json='{}'
if [[ -n "$base" && ! "$base" =~ ^0+$ ]] && git cat-file -e "$base^{commit}" 2>/dev/null; then
  tree="$(mktemp -d)"
  trap 'git worktree remove --force "$tree" >/dev/null 2>&1 || true' EXIT
  git worktree add --quiet --detach "$tree" "$base"
  base_json="$(drvs "$tree" 2>/dev/null)" || {
    echo "base $base does not evaluate; checking every environment" >&2
    base_json='{}'
  }
else
  echo "no base to compare with; checking every environment" >&2
fi

jq -rn --argjson head "$head_json" --argjson base "$base_json" \
  '$head | to_entries[] | select($base[.key] != .value) | .key'
