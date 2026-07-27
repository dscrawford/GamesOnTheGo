# shellcheck shell=bash
# Which emulator runs a game, and where its binary lives.
#
# Every emulator is built ahead of time into a GC-rooted symlink under
# ~/.local/state/gotg/roots. Launching only ever execs a store path that already
# exists: Steam's launch environment breaks nix evaluation, so a `nix run` at
# launch time fails with no visible error and the game just closes.

# The flake that pins the emulator versions. Falls back to plain nixpkgs when the
# checkout is not around, which costs pinning but still launches a game.
gotg_flake() {
  local candidate="${GOTG_FLAKE:-}"
  [[ -z "$candidate" ]] && candidate="$(config_get flake 2>/dev/null || true)"
  [[ -z "$candidate" ]] && candidate="$HOME/Documents/GOTG"
  printf '%s' "$candidate"
}

emulators_json() { printf '%s/emulators.json' "$GOTG_DATA"; }
platforms_json() { printf '%s/platforms.json' "$GOTG_DATA"; }

# Per-game overrides are the one thing a person tweaks per machine, so a copy in
# the config directory wins over the one shipped in the store.
overrides_json() {
  if [[ -f "$GOTG_CONFIG_DIR/overrides.json" ]]; then
    printf '%s/overrides.json' "$GOTG_CONFIG_DIR"
  else
    printf '%s/overrides.json' "$GOTG_DATA"
  fi
}

# Per-game overrides, looked up by "platform/id" first then bare id.
override_field() {
  local game="$1" field="$2" id platform
  id="$(manifest_field "$game" id)"
  platform="$(manifest_field "$game" platform)"
  jq -r --arg k1 "$platform/$id" --arg k2 "$id" --arg f "$field" \
    '(.[$k1][$f] // .[$k2][$f]) // empty' "$(overrides_json)"
}

emulator_for_game() {
  local game="$1" name platform
  name="$(override_field "$game" emulator)"
  if [[ -z "$name" ]]; then
    platform="$(manifest_field "$game" platform)"
    name="$(jq -r --arg p "$platform" '.[$p].emulator // empty' "$(platforms_json)")"
  fi
  [[ -n "$name" ]] || die "no emulator configured for platform '$(manifest_field "$game" platform)'.
     Add one to $(platforms_json), or set an override in $(overrides_json)."
  printf '%s' "$name"
}

emulator_spec() {
  local name="$1" field="$2" value
  value="$(jq -r --arg n "$name" --arg f "$field" '.[$n][$f] // empty' "$(emulators_json)")"
  [[ -n "$value" ]] || die "emulator '$name' has no '$field' in $(emulators_json)"
  printf '%s' "$value"
}

emulator_root() { printf '%s/%s' "$GOTG_ROOTS_DIR" "$1"; }

emulator_bin() {
  local name="$1" bin
  bin="$(emulator_spec "$name" bin)"
  printf '%s/bin/%s' "$(emulator_root "$name")" "$bin"
}

emulator_is_built() { [[ -x "$(emulator_bin "$1")" ]]; }

# Build an emulator and keep it alive with a GC root. Terminal-only: this is the
# step that needs nix evaluation.
emulator_build() {
  local name="$1" attr root flake
  attr="$(emulator_spec "$name" attr)"
  root="$(emulator_root "$name")"
  flake="$(gotg_flake)"
  mkdir -p "$GOTG_ROOTS_DIR"

  local ref
  if [[ -f "$flake/flake.nix" ]]; then
    ref="$flake#$name"
  else
    warn "no flake at $flake; building $attr from your nixpkgs instead of the pinned version"
    ref="nixpkgs#$attr"
  fi

  log "building $name ($ref) — this can take a while the first time"
  nix build "$ref" -o "$root" ||
    die "could not build $name. Run this from a terminal (not through Steam)."
  emulator_is_built "$name" ||
    die "built $name but $(emulator_bin "$name") is missing — check 'bin' in $(emulators_json)"
}

# The file handed to the emulator. Directory games need a glob (Wii U wants the
# .rpx inside code/), single-file games are just themselves.
resolve_target() {
  local game="$1" path pattern match
  path="$(game_local_path "$game")"
  [[ -e "$path" ]] || die "not installed: $path"

  pattern="$(override_field "$game" target)"
  if [[ -z "$pattern" || ! -d "$path" ]]; then
    printf '%s' "$path"
    return 0
  fi

  # shellcheck disable=SC2086 # the pattern is a glob on purpose
  match="$(find "$path" -ipath "$path/$pattern" -type f 2>/dev/null | head -n1)"
  [[ -n "$match" ]] || die "no file matching '$pattern' under $path"
  printf '%s' "$match"
}

# Build the emulator's argument list, substituting {target} and {install}.
emulator_args() {
  local game="$1" name="$2" target install template
  target="$(resolve_target "$game")"
  install="$(game_local_path "$game")"

  template="$(override_field "$game" args)"
  [[ -n "$template" ]] || template="$(jq -c --arg n "$name" '.[$n].argsTemplate // []' "$(emulators_json)")"

  jq -r --arg t "$target" --arg i "$install" \
    '.[] | gsub("\\{target\\}"; $t) | gsub("\\{install\\}"; $i)' <<<"$template"
}
