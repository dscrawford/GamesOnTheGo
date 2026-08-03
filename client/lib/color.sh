# shellcheck shell=bash
# Colour, and the rules for when there is none.
#
# Four conventions, checked in this order:
#
#   NO_COLOR     any non-empty value turns colour off whatever else says, per
#                https://no-color.org
#   TERM=dumb    the terminal saying it cannot render this
#   FORCE_COLOR  turns it on even when the output is not a terminal, which is
#                what CI logs and `script` want
#   a terminal   otherwise, only when someone is watching one
#
# The last of those is why nothing here needs to know about Steam: a launch has
# both streams redirected to a log file, so it is uncoloured by the same rule
# that uncolours a pipe.
#
# Both streams are required rather than either, so that `gotg list > file` does
# not colour the commentary on stderr while the table it belongs to goes out
# plain. It costs colour in the rarer `gotg list | less` case, and that is the
# right way round.
#
# Every name below is defined either way — empty when colour is off — so call
# sites never test a flag. Colours are the eight-colour set on purpose: those
# are the ones a terminal theme controls, so they stay legible on a light
# background, which 256-colour and truecolour do not.
# shellcheck disable=SC2034

color_enabled() {
  [[ -z "${NO_COLOR:-}" ]] || return 1
  [[ "${TERM:-}" != "dumb" ]] || return 1
  [[ -z "${FORCE_COLOR:-}" ]] || return 0
  [[ -t 1 && -t 2 ]]
}

# Separate from the definitions so a test can change the environment and ask
# again; called once at load for everything else.
color_init() {
  if color_enabled; then
    C_RESET=$'\033[0m'
    C_BOLD=$'\033[1m'
    C_DIM=$'\033[2m'
    C_RED=$'\033[31m'
    C_GREEN=$'\033[32m'
    C_YELLOW=$'\033[33m'
    C_CYAN=$'\033[36m'
  else
    C_RESET=""
    C_BOLD=""
    C_DIM=""
    C_RED=""
    C_GREEN=""
    C_YELLOW=""
    C_CYAN=""
  fi

  # Named for what they mean rather than what they look like, so a call site
  # says "this is an id" and the palette decides how an id looks.
  C_ERROR="$C_RED"
  C_WARN="$C_YELLOW"
  C_OK="$C_GREEN"
  C_ID="$C_CYAN"
  C_HEAD="$C_BOLD"
  C_MUTED="$C_DIM"
}

color_init
