# shellcheck shell=bash
# Save states: the overlay presses the emulator's own hotkeys; this lists the
# slots and binds the keys.

saves_states_manifest() {
  local file
  file="$(env_saves_manifest "$1")"
  [[ -f "$file" ]] || return 0
  jq -c '.states // empty' "$file"
}

# The keys the overlay's table has (states.rs), or F{slot} for the slot's
# F-key. The same list, so a key bash lets through is one Rust can press.
SAVES_KEY_RE='^(F\{slot\}|F([1-9]|1[0-2])|[A-Z0-9]|ESC|TAB|ENTER|SPACE|(LEFT|RIGHT)(CTRL|SHIFT|ALT))$'

# The manifest's slot count, 1 to 9, or nothing for anything else.
saves_states_slots() {
  local slots
  slots="$(jq -r '.slots // 1' <<<"$1")"
  [[ "$slots" =~ ^[1-9]$ ]] && printf '%s' "$slots"
  return 0
}

# What `gotg-killswitch --states` is given. Nothing when any key is not one
# the overlay knows: a spec it cannot press would be a row that does nothing.
saves_states_spec() {
  local manifest slots save load key
  manifest="$(saves_states_manifest "$1")"
  [[ -n "$manifest" ]] || return 0
  slots="$(saves_states_slots "$manifest")"
  [[ -n "$slots" ]] || return 0
  local keys=()
  mapfile -t keys < <(jq -r '(.save // [])[], (.load // [])[]' <<<"$manifest")
  for key in "${keys[@]}"; do
    [[ "$key" =~ $SAVES_KEY_RE ]] || return 0
  done
  save="$(jq -r '(.save // []) | join("+")' <<<"$manifest")"
  load="$(jq -r '(.load // []) | join("+")' <<<"$manifest")"
  [[ -n "$save" && -n "$load" ]] || return 0
  printf 'slots=%s;save=%s;load=%s' "$slots" "$save" "$load"
}

saves_cmd_states() {
  local want="" variant="" form="say" arg
  for arg in "$@"; do
    case "$arg" in
      --json) form="json" ;;
      --lines) form="lines" ;;
      *) if [[ -z "$want" ]]; then want="$arg"; else variant="$arg"; fi ;;
    esac
  done
  [[ -n "$want" ]] || die "which game? usage: gotg saves states <id> [variant] [--lines|--json]"
  local attrs=() attr list
  mapfile -t attrs < <(saves_resolve "$want" "$variant")
  for attr in "${attrs[@]}"; do
    list="$(saves_states_one "$attr")"
    case "$form" in
      json) printf '%s\n' "$list" ;;
      lines) saves_states_lines "$list" ;;
      *) saves_states_say "$list" ;;
    esac
  done
}

# Every slot: its number, the game in it and when it was written (empty for
# an empty slot). `{game}` in the files glob is the game being played
# (GOTG_PLAY_TARGET's stem) -- how ares names states, and what keeps one
# game's slots out of another's in an environment several share -- or any
# game when nothing is being played.
saves_states_one() {
  local attr="$1" manifest state files slots slot pattern game="*" newest name at
  manifest="$(saves_states_manifest "$attr")"
  state="$(env_state_dir "$attr")"
  if [[ -z "$manifest" ]]; then
    jq -nc --arg attr "$attr" '{attr: $attr, slots: []}'
    return 0
  fi
  files="$(jq -r '.files // empty' <<<"$manifest")"
  # Under the state directory only.
  case "/$files/" in */../* | //*) files="" ;; esac
  slots="$(saves_states_slots "$manifest")"
  : "${slots:=1}"
  if [[ -n "${GOTG_PLAY_TARGET:-}" ]]; then
    game="${GOTG_PLAY_TARGET##*/}"
    game="${game%.*}"
  fi
  {
    for ((slot = 1; slot <= slots; slot++)); do
      pattern="${files//\{slot\}/$slot}"
      pattern="${pattern//\{slot2\}/$(printf '%02d' "$slot")}"
      pattern="${pattern//\{game\}/$game}"
      newest="$(saves_states_newest "$state" "$pattern")"
      name="" at=""
      if [[ -n "$newest" ]]; then
        name="${newest##*/}"
        name="${name%.*}"
        at="$(date -u -d "@$(stat -c '%Y' -- "$state/$newest")" +%Y-%m-%dT%H:%M:%SZ)"
      fi
      jq -nc --argjson slot "$slot" --arg name "$name" --arg at "$at" \
        '{id: "slot:\($slot)", slot: $slot, name: $name, written_at: $at}'
    done
  } | jq -sc --arg attr "$attr" '{attr: $attr, slots: .}'
}

# The newest file matching `pattern` under `state`, relative to it.
saves_states_newest() {
  local state="$1" pattern="$2"
  [[ -d "$state" && -n "$pattern" ]] || return 0
  (
    cd "$state" || exit 0
    shopt -s globstar nullglob
    local matches=() match
    # shellcheck disable=SC2086 # the manifest entry is a glob on purpose
    for match in $pattern; do
      [[ -f "$match" && ! -L "$match" ]] && matches+=("$match")
    done
    [[ ${#matches[@]} -gt 0 ]] || exit 0
    stat -c '%Y %n' -- "${matches[@]}" | sort -rn | first_line | cut -d' ' -f2-
  )
}

# For the overlay: `slot:N`, when as a person reads it, the game; tab-separated.
saves_states_lines() {
  local list="$1" id at name
  while IFS=$'\t' read -r id at name; do
    [[ -n "$at" ]] && at="$(saves_when "$at")"
    printf '%s\t%s\t%s\n' "$id" "$at" "$(printf '%s' "$name" | tr '\t\n\r' '   ' | cut -c1-80)"
  done < <(jq -r '.slots[] | [.id, .written_at, .name] | @tsv' <<<"$list")
}

saves_states_say() {
  local list="$1"
  printf '%s%s%s\n' "$C_HEAD" "$(jq -r .attr <<<"$list")" "$C_RESET"
  jq -r 'if (.slots | length) == 0 then "  this emulator has no save states" else empty end' <<<"$list"
  jq -r '.slots[] | "  slot \(.slot)\t" + (if .written_at == "" then "empty" else "\(.written_at)  \(.name)" end)' <<<"$list"
}

# ares' keyboard, as its SDL input driver numbers the keys: Escape is 0 and
# F1 to F12 are 1 to 12, bound as `0x1/0/<n>`.
ares_key_index() {
  case "$1" in
    F[1-9] | F1[0-2]) printf '%s' "${1#F}" ;;
    *) return 1 ;;
  esac
}

# Bind SaveState and LoadState in ares' Hotkey block to the environment's
# keys, where nobody has bound them: a person's own binding stays. ares merges
# a well-formed block it did not write and rewrites the file on exit.
ares_hotkeys_ensure() {
  local attr="$1" manifest file save load
  manifest="$(saves_states_manifest "$attr")"
  [[ -n "$manifest" && "$(jq -r '.hotkeys // empty' <<<"$manifest")" == "ares" ]] || return 0
  save="$(ares_key_index "$(jq -r '.save[0] // empty' <<<"$manifest")")" || return 0
  load="$(ares_key_index "$(jq -r '.load[0] // empty' <<<"$manifest")")" || return 0
  file="$(env_state_dir "$attr")/data/ares/settings.bml"
  mkdir -p "$(dirname "$file")"
  [[ -f "$file" ]] || : >"$file"
  if [[ -s "$file" && "$(tail -c1 "$file")" != $'\n' ]]; then
    printf '\n' >>"$file"
  fi
  # `load` is a gawk builtin, so the variables are named otherwise.
  awk -v saveId="0x1/0/$save" -v loadId="0x1/0/$load" '
    /^[A-Za-z]/ { block = $1 }
    block == "Hotkey" { seen = 1 }
    block == "Hotkey" && $0 == "  SaveState: ;;" { print "  SaveState: " saveId ";;"; next }
    block == "Hotkey" && $0 == "  LoadState: ;;" { print "  LoadState: " loadId ";;"; next }
    { print }
    END {
      if (!seen) {
        print "Hotkey"
        print "  SaveState: " saveId ";;"
        print "  LoadState: " loadId ";;"
      }
    }
  ' "$file" >"$file.gotg-tmp" || {
    rm -f "$file.gotg-tmp"
    return 1
  }
  if cmp -s "$file" "$file.gotg-tmp"; then
    rm -f "$file.gotg-tmp"
  else
    mv "$file.gotg-tmp" "$file"
  fi
}

states_prepare() {
  ares_hotkeys_ensure "$1"
}
