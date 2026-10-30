#!/usr/bin/env bash
# Fetch the pinned EmulatorJS release into a directory the service serves at
# /play/ejs/ (GOTG_EJS_DIR). Nothing from it is committed. Usage:
#   scripts/fetch-emulatorjs.sh /srv/gotg/emulatorjs
# Needs curl, sha256sum and 7z (nix shell nixpkgs#p7zip). Result: <dir>/data/loader.js
set -euo pipefail

VERSION="4.2.3"
URL="https://github.com/EmulatorJS/EmulatorJS/releases/download/v${VERSION}/${VERSION}.7z"
SHA256="07d451bc06fa3ad04ab30d9b94eb63ac34ad0babee52d60357b002bde8f3850b"

dest="${1:?usage: $0 <destination-dir>}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

curl -fL --retry 3 -o "$work/ejs.7z" "$URL"
echo "${SHA256}  $work/ejs.7z" | sha256sum -c -
mkdir -p "$dest"
7z x -y -o"$dest" "$work/ejs.7z" >/dev/null
test -f "$dest/data/loader.js" || { echo "unexpected layout: no data/loader.js in $dest" >&2; exit 1; }
echo "EmulatorJS ${VERSION} in $dest (serve with GOTG_EJS_DIR=$dest)"
