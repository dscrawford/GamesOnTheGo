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
