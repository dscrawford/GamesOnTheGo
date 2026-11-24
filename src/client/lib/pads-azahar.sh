# shellcheck shell=bash
# Azahar's bindings: player one's pad as the 3DS. Its `api:controller` takes
# SDL2's gamepad numbering, so no per-model table; `port` is gotg-pads' slot.

# 3DS control -> SDL2 GameController button. Laid out by position, as a 3DS
# is: A is the right face button, B the bottom one.
GOTG_AZAHAR_BUTTONS=(
  button_a=1 button_b=0 button_x=3 button_y=2
  button_up=11 button_down=12 button_left=13 button_right=14
  button_l=9 button_r=10 button_start=6 button_select=4 button_home=5
)

pads_azahar_ini() { printf '%s/config/azahar-emu/qt-config.ini' "$(env_state_dir "$1")"; }

# The [Controls] lines for one pad, as key=value. Qt reads an unquoted value
# with commas in it as a list, so each binding is quoted.
pads_azahar_lines() {
  local guid="$1" port="$2" motion="$3" pad entry
  pad="engine:sdl,api:controller,guid:$guid,port:$port"
  printf 'profile=0\nprofiles\\size=1\n'
  for entry in "${GOTG_AZAHAR_BUTTONS[@]}"; do
    printf 'profiles\\1\\%s="%s,button:%s"\n' "${entry%%=*}" "$pad" "${entry#*=}"
  done
  printf 'profiles\\1\\button_zl="%s,axis:4,direction:+,threshold:0.5"\n' "$pad"
  printf 'profiles\\1\\button_zr="%s,axis:5,direction:+,threshold:0.5"\n' "$pad"
  printf 'profiles\\1\\circle_pad="%s,axis_x:0,axis_y:1,deadzone:0.1"\n' "$pad"
  printf 'profiles\\1\\c_stick="%s,axis_x:2,axis_y:3,deadzone:0.1"\n' "$pad"
  # A pad without a gyro keeps the mouse-driven motion Azahar defaults to.
  if [[ "$motion" == true ]]; then
    printf 'profiles\\1\\motion_device="engine:sdl,guid:%s,port:%s"\n' "$guid" "$port"
  fi
}

# Set each key=value on stdin in [Controls], dropping its old value and the
# `\default` marker that would make Azahar ignore it.
pads_azahar_rewrite() {
  local file="$1" lines
  lines="$(cat)"
  mkdir -p "${file%/*}" && touch "$file" || return 1
  # shellcheck disable=SC2016 # an awk program
  GOTG_LINES="$lines" atomic_write "$file" -- awk '
    BEGIN {
      n = split(ENVIRON["GOTG_LINES"], lines, "\n")
      for (i = 1; i <= n; i++) { k = lines[i]; sub(/=.*/, "", k); keys[k] = 1 }
    }
    function emit(   i) { for (i = 1; i <= n; i++) print lines[i]; done = 1 }
    /^\[/ {
      if (inside && !done) emit()
      inside = ($0 == "[Controls]")
      print
      next
    }
    inside {
      k = $0; sub(/=.*/, "", k); sub(/\\default$/, "", k)
      if (k in keys) next
    }
    { print }
    END {
      if (!done) { if (!inside) print "[Controls]"; emit() }
    }
  ' "$file"
}

# Never fatal: a launch on Azahar's keyboard defaults beats no launch.
pads_azahar_configure() {
  local attr="$1" pads seating first guid slot motion name
  pads="$(pads_enumerate)" || return 0
  seating="$(pads_seating "$pads")"
  first="$(jq -c '.[0] // empty' <<<"$seating")"
  [[ -n "$first" ]] || return 0
  IFS=$'\t' read -r guid slot motion name < <(jq -r '[.guid, .slot, (.motion // false), .name] | @tsv' <<<"$first")
  [[ "$guid" =~ ^[0-9a-fA-F]{32}$ && "$slot" =~ ^[0-9]+$ ]] || return 0

  pads_azahar_lines "$guid" "$slot" "$motion" | pads_azahar_rewrite "$(pads_azahar_ini "$attr")" ||
    {
      warn "could not write bindings for $attr"
      return 0
    }
  log "player 1: $name -> 3DS"
}
