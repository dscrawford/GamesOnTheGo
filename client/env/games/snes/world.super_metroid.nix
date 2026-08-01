# Super Metroid — the SNES base, with settings of its own.
#
# This is the template for per-game tweaks. The file returns only what it
# changes; everything else comes from ../../snes.nix, and `base` is that file's
# attributes if a setting needs adding to rather than replacing:
#
#   args        = base.args ++ [ "--fullscreen" ];   the command line
#   env         = { SDL_VIDEODRIVER = "wayland"; };  merged over the base's
#   emulator    = pkgs.mesen; bin = "mesen";         a different emulator entirely
#   configFiles = { "ares/settings.bml" = ./settings.bml; };  seeded on first run
#   preLaunch   = "…";                               shell, with $target/$install/$state
#
# The build is keyed on the file, so editing it and running `gotg sync` (or just
# launching again after `rm ~/.local/state/gotg/roots/env-snes-world_super_metroid`)
# picks the change up.
{ ... }:
{
  # Its own config and save directory under ~/.local/state/gotg/env, so tuning
  # ares for Super Metroid cannot change how the rest of the SNES set runs.
  isolate = true;
}
