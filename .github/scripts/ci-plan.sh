#!/usr/bin/env bash
# Decide what CI builds: each listed check, and each env-* package, whose
# drvPath differs from <base>'s. The same drvPath is the same build, so its
# result is already known from the base's run. Evaluating every check,
# package and devShell here is also the "flake evaluates" gate.
#
#   ci-plan.sh <base-rev|""> <check>...      prints {"checks":[…],"envs":[…]}
#
# No usable base (empty, all zeros, not fetchable, does not evaluate) means
# build everything listed. Import-from-derivation is off so planning takes
# seconds, not minutes: a listed check that needs it is built (its job
# evaluates it), anything else that needs it is evaluated here with it on.
set -euo pipefail

base="${1:-}"
shift
listed="$(jq -nc '$ARGS.positional' --args "$@")"
system="${SYSTEM:-x86_64-linux}"
nej="${NIX_EVAL_JOBS:-nix-eval-jobs}"
read -ra nej_flags <<<"${NIX_EVAL_JOBS_FLAGS:-}"

log() { echo "$*" >&2; }

work="$(mktemp -d)"
tree=""
cleanup() {
  [[ -z "$tree" ]] || git worktree remove --force "$tree" >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT

# Writes $work/<name>.jsonl: {"key":"checks.ruff","value":drvPath|null,"error":…}
# per derivation. shallow=1 since CI checks out a single commit.
evaluate() {
  local dir="$1" name="$2" rev
  rev="$(git -C "$dir" rev-parse HEAD)"
  "$nej" "${nej_flags[@]}" --workers 1 \
    --option allow-import-from-derivation false \
    --expr "let
      f = builtins.getFlake \"git+file://$dir?shallow=1&rev=$rev\";
      r = a: a // { recurseForDerivations = true; };
    in {
      checks = r f.checks.$system;
      packages = r f.packages.$system;
      devShells = r f.devShells.$system;
    }" 2>"$work/$name.err" |
    jq -c '{key: (.attrPath | join(".")), value: (.drvPath // null), error}' >"$work/$name.jsonl" || true
  [[ -s "$work/$name.jsonl" ]] || { grep -v 'evaluation warning' "$work/$name.err" | tail -20 >&2; return 1; }
}

: >"$work/base.jsonl"
base_pid=""
if [[ -n "$base" && ! "$base" =~ ^0+$ ]] &&
  { git cat-file -e "$base^{commit}" 2>/dev/null ||
    git fetch --quiet --no-tags --depth=1 origin "$base" 2>/dev/null; }; then
  tree="$work/base-tree"
  git worktree add --quiet --detach "$tree" "$base"
  evaluate "$tree" base &
  base_pid=$!
else
  log "no base to compare with; building everything"
fi

evaluate "$PWD" head || { log "the flake does not evaluate at HEAD"; exit 1; }

if [[ -n "$base_pid" ]] && ! wait "$base_pid"; then
  log "base $base does not evaluate; building everything"
  : >"$work/base.jsonl"
fi

# What failed without IFD and is not a listed check gets one more try with it;
# a real evaluation error fails the plan, as `flake evaluates` used to.
jq -r --argjson listed "$listed" '
  select(.error) | .key
  | . as $k | select(($k | startswith("checks.")) and ($listed | index($k | ltrimstr("checks."))) | not)' \
  "$work/head.jsonl" >"$work/retry"
while read -r attr; do
  log "evaluating $attr with import-from-derivation"
  nix eval --raw ".#${attr%%.*}.$system.${attr#*.}.drvPath" >/dev/null
done <"$work/retry"

jq -sc --argjson listed "$listed" --slurpfile base "$work/base.jsonl" '
  from_entries as $head | ($base | from_entries) as $base
  | def changed(k): $head[k] == null or $head[k] != $base[k];
  {
    checks: [$listed[] | select(changed("checks." + .))],
    envs: [$head | keys[] | select(startswith("packages.env-")) | select(changed(.))
           | ltrimstr("packages.")]
  }' "$work/head.jsonl"
