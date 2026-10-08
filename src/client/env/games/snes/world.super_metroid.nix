# Super Metroid — the SNES base, with settings of its own. The file returns
# only what it changes; everything else comes from ../../snes.nix.
{ ... }:
{
  # Its own config and save directory under ~/.local/state/gotg/env, so tuning
  # ares for Super Metroid cannot change how the rest of the SNES set runs.
  isolate = true;
}
