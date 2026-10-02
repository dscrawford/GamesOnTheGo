# The shell the recorded demos run in.
#
# What is on camera is `nix search gotg …` and `nix run gotg#…`: a library,
# named `gotg` in the flake registry. Which library, and whose config and
# state, is the recorder's: GOTG_DEMO_RC names a file that exports them
# (XDG_CONFIG_HOME with a nix/registry.json naming the library, GOTG_CONFIG_DIR,
# XDG_STATE_HOME, GOTG_LIBRARY), so a machine's paths never land in here.

PS1='$ '
# shellcheck disable=SC1090
[[ -z "${GOTG_DEMO_RC:-}" ]] || source "$GOTG_DEMO_RC"

# One Tab lists the alternatives instead of waiting for a second one, and no
# terminal bell — a flash on every ambiguous completion reads as an error.
bind 'set show-all-if-ambiguous on'
bind 'set bell-style none'
