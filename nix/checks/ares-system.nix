# ares reads --system as one argument, so a regression that let the space
# through unquoted would split "Game" from "Boy" and put the picker back in
# front of every launch — the exact thing gb and gbc were pinned to stop.
# Grepping the source text cannot see that: it is what the shell does with the
# line, not what the line reads as. So this runs the generated launcher with
# ares swapped for something that prints one line per argument it was actually
# handed.
{ pkgs }:
let
  envs = import ../../src/client/env { inherit pkgs; };
  argvPrinter = pkgs.writeShellScript "print-argv" ''
    for a in "$@"; do printf 'ARG<%s>\n' "$a"; done
  '';
  # GOTG_FULLSCREEN=0 because the sandbox has no tty, and the default
  # for that is --fullscreen — which would shift everything after it.
  # That behaviour is the fullscreen check's to assert, not this one's.
  argvOf =
    env: label: rom:
    pkgs.runCommand "ares-argv-${label}" { } ''
      cp ${env}/bin/gotg-play launcher
      chmod +w launcher
      sed -E -i \
        's#/nix/store/[a-z0-9]+-ares-[0-9.]+/bin/ares#${argvPrinter}#' \
        launcher
      grep -qF '${argvPrinter}' launcher \
        || { echo "ares was not where this expected to find it" >&2
             grep -n '^exec' launcher >&2
             exit 1; }
      GOTG_FULLSCREEN=0 GOTG_ENV_STATE="$TMPDIR/state" \
        ./launcher ${rom} >$out
    '';
  gbArgv = argvOf envs.env-gb "gb" "/nowhere/game.gb";
  gbcArgv = argvOf envs.env-gbc "gbc" "/nowhere/game.gbc";
in
pkgs.runCommand "check-ares-system" { } ''
  actual_gb="$(grep -A2 -x -F 'ARG<--system>' ${gbArgv})"
  expected_gb="$(printf 'ARG<--system>\nARG<Game Boy>\nARG</nowhere/game.gb>')"
  [ "$actual_gb" = "$expected_gb" ] \
    || { echo "gb's --system did not survive as one argument ahead of the rom" >&2
         cat ${gbArgv} >&2
         exit 1; }

  actual_gbc="$(grep -A2 -x -F 'ARG<--system>' ${gbcArgv})"
  expected_gbc="$(printf 'ARG<--system>\nARG<Game Boy Color>\nARG</nowhere/game.gbc>')"
  [ "$actual_gbc" = "$expected_gbc" ] \
    || { echo "gbc's --system did not survive as one argument ahead of the rom" >&2
         cat ${gbcArgv} >&2
         exit 1; }

  # A platform that never named a core has to keep letting ares work
  # it out, so a refactor that started passing --system to everything
  # fails here rather than showing up as a prompt nobody was watching
  # for.
  ! grep -q -- '--system' ${envs.env-n64}/bin/gotg-play \
    || { echo "n64 grew a --system it never asked for" >&2; exit 1; }

  # gbc saves into "Game Boy", not "Game Boy Color" — ares names the
  # directory for the cartridge rather than the core it was told to
  # run. Unifying the two fields is the obvious-looking refactor, and
  # it does not error: it quietly starts adopting saves into a
  # directory ares never reads.
  grep -q '"into":"saves/Game Boy"' ${envs.env-gbc}/share/gotg/saves.json \
    || { echo "gbc's legacy saves no longer land in 'Game Boy'" >&2; exit 1; }
  ! grep -q '"into":"saves/Game Boy Color"' ${envs.env-gbc}/share/gotg/saves.json \
    || { echo "gbc's legacy saves now target its own name, not the cartridge's" >&2; exit 1; }

  touch $out
''
