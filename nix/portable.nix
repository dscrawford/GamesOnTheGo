# A game's library output as one file that runs on a Linux machine without Nix:
# `nix build <library>#<output>.gotgPortable`, pushed by `gotg port push` and
# downloaded from the web app (src/gotg/ports.py).
#
# The file is a shell header, a small uncompressed tar of two static tools
# (nix-user-chroot, zstd) and the output's whole closure as a tar.zst. Run, it
# unpacks once under ~/.cache/gotg/portable/ and starts the game with that
# directory mounted at /nix in a user namespace -- the store paths it was
# built with, so nothing is relinked. The mesa a machine without
# /run/opengl-driver loads (env/foreign-gl.nix) rides along: there is no Nix
# there to fetch it.
{
  lib,
  runCommand,
  closureInfo,
  pkgsStatic,
  zstd,
  gnutar,
  mesa,
}:
{
  game,
  program,
  name,
  gotg,
  server,
}:
let
  closure = closureInfo {
    rootPaths = [
      game
      mesa
    ];
  };
  # Fixed-size, so the payload's offset is known before it is written.
  headerBytes = 16384;
  header = ''
    #!/bin/sh
    # GOTG: ${name}, portable -- runs without Nix. `<this file> login` first, once per machine.
    set -eu
    self=$(readlink -f "$0")
    base=''${GOTG_PORTABLE_DIR:-''${XDG_CACHE_HOME:-$HOME/.cache}/gotg/portable}
    dir="$base/${name}-@id@"
    if [ ! -e "$dir/.complete" ]; then
      echo "gotg: unpacking ${name} into $dir (once)" >&2
      mkdir -p "$base"
      for old in "$base/${name}"-* ; do
        [ -e "$old" ] || continue
        chmod -R u+w "$old" 2>/dev/null || true
        rm -rf "$old"
      done
      mkdir -p "$dir.part/tools"
      tail -c +$((${toString headerBytes} + 1)) "$self" | head -c @tools@ | tar -x -C "$dir.part/tools"
      tail -c +$((${toString headerBytes} + @tools@ + 1)) "$self" | "$dir.part/tools/zstd" -dcq | tar -x -C "$dir.part"
      touch "$dir.part/.complete"
      mv "$dir.part" "$dir"
    fi
    export GOTG_PORTABLE=1
    if [ "''${1:-}" = login ]; then
      shift
      exec "$dir/tools/nix-user-chroot" "$dir/nix" ${gotg}/bin/gotg login --server ${lib.escapeShellArg server} "$@"
    fi
    if [ ! -e "''${GOTG_CONFIG_DIR:-''${XDG_CONFIG_HOME:-$HOME/.config}/gotg}/api.json" ]; then
      # $0, not $self: the name it was run by, not the store path a download resolves to.
      echo "gotg: this machine has not logged in to ${server} yet; run: $0 login" >&2
      exit 1
    fi
    exec "$dir/tools/nix-user-chroot" "$dir/nix" ${game}/bin/${program} "$@"
    exit 127
  '';
in
runCommand "gotg-portable-${name}.run"
  {
    nativeBuildInputs = [
      zstd
      gnutar
    ];
    inherit header;
    passAsFile = [ "header" ];
    passthru = { inherit closure; };
    meta.description = "${name}, as one file that runs without Nix";
  }
  ''
    mkdir tools
    cp ${pkgsStatic.nix-user-chroot}/bin/nix-user-chroot ${pkgsStatic.zstd.bin}/bin/zstd tools/
    tar -C tools --owner=0 --group=0 --numeric-owner --mtime=@1 -cf tools.tar nix-user-chroot zstd
    sed 's|^/||' ${closure}/store-paths >paths
    tar -C / --owner=0 --group=0 --numeric-owner --mtime=@1 --sort=name -cf - -T paths |
      zstd -q -T"''${NIX_BUILD_CORES:-1}" -12 >payload.tar.zst

    id=$(basename "$out" | cut -c1-12)
    sed -e "s|@id@|$id|g" -e "s|@tools@|$(stat -c %s tools.tar)|g" "$headerPath" >header.sh
    size=$(stat -c %s header.sh)
    if [ "$size" -gt ${toString headerBytes} ]; then
      echo "portable: the header is $size bytes, over ${toString headerBytes}" >&2
      exit 1
    fi
    # Padded with newlines: the shell has exec'd before it reads them.
    head -c $((${toString headerBytes} - size)) /dev/zero | tr '\0' '\n' >>header.sh
    cat header.sh tools.tar payload.tar.zst >$out
    chmod 555 $out
  ''
