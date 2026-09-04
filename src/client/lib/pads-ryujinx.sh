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
# Deliberately not generated from SDL the way ares and dolphin bindings are:
# the id Ryujinx stores renders SDL's GUID through .NET's own byte order with
# its first four hex digits zeroed, and a wrong id is a pad that silently has
# no buttons. Copying an id Ryujinx itself wrote sidesteps every one of those
# details.

pads_ryujinx_config() { printf '%s/config/Ryujinx/Config.json' "$(env_state_dir "$1")"; }

pads_ryujinx_snapshot() { printf '%s/input-config.json' "$(env_state_dir "$1")"; }

pads_ryujinx_configure() {
  local attr="$1"
  pads_ryujinx_keep "$attr"
  # Twice around the keep, so the snapshot carries the motion block from this
  # launch rather than from the next one: a config restored a launch later
  # would otherwise arrive with motion off and turn it on again, which reads in
  # the log as something flapping.
  if pads_ryujinx_motion "$attr"; then
    pads_ryujinx_keep "$attr"
  fi
  pads_ryujinx_warn_steam "$attr"
  # Never fatal, like every other binding writer: no pad attached, or one SDL
  # cannot read, is a launch without motion rather than no launch.
  return 0
}

# A Steam Controller is a gamepad only while Steam runs; without it the puck
# stays in lizard mode and Ryujinx sees no pad at all. A Switch game then
# opens its "connect a controller" screen, which Ryujinx cannot show — it
# throws twenty seconds in. Said before the launch, where it can be acted on.
pads_ryujinx_warn_steam() {
  local attr="$1" config
  config="$(pads_ryujinx_config "$attr")"
  [[ -f "$config" ]] || return 0
  jq -e '[.input_config[]? | .id // "" | test("28de")] | any' "$config" >/dev/null 2>&1 || return 0
  if [[ -n "${GOTG_STEAM_RUNNING:-}" ]]; then
    [[ "$GOTG_STEAM_RUNNING" == "1" ]] && return 0
  elif pgrep -x steam >/dev/null 2>&1; then
    return 0
  fi
  warn "Steam is not running: the Steam Controller stays a keyboard and Ryujinx will find no pad,
         so the game will stop on its controller screen. Start Steam, or bind another pad:
         gotg controllers order --set <pad> && gotg controllers apply <id>"
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
