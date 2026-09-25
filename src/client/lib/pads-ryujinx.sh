# shellcheck shell=bash
# Ryujinx controller bindings: kept, rather than generated.
#
# Ryujinx never unbinds a pad on hotplug — a disconnect mid-game rebinds by
# itself when the pad returns, because the id it stores is stable for a lone
# pad of a given model. The unbind that people see is its input settings
# screen: a pad that goes to sleep while that window is open flips the player
# to "Disabled", and saving in that state deletes the player's entry from
# Config.json entirely. Read off the Ryubing source (InputViewModel: a
# disconnect sets IsModified, and Save drops any player whose Device is 0).
#
# So what a Ryujinx environment needs is not generated bindings but a memory:
# whatever bindings were last seen working are kept beside the config, and a
# config that has lost them gets them back before the launch. Bindings made in
# `gotg configure` become the thing restored, and no settings-screen accident
# survives past the next launch.
#
# The bindings themselves are still Ryujinx's to write — a mapping copied from
# the emulator is a mapping that works — but the *id* is now ours to compute
# when nothing here has one. That was not safe while the emulator carried its
# own SDL 2.30.0 and the launcher ran SDL3: two SDL majors do not agree about a
# device's GUID, and a wrong id is a pad that silently has no buttons.
# pkgs/ryubing points the emulator's libSDL2 at sdl2-compat, so both sides now
# ask the same library the same question, and pads_ryujinx_id renders the
# answer the way Ryujinx does.

pads_ryujinx_config() { printf '%s/config/Ryujinx/Config.json' "$(env_state_dir "$1")"; }

pads_ryujinx_snapshot() { printf '%s/input-config.json' "$(env_state_dir "$1")"; }

pads_ryujinx_configure() {
  local attr="$1"
  pads_ryujinx_seed "$attr"
  pads_ryujinx_rebind "$attr"
  pads_ryujinx_keep "$attr"
  # Twice around the keep, so the snapshot carries the motion block from this
  # launch rather than from the next one: a config restored a launch later
  # would otherwise arrive with motion off and turn it on again, which reads in
  # the log as something flapping.
  local changed=0
  # danstick's writer binds its seated pads and points each at its DSU server
  # for motion; the SDL-sensor path below is for a pad that is not danstick's.
  danstick_emit "$attr" && changed=1
  pads_ryujinx_motion "$attr" && changed=1
  if ((changed)); then
    pads_ryujinx_keep "$attr"
  fi
  # Never fatal, like every other binding writer: no pad attached, or one SDL
  # cannot read, is a launch without motion rather than no launch.
  return 0
}

# Ryujinx's untouched default: a keyboard bound as player 1 and no pad at all.
# Worth naming, because it is not a choice anyone made — it is what the emulator
# writes on a first run — and both halves of this file need to tell it apart
# from bindings somebody has. Remembering it as a preference is how a new
# environment would pin its own padlessness on launch one and keep it.
pads_ryujinx_is_default() {
  local config="$1"
  jq -e '(.input_config // []) as $c
         | ($c | length) == 0
           or ($c | length) == 1 and $c[0].backend == "WindowKeyboard" and $c[0].id == "0"' \
    "$config" >/dev/null 2>&1
}

# A pad this environment has never been told about, taken from one that has.
#
# Bindings are per environment, and every variant of a game is its own — so a
# player who bound a pad once, for the platform, met Ryujinx's keyboard default
# again the first time they opened a mod. Which looks exactly like "the game
# does not see my controller", and was.
#
# The id Ryujinx stores is the pad's, not the environment's: the same puck
# produces the same id under every config on the machine. So a sibling's
# snapshot is not a guess, it is the same answer already written down — and
# copying it stays inside this file's rule of keeping what Ryujinx wrote rather
# than generating an id ourselves.
#
# Only into an environment that has nothing of its own. A snapshot here means
# this environment has been bound before, and whatever it says wins.
pads_ryujinx_seed() {
  local attr="$1" config snap source
  snap="$(pads_ryujinx_snapshot "$attr")"
  # A snapshot with nothing but a keyboard in it is not bindings either. Older
  # clients wrote those, before the default was told apart from a choice, and
  # an environment carrying one is exactly an environment that never had a pad.
  pads_ryujinx_has_pad "$snap" && return 0

  # The `|| source=` belongs out here: with no sibling the lookup exits
  # non-zero, and under errexit a bare assignment from it takes the launch down
  # with it — which is every environment on a machine with one pad bound once.
  source="$(pads_ryujinx_sibling "$attr")" || source=""
  [[ -n "$source" ]] || return 0

  # An environment that has never run has no state directory yet, which is
  # exactly the case this exists for.
  mkdir -p -- "$(dirname "$snap")" || return 0
  cp -- "$source" "$snap.part" && mv "$snap.part" "$snap" || return 0

  # And into the config, when there is one to write into. On a true first
  # launch there is not — the emulator writes it minutes from now, and the
  # platform's own preLaunch restores this snapshot the moment it does.
  config="$(pads_ryujinx_config "$attr")"
  [[ -f "$config" ]] || return 0
  pads_ryujinx_is_default "$config" || return 0
  if jq --slurpfile saved "$snap" '.input_config = $saved[0]' "$config" \
    >"$config.part" 2>/dev/null && [[ -s "$config.part" ]]; then
    mv "$config.part" "$config"
    local from="${source%/input-config.json}"
    log "took the controller bindings from ${from##*/}"
  else
    rm -f "$config.part"
  fi
}

# The most recently used environment of the same platform that has a pad bound.
# Same platform means same emulator, which is what makes the snapshot readable
# here at all; most recent, because it is the one whose bindings the player last
# saw working.
pads_ryujinx_sibling() {
  local attr="$1" platform state mine snap
  # "env-switch-world_zelda-60fps" and "env-switch" are both the switch family;
  # a platform slug holds no dash, which is what makes this a prefix and not a
  # parse.
  platform="${attr#env-}"
  platform="${platform%%-*}"
  state="${GOTG_ENV_STATE_DIR:-$GOTG_STATE_DIR/env}"
  mine="$(pads_ryujinx_snapshot "$attr")"

  while IFS= read -r snap; do
    [[ "$snap" != "$mine" ]] || continue
    pads_ryujinx_has_pad "$snap" || continue
    printf '%s' "$snap"
    return 0
  done < <(ls -t -- "$state/env-$platform"*/input-config.json 2>/dev/null)
  return 1
}

# Does this snapshot name a pad, rather than only the keyboard Ryujinx binds
# when nobody has bound anything?
pads_ryujinx_has_pad() {
  [[ -s "$1" ]] || return 1
  jq -e '[.[]? | .backend // "" | . != "WindowKeyboard"] | any' "$1" >/dev/null 2>&1
}

# The id Ryujinx stores for a pad, from the identity SDL hands the launcher.
#
# Two renderings meet here. SDL's GUID is a byte string; .NET's Guid prints its
# first three fields in the machine's own byte order, so the bytes come back
# shuffled — and Ryujinx zeroes SDL's CRC16 of the device name first, so that
# renaming a pad cannot orphan its bindings. The slot is what SDL calls the
# device index, and Ryujinx puts it in front.
#
# Verified against the running emulator: with this id in the config and nothing
# else changed, Paper Mario stopped opening its controller applet and went
# straight into the game.
pads_ryujinx_id() {
  local guid="$1" slot="$2" b=() i
  [[ "$guid" =~ ^[0-9a-fA-F]{32}$ ]] || return 1
  [[ "$slot" =~ ^[0-9]+$ ]] || return 1
  for ((i = 0; i < 32; i += 2)); do b+=("${guid:i:2}"); done
  printf '%s-0000%s%s-%s%s-%s%s-%s%s-%s%s%s%s%s%s' "$slot" \
    "${b[1]}" "${b[0]}" "${b[5]}" "${b[4]}" "${b[7]}" "${b[6]}" \
    "${b[8]}" "${b[9]}" "${b[10]}" "${b[11]}" "${b[12]}" "${b[13]}" "${b[14]}" "${b[15]}"
}

# A binding that names a pad which is not here, when one is.
#
# Ryujinx matches a player to a pad by that id and nothing else, so a binding
# left over from a different device is a player with no buttons and no message
# — which is what a Steam Controller bound through Steam's virtual gamepad
# became the moment the emulator could read the puck itself.
#
# Only when the stored id matches *nothing* attached. A pad that is merely
# asleep keeps its seat: its id is still the id it will have when it wakes, and
# stealing player one from it would be this function inventing a preference.
#
# The remembered bindings are repointed with the live ones. A snapshot is what
# a launch restores before the emulator has read anything, so a memory of a
# device that is not here is the same failure one launch later.
# shellcheck disable=SC2016  # a jq program: $attached and friends are jq's
GOTG_RYUJINX_REPOINT='
  map(
    if .backend == "GamepadSDL2"
       and (.player_index // "Player1") == "Player1"
       and ((.id // "") as $id | any($attached[]; . == $id) | not)
    then .id = $want
       | .name = (if $name == "" then .name else "\($name) (\($slot))" end)
    else . end
  )'

pads_ryujinx_rebind() {
  local attr="$1" pads seated attached guid slot name want

  pads="$("$(pads_bin)" 2>/dev/null)" || return 0
  seated="$(pads_seating "$pads" 2>/dev/null)" || return 0
  [[ -n "$seated" && "$seated" != "[]" ]] || return 0

  # Every attached pad's id, so that a config already naming one of them is
  # left alone whichever seat it took.
  attached="[]"
  while IFS=$'\t' read -r guid slot; do
    [[ -n "$guid" ]] || continue
    want="$(pads_ryujinx_id "$guid" "$slot")" || continue
    attached="$(jq -c --arg id "$want" '. + [$id]' <<<"$attached")"
  done < <(jq -r '.[] | "\(.identity)\t\(.slot)"' <<<"$seated")
  [[ "$attached" != "[]" ]] || return 0

  IFS=$'\t' read -r guid slot name < <(
    jq -r '.[0] | "\(.identity)\t\(.slot)\t\(.name // "")"' <<<"$seated"
  )
  want="$(pads_ryujinx_id "$guid" "$slot")" || return 0

  pads_ryujinx_repoint "$(pads_ryujinx_config "$attr")" \
    ".input_config |= $GOTG_RYUJINX_REPOINT" "$attached" "$want" "$name" "$slot" &&
    log "player one was bound to a controller that is not here; pointed it at $name"
  pads_ryujinx_repoint "$(pads_ryujinx_snapshot "$attr")" \
    "$GOTG_RYUJINX_REPOINT" "$attached" "$want" "$name" "$slot" || true
  return 0
}

# Apply that filter to one file, in place, and say whether anything moved.
pads_ryujinx_repoint() {
  local file="$1" filter="$2" attached="$3" want="$4" name="$5" slot="$6" updated
  [[ -s "$file" ]] || return 1

  updated="$(
    jq --argjson attached "$attached" --arg want "$want" --arg name "$name" \
      --arg slot "$slot" "$filter" "$file" 2>/dev/null
  )" || return 1
  [[ -n "$updated" ]] || return 1
  jq -e --slurpfile before "$file" '. != $before[0]' <<<"$updated" >/dev/null 2>&1 || return 1

  printf '%s\n' "$updated" >"$file.part" && mv "$file.part" "$file"
}

pads_ryujinx_keep() {
  local attr="$1" config snap bound
  config="$(pads_ryujinx_config "$attr")"
  snap="$(pads_ryujinx_snapshot "$attr")"

  # No config yet means the environment has never launched; the emulator writes
  # its full default on that first run, and there is nothing to keep or heal.
  [[ -f "$config" ]] || return 0

  # An unreadable config is not ours to fix — the emulator will replace it with
  # a default and say so, which is a better failure than us guessing at JSON.
  bound="$(jq -ce '.input_config // []' "$config" 2>/dev/null)" || return 0

  # The untouched default is not bindings; see above. Treated as the empty set
  # so that it is neither remembered nor allowed to stand where a snapshot has
  # something real.
  pads_ryujinx_is_default "$config" && bound="[]"

  if [[ "$bound" != "[]" ]]; then
    # This runs on every launch; an unchanged snapshot is not rewritten.
    [[ -f "$snap" && "$bound" == "$(<"$snap")" ]] && return 0
    printf '%s\n' "$bound" >"$snap.part" && mv "$snap.part" "$snap"
    return 0
  fi

  # A config with no bindings and a snapshot that has some: the settings screen
  # (or an id format change in an upgrade) lost them. An empty set is never
  # what anyone wants at launch, so last-known-good wins over deliberate-blank.
  [[ -s "$snap" ]] || return 0
  if jq --slurpfile saved "$snap" '.input_config = $saved[0]' "$config" \
    >"$config.part" 2>/dev/null && [[ -s "$config.part" ]]; then
    mv "$config.part" "$config"
    log "restored the controller bindings Ryujinx had lost"
  else
    rm -f "$config.part"
  fi
}

# Ryujinx's own defaults for the two numbers a motion block needs, so a player
# who has never opened the motion page gets what that page would have offered.
# From GamepadInputConfig: sensitivity 100, gyro deadzone 1.
GOTG_RYUJINX_GYRO_SENSITIVITY=100
GOTG_RYUJINX_GYRO_DEADZONE=1

# Turn the gyro on for a pad that has one.
#
# Motion is off in a fresh Ryujinx config and there is no global switch for it:
# it is four keys inside each player's own entry, and a pad bound before those
# keys existed has none of them. So a Switch game that wants motion — Skyward
# Sword's flying, Splatoon's aiming — is unplayable with a controller that has
# the hardware, until somebody finds the motion page.
#
# The pad decides, not the config: `motion_backend: GamepadDriver` is Ryujinx
# reading SDL's own sensors, and SDL only reports them for a controller that
# has both. The current Steam Controller does — SDL3's hidapi driver for it
# (SDL_hidapi_steam_triton.c) registers gyro and accelerometer at 248Hz — which
# is the whole reason this is reachable without a DSU server in between.
#
# Returns non-zero when nothing changed, so the caller can skip re-snapshotting.
pads_ryujinx_motion() {
  local attr="$1" config pads names updated
  config="$(pads_ryujinx_config "$attr")"
  [[ -f "$config" ]] || return 1

  pads="$("$(pads_bin)" 2>/dev/null)" || return 1
  names="$(jq -c '[.[] | select(.motion) | .name | select(. != null)]' <<<"$pads")" || return 1
  [[ "$names" != "[]" ]] || return 1

  # Matched on the name because the id cannot be reproduced safely — see the
  # note at the top of this file. Ryujinx stores "<SDL name> (<n>)", truncated
  # to 50 characters with an ellipsis, so the stored name is turned back into
  # its prefix and the SDL name is asked whether it starts with it.
  updated="$(
    jq --argjson names "$names" '
      def base: sub(" \\([0-9]+\\)$"; "") | sub("\\.\\.\\.$"; "");
      def wants_motion:
        (.name // "") as $stored
        | ($stored | base) as $prefix
        | $prefix != "" and any($names[]; startswith($prefix));
      # A player who set up a DSU server chose a different source for the
      # same thing; that choice is theirs and is left alone. But only a
      # CemuHook block that names a server is a choice: the settings page
      # saves the checkbox with an empty host field too, and that is motion
      # polling a null address forever — Ryujinx warns "Unable to register
      # motion client" every five seconds — while a working gyro sits idle.
      def chose_dsu:
        (.motion.motion_backend // "") == "CemuHook"
        and (.motion.dsu_server_host // "") != "";
      .input_config = [
        .input_config[]
        | if .backend == "GamepadSDL2"
             and wants_motion
             and (chose_dsu | not)
          then
            # Built fresh rather than merged, so healing a serverless CemuHook
            # block sheds its DSU fields instead of carrying them along.
            .motion = {
              motion_backend: "GamepadDriver",
              enable_motion: true,
              sensitivity: (.motion.sensitivity // '"$GOTG_RYUJINX_GYRO_SENSITIVITY"'),
              gyro_deadzone: (.motion.gyro_deadzone // '"$GOTG_RYUJINX_GYRO_DEADZONE"')
            }
          else . end
      ]
    ' "$config" 2>/dev/null
  )" || return 1
  [[ -n "$updated" ]] || return 1

  # Which entries actually moved, compared on the parsed value rather than on
  # the text: this runs on every launch, and jq's own formatting differs from
  # Ryujinx's, so comparing the files would rewrite a config that already says
  # what it should and report it as a change every time.
  local before changed
  before="$(jq -c '.input_config' "$config")" || return 1
  changed="$(
    jq -r --argjson before "$before" '
      [ .input_config | to_entries[]
        | select(.value.motion != ($before[.key].motion // null))
        | .value.name // "a controller" ]
      | join(", ")
    ' <<<"$updated"
  )" || return 1
  [[ -n "$changed" ]] || return 1

  printf '%s\n' "$updated" >"$config.part" || return 1
  mv "$config.part" "$config" || return 1
  log "motion controls enabled for $changed"
}
