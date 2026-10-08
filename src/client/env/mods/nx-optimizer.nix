# MaxLastBreath's nx-optimizer UltraCam modules, as both Zelda games take them.
#
# The Tears of the Kingdom and Breath of the Wild files each pinned the same
# revision, fetched the same two files (main.npdm, subsdk3) from a different
# directory of it, and carried the same loop to put them in a mods directory.
# The loop had one subtle part, which now has one comment.
{ pkgs }:
let
  rev = "c41e68439ccd0c19bffc8a5bab08ffdf0b81b992";
in
{
  # The emulated display's side of a frame-rate cap: the console's own 60 Hz
  # at or below 60, a custom interval above it, since the emulated display is
  # what the game's frames are handed to. Both games' files had this verbatim.
  vsyncFor =
    fps:
    if fps <= 60 then
      ".vsync_mode = 0 | .enable_custom_vsync_interval = false"
    else
      ".vsync_mode = 2 | .enable_custom_vsync_interval = true | .custom_vsync_interval = ${toString fps}";

  # `game` is the directory under src/PatchInfo, percent-encoded
  # ("Tears%20Of%20The%20Kingdom"); each hash is of the file named for it.
  # `install dir` is the shell that makes `dir` hold both, copying only what
  # changed. The caller makes the directory.
  nxOptimizerExefs =
    {
      game,
      npdmHash,
      subsdkHash,
    }:
    let
      exefs = "https://raw.githubusercontent.com/MaxLastBreath/nx-optimizer/${rev}/src/PatchInfo/${game}/UltraCam/exefs";
      npdm = pkgs.fetchurl {
        url = "${exefs}/main.npdm";
        hash = npdmHash;
      };
      subsdk = pkgs.fetchurl {
        url = "${exefs}/subsdk3";
        hash = subsdkHash;
      };
    in
    {
      install = dir: ''
        for pair in "${npdm}:main.npdm" "${subsdk}:subsdk3"; do
          src="''${pair%%:*}"
          name="''${pair##*:}"
          # Without the game's LD_PRELOAD: sdl2-compat aborts any program it is
          # preloaded into that has no SDL3 beside it (ryujinx.nix, cheatsOn),
          # and cmp aborting read as "different" and copied on every launch.
          if ! LD_PRELOAD=''' cmp -s "$src" "${dir}/$name"; then
            cp --no-preserve=mode "$src" "${dir}/$name"
            echo "installed UltraCam ($name)" >&2
          fi
        done'';
    };
}
