#!/usr/bin/env bash
# Bring a pod up to where `gotg qa` can run, then hand off to it.
#
# The harness assumes two things a desktop has and a container does not: a
# running PipeWire it can hang a null sink on, and a writable runtime dir for
# the sockets. Start the first, make the second, and otherwise stay out of the
# way — every QA decision belongs to `gotg qa`, not here.
set -euo pipefail

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/tmp/xdg}"
mkdir -p "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"

# Adopt the environments baked into the image. A pod's nix store is a
# read-only image layer, so nothing can be built here; these were built when
# the image was, and linking them where `gotg play` looks is what makes a run
# start without ever calling nix. A link already there is left alone — the
# state volume outlives the pod, and a newer image is adopted by name.
roots="${XDG_STATE_HOME:-$HOME/.local/state}/gotg/roots"
mkdir -p "$roots"
for env in "${GOTG_QA_BAKED_ENVS:-/nonexistent}"/*; do
  [[ -e "$env" ]] || continue
  ln -sfn "$(readlink -f "$env")" "$roots/$(basename "$env")"
done

# A session bus, because pipewire-pulse wants one and containers have none.
if [[ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ]]; then
  eval "$(dbus-launch --sh-syntax)"
  export DBUS_SESSION_BUS_ADDRESS
fi

pipewire &
pipewire-pulse &
wireplumber &

# Wait for the Pulse shim to answer, so the first `pactl` in the harness does
# not race the daemon it depends on.
for _ in $(seq 1 50); do
  pactl info >/dev/null 2>&1 && break
  sleep 0.2
done
pactl info >/dev/null 2>&1 || {
  echo "entrypoint: pipewire-pulse never came up" >&2
  exit 1
}

exec gotg qa "$@"
