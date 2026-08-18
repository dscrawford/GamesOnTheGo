#!/usr/bin/env bash
# Starts the shell the demos are recorded in. Run from a tape, not by hand.
#
# It picks that shell rather than taking the one on PATH: inside `nix develop`,
# `bash` is the minimal build, without readline or programmable completion — in
# which a recording of tab completion is three commands and no completions,
# with nothing on screen to say why.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# render.sh gives the login shell an empty HOME so nobody's rc file ends up in
# the recording, and passes the real one along here — this shell is the one
# that has to find ~/.config/gotg.
home="${GOTG_DEMO_HOME:-$HOME}"
path="${GOTG_DEMO_PATH:-$PATH}"

# env -i for the same reason: without it this arrives carrying the direnv hook
# and the prompt of whoever is recording.
for candidate in "${SHELL:-}" /run/current-system/sw/bin/bash /bin/bash "$(command -v bash)"; do
  [[ -x "$candidate" ]] || continue
  "$candidate" --noprofile --norc -ic 'type -t complete' >/dev/null 2>&1 || continue
  # NOSYSBASHRC because NixOS' /etc/bashrc sources /etc/profile whenever the
  # profile has not run in a parent — which under env -i it has not — and that
  # rebuilds PATH, losing the gotg being demonstrated.
  exec env -i \
    HOME="$home" PATH="$path" TERM="${TERM:-xterm-256color}" \
    LANG="${LANG:-C.UTF-8}" NOSYSBASHRC=1 \
    "$candidate" --noprofile --rcfile "$here/demo.bashrc" -i
done

echo "no bash with programmable completion on PATH — see docs/demos/README.md" >&2
exit 1
