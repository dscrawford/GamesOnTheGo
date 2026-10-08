# The programs this project packages itself because nixpkgs does not carry
# them (or carries them without a patch the ports need), and the SDL cut they
# share. Two things need exactly this set: the flake's `packages`
# (nix/packages.nix) and the environments in src/client/env, whose game files
# name them as `gotgPkgs`; built apart, a bare
# `import src/client/env { inherit pkgs; }` quietly got a second copy.
#
# Only what a derivation of ours is made of: danstick and the gotg-pads
# enumerator come from the flake's inputs and outputs, so the callers add them.
{ pkgs }:
let
  # The ports patched onto nixpkgs take an SDL cut to what a game uses; see
  # pkgs/sdl3.nix for what that leaves out and what it saved.
  sdl3s = pkgs.callPackage ../pkgs/sdl3.nix { };
in
{
  inherit sdl3s;

  # Environments name these as `gotgPkgs.<name>`, passed alongside pkgs so an
  # env file never has to reach back up the tree with a relative path.
  tools = {
    # Donkey Kong 64: Recompiled -- not in nixpkgs, though its siblings
    # zelda64recomp and n64recomp are.
    dk64recomp = pkgs.callPackage ../pkgs/dk64recomp { SDL2 = sdl3s.sdl2; };
    # Snowboard Kids 2: Recompiled -- the same N64Recomp/RT64 stack as
    # dk64recomp, and likewise not in nixpkgs.
    snowboardkids2recomp = pkgs.callPackage ../pkgs/snowboardkids2recomp { SDL2 = sdl3s.sdl2; };
    # Super Smash Bros. (N64) -- the libultraship port, not a recomp.
    battleship = pkgs.callPackage ../pkgs/battleship { SDL2 = sdl3s.sdl2; };
    # Paper Mario -- PaperBoat, Harbour Masters' libultraship port.
    paperboat = pkgs.callPackage ../pkgs/paperboat { SDL2 = sdl3s.sdl2; };
    # Its asset extractor, run by the launch instead of PaperBoat's wizard.
    paperboat-torch = pkgs.callPackage ../pkgs/paperboat-torch { };
    # Pikmin -- the native port off the projectPiki decompilation.
    open-nectar = pkgs.callPackage ../pkgs/open-nectar { };
    # Super Smash Bros. Melee -- the native port off doldecomp/melee.
    melee-pc = pkgs.callPackage ../pkgs/melee-pc { };
    # nixpkgs' Ryubing with the JIT cache size upstream Ryujinx shipped; see
    # the package for the crash the 1.3.3 default has.
    ryubing = pkgs.callPackage ../pkgs/ryubing { };
    # Extracts and rebuilds GameCube discs. wiimms-iso-tools can do the
    # first but rebuilds as a Wii disc, which boots into nothing.
    pyisotools = pkgs.callPackage ../pkgs/pyisotools { };
    # Not in nixpkgs, though its sibling wiimms-iso-tools is. Needed to
    # open and rebuild the Yaz0 archives GameCube games keep their data in.
    wiimms-szs-tools = pkgs.callPackage ../pkgs/wiimms-szs-tools { };
    # Only the Four Swords Adventures split-screen variants name this, so only
    # they build it -- sway, gamescope and bwrap are not the client's problem.
    splitscreen = pkgs.callPackage ../pkgs/splitscreen { };
    # The relay Ship of Harkinian's co-op talks through, run locally so four
    # copies of Ocarina of Time on one sofa need no internet.
    anchor-server = pkgs.callPackage ../pkgs/anchor-server { };
  };
}
