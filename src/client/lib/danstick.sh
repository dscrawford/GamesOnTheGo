# shellcheck shell=bash
# Running a game under danstick, which is what the controllers actually are.
#
# danstick republishes each physical pad through uinput as a controller whose
# identity it made -- stable across replugs, in the player order somebody chose
# by holding a button -- and captures the mapping for models SDL does not know.
# Two things have to happen for a game to see any of that:
#
#   - the daemon has to be running, or there are no virtual pads to see;
#   - the mapping has to be in the environment *before* the emulator starts,
#     because SDL reads its database once, at startup. A mapping written a
#     second later does nothing until the next launch, which is the failure
#     that looks like "it works the second time".
#
# Neither is allowed to stop a launch. A machine with no daemon, or one where
# danstick cannot reach uinput, still plays games -- with whatever SDL already
# knows, which is what it had before danstick existed.

# Start the daemon if it is not running, or restart it if it is running code
# older than what is installed.
#
# Once per process: DANSTICK_SKIP_DAEMON_CHECK is danstick's own flag for exactly
# this, and the picker sets it before launching a game so a launch from the
# grid does not ask again.
# Overridable, like every other tool this shells out to: a test needs a
# stand-in that records how it was called, and the packaged client puts the
# real one first on PATH where a test's copy cannot win.
danstick_bin() { printf '%s' "${GOTG_DANSTICK:-danstick}"; }
danstick_rs_bin() { printf '%s' "${GOTG_DANSTICK_RS:-danstick-rs}"; }

# `--no-wait` leaves the publish wait to the caller: the seat gate, which is
# what makes a fresh daemon publish anything at all, and which cannot run
# before the daemon it talks to. DANSTICK_MARKER is the moment the daemon was
# asked after, for that caller to wait against; empty when there was
# nothing started here and so nothing to wait for.
DANSTICK_MARKER=""
danstick_ensure() {
  local wait=1
  [[ "${1:-}" != "--no-wait" ]] || wait=0
  [[ "${DANSTICK_SKIP_DAEMON_CHECK:-0}" != "1" ]] || return 0
  command -v "$(danstick_bin)" >/dev/null 2>&1 || return 0
  # A moment before the daemon is asked after: anything it publishes from
  # here on is newer than this file, and anything older is a leftover.
  local marker said
  marker="$(mktemp)"
  # Unseated, and for as long as this shell -- and the emulator it execs
  # into -- lives. danstick treats the pair as a session name: a daemon already
  # following this pid, which is the picker's after its execvp, is left
  # alone; one belonging to no session, or another, is replaced.
  # No session opened by the daemon itself, and no seat but by a hold: a pad
  # danstick remembers a mapping for was seated the moment it was seen, which
  # --fresh does not stop. The daemon reads these, so they are set before it
  # is started, and left alone when somebody set them themselves.
  export DANSTICK_NO_AUTOSETUP="${DANSTICK_NO_AUTOSETUP:-1}" DANSTICK_NO_AUTOATTACH="${DANSTICK_NO_AUTOATTACH:-1}"
  # And how long a hold takes to claim a seat. The picker and the gate ask for
  # this on every `seating`, but an assignment session -- danstick's own wizard
  # -- takes the daemon's, so it is set here as well. danstick keeps its quarter
  # second for anything outside 0.05..10, and an older daemon ignores it.
  export DANSTICK_HOLD_SECONDS="${DANSTICK_HOLD_SECONDS:-1.5}"
  # Which session this daemon is, for the gate that runs next: seats taken in
  # the picker a moment ago belong to this launch -- the picker's pid is this
  # shell's, through the execvp -- and seats a daemon has held since yesterday
  # do not.
  export DANSTICK_FOLLOW="$$"
  if ! said="$("$(danstick_bin)" ensure-daemon --fresh --follow "$$" 2>&1)"; then
    warn "danstick has no current daemon; controllers will be whatever SDL finds"
    rm -f "$marker"
    # Not latched: this failure is worth asking about again. The latch used
    # to be set on the way out regardless, so one bad start at the picker
    # disabled the check for every game launched from it afterwards.
    return 0
  fi
  # Only a daemon this call started has a first publish to wait for. One
  # that was already current published long ago, or has nothing to publish
  # yet because nobody is seated, and waiting on it would be waiting on a
  # button press.
  if [[ "$said" == *"daemon up"* || "$said" == *"restarted"* ]]; then
    if ((wait)); then
      danstick_wait_published "$marker"
      rm -f "$marker"
    else
      DANSTICK_MARKER="$marker"
    fi
  else
    rm -f "$marker"
  fi
  export DANSTICK_SKIP_DAEMON_CHECK=1
}

# ensure-daemon returns once the daemon is *running*, which is before it has
# published anything. A launch on a machine where nothing had started it yet
# went straight on to `danstick-rs exec`, which read the mappings file a moment
# before it existed:
#
#     no mappings at /run/user/1000/danstick/env.sh (No such file or directory)
#     ... will see whatever SDL already knows
#
# and the game came up with no controller. A few seconds of waiting covers
# it; a daemon that never publishes gets the same warning it always did.
#
# "Published" means published by *this* daemon: the file has to be newer than
# the marker made before the daemon was started. A daemon that died in an
# earlier session leaves its file behind, and an existence test passed on
# that leftover while the new daemon had said nothing yet.
danstick_wait_published() {
  local marker="$1" file waited=0 limit="${GOTG_DANSTICK_PUBLISH_WAIT:-30}"
  file="$(danstick_runtime_dir)/env.sh"
  while [[ ! "$file" -nt "$marker" ]] && ((waited < limit)); do
    sleep 0.1
    waited=$((waited + 1))
  done
  [[ "$file" -nt "$marker" ]] || warn "danstick started but has published no controllers yet"
}

# Keep the daemon alive for as long as the game is.
#
# Modelled on killswitch_start, and for the same reason: this shell is about
# to be replaced by the emulator, so the keeper is a background process that
# polls the game's pid and exits on its own when the game is gone. What it
# watches for is the one thing that is its to fix -- no daemon at all, which
# takes the uinput clones with it and leaves the game holding nothing. A
# daemon running older code is *not* restarted: that is a sync's business,
# and swapping the daemon mid-level would drop the clones the game is using.
# ensure-daemon --check exits 1 for both, so the reason is read off what it
# prints rather than off its status.
danstick_keeper_start() {
  local pid="$1"
  [[ "${GOTG_DANSTICK_KEEPER:-1}" != "0" ]] || return 0
  command -v "$(danstick_bin)" >/dev/null 2>&1 || return 0
  (
    interval="${GOTG_DANSTICK_KEEPER_INTERVAL:-5}"
    while kill -0 "$pid" 2>/dev/null; do
      sleep "$interval"
      kill -0 "$pid" 2>/dev/null || break
      said="$("$(danstick_bin)" ensure-daemon --check 2>&1)" && continue
      [[ "$said" == *"no daemon running"* ]] || continue
      # Following the game, and not fresh: the seats it had are the ones
      # the level is being played with.
      if "$(danstick_bin)" ensure-daemon --follow "$pid" >/dev/null 2>&1; then
        warn "danstick had stopped; started it again"
      else
        warn "danstick stopped and would not start again"
      fi
    done
  ) &
}

# Where the launch-time controller check is, or nothing.
#
# On PATH first. Failing that, beside `gotg-ui`: they are built and installed
# as one package, so a machine with the picker has the check even when only the
# picker's directory made it onto PATH -- which is the ordinary case, because
# nothing puts a second entry there for a command nobody types.
danstick_seat_bin() {
  local named="${GOTG_SEAT:-}" beside dir
  if [[ -n "$named" ]]; then
    printf '%s' "$named"
    return 0
  fi
  if command -v gotg-seat >/dev/null 2>&1; then
    printf 'gotg-seat'
    return 0
  fi
  if beside="$(command -v gotg-ui 2>/dev/null)"; then
    beside="$(dirname "$(readlink -f "$beside")")/gotg-seat"
    if [[ -x "$beside" ]]; then
      printf '%s' "$beside"
      return 0
    fi
  fi
  # The Nix profiles, by name, because a launch from Steam has none of them on
  # PATH -- the same list the generated Steam launcher walks to find the picker
  # itself. This is what "the check was skipped" actually was: not a machine
  # without gotg-seat, but a PATH without the directory holding it, on the one
  # launch path where nobody sees the warning that says so.
  for dir in "$HOME/.nix-profile/bin" "$HOME/.local/state/nix/profile/bin" \
    /nix/var/nix/profiles/default/bin; do
    [[ -x "$dir/gotg-seat" ]] || continue
    printf '%s' "$dir/gotg-seat"
    return 0
  done
  return 1
}

# What Steam leaves in the environment of anything it starts, taken back out.
#
# Steam hands a game it launches SDL_GAMECONTROLLER_IGNORE_DEVICES naming the
# controllers Steam Input is handling on its behalf, so the game uses Steam's
# virtual gamepad instead of the physical one. These emulators do not speak
# Steam Input, and danstick's clones mirror the physical pad's identity -- so an
# ignore rule Steam wrote for the Steam Controller silenced its clone too, and
# Ryujinx sat on "Waiting for controller connection" for a pad that was there.
# Measured: inside the sandbox, SDL listed the clone until the variable was
# set, and not after.
#
# The per-game Steam launcher has undone this since the first Steam entry. The
# picker does not go through that launcher: it calls `gotg play` itself, so a
# game started from the picker, from Steam, inherited all of it through the
# picker. Here, then -- the one place every route passes -- and before the
# seat gate, which is SDL too.
#
# LD_PRELOAD is Steam's overlay, which nix-wrapped programs cannot load
# ("libGL.so.1: cannot open shared object file", the picker on a Deck). Only
# that entry is dropped; anything else in there is somebody's own doing.
danstick_clear_steam_env() {
  unset SDL_GAMECONTROLLER_IGNORE_DEVICES SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT
  export SDL_JOYSTICK_HIDAPI=0 SDL_JOYSTICK_DISABLE_UDEV=0
  if [[ "${LD_PRELOAD:-}" == *gameoverlayrenderer* ]]; then
    local kept=() entry
    for entry in ${LD_PRELOAD//:/ }; do
      [[ "$entry" == *gameoverlayrenderer* ]] || kept+=("$entry")
    done
    if ((${#kept[@]})); then
      LD_PRELOAD="$(IFS=:; printf '%s' "${kept[*]}")"
      export LD_PRELOAD
    else
      unset LD_PRELOAD
    fi
  fi
}

# What danstick's clones should look like for this environment, or nothing.
#
# Nothing is danstick's default, "mirror", and that is deliberate: a clone that
# carries the physical pad's vendor and product is what an emulator told to
# bind *that* controller expects to find. The environments that ask for
# something else say so in their pads.json -- see padIdentity in
# src/client/env/lib.nix.
#
# Refused for Ryujinx, whatever the environment says. Under "xbox360" every
# clone is 045e:028e and every GUID is the same one; Ryujinx builds its
# device id by blanking the name CRC, so four players would land on one id
# and one seat. danstick documents that as the consumer the identity does not
# suit, and this is the line that keeps it out of reach.
danstick_identity() {
  local attr="$1" manifest identity emulator
  manifest="$(env_pads_manifest "$attr")" || return 0
  [[ -f "$manifest" ]] || return 0
  identity="$(jq -r '.identity // ""' "$manifest" 2>/dev/null)" || return 0
  [[ -n "$identity" ]] || return 0
  emulator="$(jq -r '.emulator // ""' "$manifest" 2>/dev/null)"
  if [[ "$emulator" == ryujinx ]]; then
    warn "ignoring the $identity pad identity for $attr: Ryujinx cannot tell two clones apart under it"
    return 0
  fi
  printf '%s' "$identity"
}

# How many seats this environment wants to exist before the game starts, as
# `exec`'s flag, or nothing.
#
# Four Swords binds a GBA per player when it launches and never looks again,
# so a pad paired mid-game had nothing bound to it: the seat was taken, the
# clone published, and Start did nothing. A reserved seat is a clone that is
# there from the start and sends nothing until somebody takes it -- the
# binder finds danstick:3 at launch, and player three's pad drives it later.
#
# Reserving needs the 360 identity, which `exec` switches to in place for the
# length of the game. Refused for Ryujinx for the reason danstick_identity
# gives.
danstick_reserve() {
  local attr="$1" manifest seats emulator
  manifest="$(env_pads_manifest "$attr")" || return 0
  [[ -f "$manifest" ]] || return 0
  seats="$(jq -r '.reserve // ""' "$manifest" 2>/dev/null)" || return 0
  [[ "$seats" =~ ^[1-9][0-9]*$ ]] || return 0
  emulator="$(jq -r '.emulator // ""' "$manifest" 2>/dev/null)"
  if [[ "$emulator" == ryujinx ]]; then
    warn "not reserving seats for $attr: Ryujinx cannot tell two clones apart under the identity it needs"
    return 0
  fi
  printf -- '--reserve %s' "$seats"
}

# Hand that identity to the daemon this launch is about to use.
#
# Before the daemon is asked after, because the daemon reads the variable
# when it publishes a clone. And past the latch: the picker started a daemon
# in the default identity and exported DANSTICK_SKIP_DAEMON_CHECK on its way
# here, so without clearing it nothing would ask, and the game would get the
# picker's mirrored clones. danstick counts a daemon running a different
# identity as not current and replaces it -- but only when it is asked.
#
# Somebody who set DANSTICK_PAD_IDENTITY themselves keeps it: that is a person
# overriding a per-game default, which is the whole point of the variable.
danstick_identity_apply() {
  local attr="$1" identity
  [[ -z "${DANSTICK_PAD_IDENTITY:-}" ]] || return 0
  identity="$(danstick_identity "$attr")" || return 0
  [[ -n "$identity" ]] || return 0
  export DANSTICK_PAD_IDENTITY="$identity"
  unset DANSTICK_SKIP_DAEMON_CHECK
  log "controllers will look like a $identity pad to this game"
}

# Ask about controllers before the game takes the screen.
#
# Only ever asks when there is something to ask -- no controller seated, or one
# that has never been mapped for this console -- and the check itself is a
# socket round trip that costs nothing on a machine somebody has already set
# up. The reason it is here rather than in the picker is that a game can be
# started from a terminal, from Steam, or from the grid, and the controller is
# missing in exactly the same way in all three.
#
# Absent is fine. gotg-seat ships with the picker, which is a separate package
# and deliberately not something the client depends on: found on PATH it runs,
# and not found it is skipped without a word. Failure is fine too -- it exits 0
# by design, and this ignores its status anyway, because nothing about a
# controller is a reason not to start a game somebody asked for.
danstick_seat_gate() {
  local platform="$1" title="${2:-}" seat
  [[ "${GOTG_SEAT_GATE:-1}" != "0" ]] || return 0
  # The daemon first, unseated; then the gate, which is where somebody holds
  # a button; then the wait for what that published. The wait used to sit in
  # danstick_ensure, before the gate -- three seconds of waiting for a publish
  # nobody could have caused yet, and a warning that nothing was published
  # printed over a launch that was about to seat somebody.
  danstick_ensure --no-wait

  # The gate itself, unless the picker has already met it in its own window.
  # Only the *asking* is skipped: the ensure above and the wait below belong
  # to the launch either way, and skipping them was how Four Swords Adventures
  # came up with player two on the keyboard -- the bindings were written from
  # a pad list danstick had not finished publishing.
  if [[ "${GOTG_SEAT_MET:-0}" == "1" ]]; then
    log "controllers: the picker met the gate; not asking again"
  elif ! seat="$(danstick_seat_bin)" || ! command -v "$seat" >/dev/null 2>&1; then
    # Said out loud. A check that is quietly not there is indistinguishable
    # from a check that ran and was happy, and the difference is a game
    # starting with nothing to play it with.
    warn "no gotg-seat here; starting without checking for a controller"
  else
    "$seat" --platform "$platform" --title "$title" || true
  fi

  if [[ -n "$DANSTICK_MARKER" ]]; then
    danstick_wait_published "$DANSTICK_MARKER"
    rm -f "$DANSTICK_MARKER"
    DANSTICK_MARKER=""
  fi

  # Clones exist now, so SDL's Steam driver has to let go of Valve's ids.
  #
  # That driver claims 28de:* whether or not the device behind them is a
  # hidraw one, and a danstick clone mirrors the pad it stands for -- so with
  # the hint on, a seated Steam Controller is absent from every enumeration
  # in the launch. Measured with a uinput pad wearing 28de:1304: listed with
  # the hint off, gone with it on. Four Swords Adventures bound its first GBA
  # to the keyboard for this, twice, with danstick saying it had seated the pad.
  #
  # The environment (this) outranks the hint gotg-pads sets for itself, and
  # the environment script defaults rather than assigns, so this survives
  # into the game. Only when danstick actually published something, though: a
  # launch with no daemon has no clone to protect and every reason to keep
  # the hint, which is the one thing that makes a raw puck work at all.
  if [[ -n "$(danstick_sdl_config 2>/dev/null || true)" ]]; then
    export SDL_JOYSTICK_HIDAPI=0 SDL_JOYSTICK_HIDAPI_STEAM=0
  fi
}

# Launch, with danstick's mappings in the environment.
#
# `danstick-rs exec` reads the file the daemon wrote at its last republish and
# puts it in SDL_GAMECONTROLLERCONFIG. That is the whole of what an emulator
# reading no controller database needs -- Cemu is the reason it exists -- and
# it costs nothing for the ones that read one.
#
# Execs, so the process the kill switch is holding stays the one it was told
# about: danstick-rs replaces itself with the game.
#
# A game that can be joined mid-play has its seats reserved here too (see
# danstick_reserve): `exec` does it before it builds the bind plan, and gives
# them back when the game ends -- which means it waits on the game rather
# than becoming it. The kill switch signals the process group, so that costs
# it nothing.
danstick_exec() {
  danstick_ensure
  if command -v "$(danstick_rs_bin)" >/dev/null 2>&1; then
    danstick_no_sandbox_for "${PLAY_ATTR:-}" && export DANSTICK_NO_ISOLATE=1
    local reserve=()
    [[ -z "${PLAY_ATTR:-}" ]] || read -ra reserve <<<"$(danstick_reserve "$PLAY_ATTR")"
    exec "$(danstick_rs_bin)" exec ${reserve[@]+"${reserve[@]}"} -- "$@"
  fi
  exec "$@"
}

# Does this environment bring up a compositor of its own?
#
# danstick's sandbox is a user namespace, and inside one every file owned by
# root reads as `nobody` -- /tmp/.X11-unix among them. wlroots will not put an
# X socket in a directory "not owned by root or us", so a nested sway never
# starts Xwayland, the gamescope inside it is handed no output, and the
# screen stays black. It says nothing about sandboxes while doing it.
#
# So those sessions run outside it. They lose nothing: each copy inside gets
# its own seat, which isolates a game from the other players' pads more
# precisely than one sandbox around the lot ever did.
#
# The environment says so itself, with a marker file its derivation carries.
danstick_no_sandbox_for() {
  local attr="$1"
  [[ -n "$attr" ]] || return 1
  [[ -e "$(env_root "$attr")/share/gotg/owns-session" ]]
}

# --- what danstick wrote, into the emulator this launch is about ---------------
#
# The daemon writes Ryujinx's, Cemu's and Dolphin's bindings -- and, since
# motion arrived, where each finds danstick's DSU server -- into the *default*
# config directories under the home. Every emulator gotg runs is isolated
# under its own environment, so those files are never the ones it reads.
# `danstick-rs emit` exists for exactly this: the same writer, pointed at the
# directories the launch will use.

# danstick's own rule for where its daemon leaves things (runtime::dir_under).
danstick_runtime_dir() {
  printf '%s' "${GOTG_DANSTICK_RUNTIME:-${XDG_RUNTIME_DIR:-/tmp}/danstick}"
}

# The pads danstick has published, in the shape `emit` takes on stdin.
#
# Read off env.sh rather than asked of the daemon: it is rewritten on every
# republish, it is what `danstick-rs exec` hands the game, and each mapping line
# in it already names the pad -- GUID first, then "danstick Player N". Nothing
# else in it is needed here: the capabilities the list can carry are for the
# ares writer, and gotg binds ares itself.
danstick_published() {
  local file value
  file="$(danstick_runtime_dir)/env.sh"
  [[ -f "$file" ]] || return 1
  # danstick's own file, sourced the way exec would source it, in a shell that
  # cannot touch this one.
  # shellcheck disable=SC1090
  value="$(set +u; . "$file" 2>/dev/null; printf '%s' "${SDL_GAMECONTROLLERCONFIG:-}")" || return 1
  [[ -n "$value" ]] || return 1
  jq -cR '
    select(length > 0)
    | (split(",")) as $f
    | ($f[1] // "" | capture("^danstick Player (?<n>[0-9]+)$")? // empty) as $m
    | {player: ($m.n | tonumber), guid: $f[0], name: $f[1], sdl_line: .}
  ' <<<"$value" | jq -cs 'select(length > 0)'
}

# The SDL mapping danstick published, for a program that enumerates pads outside
# `danstick-rs exec` -- gotg-pads, deciding what to bind. Without it a clone of
# a pad SDL has no mapping for (a Steam Controller's, say) is a joystick with
# no map, and the writer would pass it over. Empty when nothing is published.
danstick_sdl_config() {
  local file
  file="$(danstick_runtime_dir)/env.sh"
  [[ -f "$file" ]] || return 0
  # shellcheck disable=SC1090
  (set +u; . "$file" 2>/dev/null; printf '%s' "${SDL_GAMECONTROLLERCONFIG:-}")
}

# Have danstick write this environment's emulator configuration. Returns 0 when
# something was written, so a caller that snapshots the result knows to.
#
# Every destination is named, including the three this environment is not:
# an unnamed one means the real location, and the point is that nothing lands
# in the home. ares is gotg's own writer, so danstick's goes to scratch.
danstick_emit() {
  local attr="$1" manifest emulator state pads scratch
  command -v "$(danstick_rs_bin)" >/dev/null 2>&1 || return 1
  manifest="$(env_pads_manifest "$attr")"
  [[ -f "$manifest" ]] || return 1
  emulator="$(jq -r '.emulator // ""' "$manifest")"
  case "$emulator" in ryujinx | cemu | dolphin) ;; *) return 1 ;; esac
  pads="$(danstick_published)" || return 1

  state="$(env_state_dir "$attr")"
  scratch="$state/danstick-scratch"
  mkdir -p "$scratch" || return 1
  local ryujinx="$scratch/Config.json" cemu="$scratch/cemu" dolphin="$scratch/dolphin-emu"
  case "$emulator" in
    ryujinx) ryujinx="$(pads_ryujinx_config "$attr")" ;;
    cemu) cemu="$(pads_cemu_config_dir "$attr")/controllerProfiles" ;;
    dolphin) dolphin="$state/config/dolphin-emu" ;;
  esac

  local written
  written="$("$(danstick_rs_bin)" emit \
    --ryujinx-config "$ryujinx" \
    --cemu-dir "$cemu" \
    --dolphin-dir "$dolphin" \
    --ares-settings "$scratch/settings.bml" \
    --env-file "$scratch/env.sh" <<<"$pads" 2>/dev/null)" || return 1
  rm -rf "$scratch"
  # Only the file this environment reads counts as written; the scratch ones
  # were the price of naming every destination.
  grep -qF "$state/" <<<"$written" || return 1
  # Dolphin's `Device = SDL/0/danstick Player 1` is left exactly as danstick
  # wrote it. It was once "corrected" to the name gotg-pads reports for the
  # same clone -- `Xbox 360 Controller`, SDL's joystick name -- and Dolphin's
  # own log then showed why that was wrong: under `danstick-rs exec` it lists
  # the clone as `SDL/0/danstick Player 1`, and `SDL/0/Xbox 360 Controller` is
  # the raw pad danstick has grabbed. Player one bound to a device that sends
  # nothing, in Four Swords Adventures, for a morning.
  log "danstick wrote the $emulator bindings and motion for $attr"
}

