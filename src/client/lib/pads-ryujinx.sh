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
  pads_ryujinx_seed "$attr"
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
