# shellcheck shell=bash
# The GL a machine with no /run/opengl-driver needs, fetched when it is needed.
#
# SteamOS has no /run/opengl-driver and its own mesa will not load into a
# program built against nixpkgs' glibc, so there every environment, the
# overlay and the picker point GL, EGL, GBM and Vulkan at nixpkgs' mesa (see
# env/foreign-gl.nix). That mesa used to be a dependency of each of them: a
# gigabyte with its LLVM in every closure, NixOS included, where it is never
# loaded. Now each only names the store path it was built with --
# share/gotg/foreign-gl -- and this fetches exactly that one, before it runs,
# on the machines that will load it.
#
# Exactly that one, not "the current mesa": a mesa from a newer nixpkgs than
# the program it is loaded into wants a newer glibc than that program's, and
# that fails as `GLIBC_2.xx not found` deep inside a driver. And kept, as a GC
# root under foreign-gl/ rather than among the environment roots, so that a
# garbage collection cannot take it a week later; sync lets go of the ones
# nothing here names any more.

foreign_gl_dir() { printf '%s/foreign-gl' "$GOTG_STATE_DIR"; }

# Whether this machine loads the GL GOTG brings: it has none of its own, or
# GOTG_FOREIGN_GL=1 says to pretend it has none (a Deck's QA run here). The
# same question the wrapper asks; GOTG_HOST_GL is where tests move the answer.
foreign_gl_needed() {
  [[ "${GOTG_FOREIGN_GL:-0}" == 1 || ! -e "${GOTG_HOST_GL:-/run/opengl-driver}" ]]
}

# A mesa's output in the store, and nothing else: the name becomes an argument
# to nix and a file name here.
foreign_gl_valid() {
  [[ "$1" =~ ^/nix/store/[0-9a-df-np-sv-z]{32}-mesa-[A-Za-z0-9.+_-]+$ ]]
}

# Fetch one named mesa and keep a root to it. Never fatal: a game that cannot
# get it still starts, and the wrapper says plainly what is missing.
foreign_gl_fetch() {
  local path="$1" root
  foreign_gl_valid "$path" || {
    warn "not fetching GL for this machine: '$path' is not a mesa in the store"
    return 0
  }
  root="$(foreign_gl_dir)/$(basename "$path")"
  if [[ -e "$path/lib/dri" && "$(readlink -f "$root" 2>/dev/null)" == "$path" ]]; then
    return 0
  fi
  mkdir -p "$(foreign_gl_dir)"
  log "fetching the GL this machine needs: $(basename "$path")"
  # A store path by itself: nix substitutes it from the binary cache, the same
  # mesa every NixOS machine downloads, or finds it here already.
  "$(nix_bin)" build "$path" -o "$root" ||
    warn "could not fetch the GL this machine needs ($path); the game may start without a picture"
}

# What one file names, fetched if this machine needs it.
foreign_gl_from() {
  local file="$1"
  [[ -f "$file" ]] || return 0
  foreign_gl_needed || return 0
  foreign_gl_fetch "$(head -n1 "$file")"
}

# For a launch: the environment's, and this client's own -- the overlay runs
# beside every game, and draws with the client's.
foreign_gl_ensure() {
  local attr="$1"
  foreign_gl_from "$(env_root "$attr")/share/gotg/foreign-gl"
  foreign_gl_from "$(foreign_gl_self)"
}

# This client's own: the overlay it starts beside a game is its own, built
# from the same nixpkgs. Absent in a checkout. GOTG_FOREIGN_GL_SELF moves it
# for tests ("none" for no file at all).
foreign_gl_self() {
  local self="${GOTG_FOREIGN_GL_SELF:-$GOTG_ROOT/foreign-gl}"
  [[ "$self" != none ]] || self=/nonexistent
  printf '%s' "$self"
}

# After a sync: fetch what the rebuilt roots name, and let go of the rest. On
# a machine with its own GL that is all of them.
foreign_gl_sync() {
  local dir root file path
  local -A wanted=()
  dir="$(foreign_gl_dir)"
  if foreign_gl_needed; then
    # The client and picker sync just built, which Steam launches, as well as
    # this one and every environment.
    for file in "$(foreign_gl_self)" "$GOTG_APP_ROOT/share/gotg/foreign-gl" \
      "$GOTG_UI_ROOT/share/gotg-ui/foreign-gl" "$GOTG_ROOTS_DIR"/env-*/share/gotg/foreign-gl; do
      [[ -f "$file" ]] || continue
      path="$(head -n1 "$file")"
      foreign_gl_valid "$path" || continue
      wanted["$(basename "$path")"]=1
      foreign_gl_fetch "$path"
    done
  fi
  [[ -d "$dir" ]] || return 0
  for root in "$dir"/*; do
    [[ -e "$root" || -L "$root" ]] || continue
    [[ -n "${wanted[$(basename "$root")]:-}" ]] && continue
    rm -rf -- "$root"
  done
}
