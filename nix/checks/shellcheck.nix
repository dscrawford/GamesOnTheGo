# shellcheck over the shell client.
{ pkgs }:
pkgs.runCommand "check-shellcheck"
  {
    nativeBuildInputs = [ pkgs.shellcheck ];
  }
  ''
    cd ${../../.}
    shellcheck --external-sources --source-path=src/client src/client/bin/gotg src/client/lib/*.sh
    touch $out
  ''
