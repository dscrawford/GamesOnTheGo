#!/usr/bin/env bash
# Renders the tapes to GIFs. From the repo root:
#
#   docs/demos/render.sh                     # all of them
#   docs/demos/render.sh docs/demos/gotg-find.tape
set -euo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../.."

vhs=(vhs)
command -v vhs >/dev/null || vhs=(nix run nixpkgs#vhs --)

# vhs films a login shell, which on a real machine runs whoever's rc file —
# this one execs tmux, which puts a status bar across the top of every
# recording and eats the first command typed into it. An empty HOME keeps that
# shell config-free; demo-shell.sh hands the real HOME to the shell actually on
# camera, which needs ~/.config/gotg and ~/.local/state/gotg to have anything
# to show.
empty_home="$(mktemp -d)"
trap 'rm -rf "$empty_home"' EXIT

tapes=("$@")
((${#tapes[@]})) || tapes=(docs/demos/*.tape)

for tape in "${tapes[@]}"; do
  printf '\n== %s\n' "$tape"
  # PATH travels the same way: the login shell rebuilds its own from
  # /etc/profile, and with HOME pointed elsewhere that one has no gotg in it.
  env HOME="$empty_home" GOTG_DEMO_HOME="$HOME" GOTG_DEMO_PATH="$PATH" \
    "${vhs[@]}" "$tape"
done
