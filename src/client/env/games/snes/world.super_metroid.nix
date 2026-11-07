# Super Metroid — the SNES base, with settings of its own. The file returns
# only what it changes; everything else comes from ../../snes.nix.
{ base, ... }:
{
  # Its own config and save directory under ~/.local/state/gotg/env, so tuning
  # ares for Super Metroid cannot change how the rest of the SNES set runs.
  isolate = true;

  # The platform's, unchanged: on Windows every environment already has its
  # own {state}, and the facet's --settings-file puts ares' config in it.
  inherit (base) windows;
}
