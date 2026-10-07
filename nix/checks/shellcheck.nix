# shellcheck over the shell client.
{ pkgs }:
pkgs.runCommand "check-shellcheck"
  {
    nativeBuildInputs = [ pkgs.shellcheck ];
  }
  ''
    cd ${../../.}
    shellcheck --external-sources --source-path=src/client src/client/bin/gotg src/client/lib/*.sh
    shellcheck -s bash src/client/play-anywhere.sh
    # Embedded into every environment's launcher by env/lib.nix.
    shellcheck -s bash src/client/env/machine.sh
    touch $out
  ''
