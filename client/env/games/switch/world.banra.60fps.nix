# Luigi's Mansion 2 HD at 60 FPS — `gotg play world.banra 60fps`.
#
# The game ships locked to 30. There are two ways to raise that and only one of
# them is right.
#
# The wrong way is Ryujinx's VSync toggle (F1), which raises the *emulated
# refresh rate*. Ryujinx says what that costs in its own settings text: "In some
# titles, this may speed up or slow down the rate of gameplay logic ... no
# guarantees for how gameplay will be affected." In this game that is exactly
# the bug where Luigi crawls up stairs — game logic running off a refresh rate
# it was never written for.
#
# The right way is this patch, which changes the framerate cap in the executable
# and leaves the logic alone. VSync then stays on "Switch", the 60Hz default, so
# nothing has to be toggled at launch and nothing drifts if F1 is pressed by
# accident.
#
# NOTE ON THE NAME: `world.banra` is the scene release's internal codename, not
# the game's. It came in that way and the id is the catalog's, so this file is
# named to match. When the entry is renamed, this renames with it.
{ pkgs, base, ... }:

let
  # Pinned to a commit rather than a branch: this is two lines of machine code
  # written against one build of one game, and a silent change to it would be a
  # silent change to the game's timing.
  patch = pkgs.fetchurl {
    url =
      "https://raw.githubusercontent.com/StevensND/switch-port-mods/"
      + "774e7f7c85561ddd865c1cab555f2a335054d522/"
      + "Luigi%27s%20Mansion%202%20HD/%5B010048701995E000%5D/60FPS/1.0.0.pchtxt";
    hash = "sha256-CFxkNqG9m+FvzMK0/ECw6qK4HC8hzuJu+Qee3OwIMLg=";
  };

  # Ryujinx reads mods from mods/contents/<title id>/<name>/exefs. The title id
  # is this game's, lowercased, and the file is named for the game version it
  # patches — a 1.0.0 patch simply does nothing to a later revision rather than
  # breaking it.
  titleId = "010048701995e000";
in
base
// {
  preLaunch =
    (base.preLaunch or "")
    + ''
      mods="$XDG_CONFIG_HOME/Ryujinx/mods/contents/${titleId}/60fps/exefs"
      if [ ! -f "$mods/1.0.0.pchtxt" ]; then
        mkdir -p "$mods"
        cp --no-preserve=mode ${patch} "$mods/1.0.0.pchtxt"
        echo "installed the 60 FPS patch" >&2
      fi

      # Hold VSync at "Switch" — 60Hz, the default. The patch does the work, so
      # a custom refresh rate here would only reintroduce what it fixes, and
      # pressing F1 mid-game would otherwise persist into the next launch.
      config="$XDG_CONFIG_HOME/Ryujinx/Config.json"
      if [ -f "$config" ]; then
        ${pkgs.jq}/bin/jq '.vsync_mode = 0 | .enable_custom_vsync_interval = false' \
          "$config" > "$config.gotg" && mv "$config.gotg" "$config"
      fi
    '';
}
