# The processing steps a recipe pipeline is built from.
#
# A recipe is a list of these, run left to right by the harness lib.nix
# generates. Each step is { name, tools?, script }: the script runs with
#
#   $cur    the cursor — the artifact so far; starts as $raw (the staged
#           member directory) and each step reassigns it to what it produced
#   $raw    the staged member directory, untouched
#   $stage  scratch space, swept whole by the harness on any exit
#   $dest   the extensionless destination — a terminal step places
#           $dest.<ext>, because the artifact keeps the id and only the
#           recipe knows the format
#   fail    error-and-exit, prefixed with the step's name
#
# Tool paths are pinned by nix but each is overridable as GOTG_<name> — which
# is what lets a test stub a multi-gigabyte conversion into an echo.
{ pkgs }:

{
  # Scene releases ship an .sfv; when one is present the volume set has to
  # match it before anything is unpacked. No .sfv is fine — not every source
  # has one — but a failing check is a refusal.
  verifySfv = {
    name = "verify-sfv";
    tools.RHASH = "${pkgs.rhash}/bin/rhash";
    script = ''
      sfv="$(find "$cur" -maxdepth 1 -name '*.sfv' | head -1 || true)"
      if [ -n "$sfv" ]; then
        (cd "$cur" && "$RHASH" -c "$sfv") || fail "sfv verification failed"
      fi
    '';
  };

  # A rar volume set: point unrar at the first volume and it reads the rest.
  # `e` rather than `x` — nothing downstream wants the archived directory
  # structure, and flattening removes the path-traversal surface entirely
  # instead of trusting the extractor's own checks. unrar is
  # unfree-redistributable; the flake already allows unfree for the emulators
  # themselves. libarchive is not a substitute — its RAR5 support is partial.
  unrar = {
    name = "unrar";
    tools.UNRAR = "${pkgs.unrar}/bin/unrar";
    script = ''
      rar="$(find "$cur" -maxdepth 1 -name '*.rar' | head -1 || true)"
      [ -n "$rar" ] || fail "no .rar in $cur"
      next="$stage/unrar"
      mkdir -p "$next"
      "$UNRAR" e -idq -o+ "$rar" "$next/" || fail "unrar failed"
      cur="$next"
    '';
  };

  # 7zz, the maintained official 7-Zip, not the long-unmaintained p7zip fork:
  # an archive parser is the largest attacker-controlled surface here.
  extract7z = {
    name = "7z";
    tools.P7Z = "${pkgs._7zz}/bin/7zz";
    script = ''
      archive="$(find "$cur" -maxdepth 1 \( -name '*.7z' -o -name '*.rar' \) | head -1 || true)"
      [ -n "$archive" ] || fail "no archive in $cur"
      next="$stage/extract"
      mkdir -p "$next"
      "$P7Z" x -bd -y -o"$next" "$archive" >/dev/null || fail "extract failed"
      cur="$next"
    '';
  };

  # An extraction leaves the game beside its filler — nfos, samples, subdirs.
  # The game is the big one. NUL-terminated throughout: extracted names are
  # the archive's to choose, and a newline in one would otherwise forge a
  # second "larger file" line pointing anywhere at all.
  pickLargest = {
    name = "pick-largest";
    script = ''
      pick="$(find "$cur" -type f -printf '%s\t%p\0' | sort -z -rn | head -z -n1 | cut -z -f2- | tr -d '\0' || true)"
      [ -n "$pick" ] || fail "nothing to pick in $cur"
      case "$(realpath -- "$pick")" in
        "$(realpath -- "$cur")"/*) ;;
        *) fail "picked path escapes $cur" ;;
      esac
      cur="$pick"
    '';
  };

  # A zipped ROM, for the ports that read a bare file rather than the archive
  # it is distributed in.
  unzip = {
    name = "unzip";
    tools.UNZIP = "${pkgs.unzip}/bin/unzip";
    script = ''
      zip="$(find "$cur" -maxdepth 1 -name '*.zip' | head -1 || true)"
      [ -n "$zip" ] || fail "no .zip in $cur"
      next="$stage/unzip"
      mkdir -p "$next"
      "$UNZIP" -q "$zip" -d "$next" || fail "unzip failed"
      cur="$next"
    '';
  };

  # Terminal: the whole tree, placed as the extensionless destination — the
  # shape overrides.json calls unzip, where the launch target is resolved by
  # glob inside it.
  placeTree = {
    name = "place-tree";
    script = ''
      rm -rf "$dest"
      mv "$cur" "$dest" || fail "could not place $dest"
      cur="$dest"
    '';
  };

  # Terminal: the source names the format. Emulators dispatch on the
  # extension, so it has to survive the rename onto the id — but it is an
  # extracted name's to choose, so anything but a plain one is refused rather
  # than installed as it stands.
  keepExtension = {
    name = "keep-extension";
    script = ''
      ext="$(basename "$cur")"
      ext="''${ext##*.}"
      case "$ext" in
        *[!A-Za-z0-9]* | "") fail "unusable extension on $(basename "$cur")" ;;
      esac
      mv "$cur" "$dest.''${ext,,}" || fail "could not place $dest.''${ext,,}"
      cur="$dest.''${ext,,}"
    '';
  };

  # Terminal: whatever disc image came out of the archive, stored as the RVZ
  # the emulator wants.
  convertRvz = {
    name = "convert-rvz";
    tools.DOLPHIN_TOOL = "${pkgs.dolphin-emu}/bin/dolphin-tool";
    script = ''
      "$DOLPHIN_TOOL" convert -f rvz -b 131072 -c zstd -l 5 -i "$cur" -o "$dest.rvz" ||
        fail "conversion failed"
      cur="$dest.rvz"
    '';
  };
}
