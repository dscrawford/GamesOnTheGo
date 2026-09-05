# Shared shapes for environments, handed to every platform and game file as
# `helpers`. Anything here is a pattern more than one game needs; a setting only
# one game wants belongs in that game's file.
#
# The shapes themselves live one per file, under emulators/ and mods/. This is
# the seam they are handed across, and nothing but composition happens here —
# so a platform file still says `helpers.aresPlatform` and does not care that
# it moved.
{ pkgs, lib }:

let
  # The step vocabulary recipes compose from; see steps.nix for the contract.
  steps = import ./steps.nix { inherit pkgs; };

  # A scene release, client-side: verify the sfv when one exists, unrar the
  # volume set, keep the largest extracted file as the game. What execute.py
  # used to do on the server, now once per machine on first install.
  sceneArchiveRecipe = {
    scene_archive = [
      steps.verifySfv
      steps.unrar
      steps.pickLargest
      steps.keepExtension
    ];
  };

  # A Switch release plus whatever updates and DLC the catalog attached,
  # unpacked beside it and installed as one directory Ryujinx is pointed at.
  # Three handlers for the three ways a base arrives: rar set, 7z, loose file.
  switchRecipe = {
    scene_archive = [
      steps.verifySfv
      steps.unrar
      steps.pickLargest
      steps.collectExtras
      steps.placeBundle
    ];
    single_archive = [
      steps.extract7z
      steps.pickLargest
      steps.collectExtras
      steps.placeBundle
    ];
    single_file = [
      steps.pickBase
      steps.collectExtras
      steps.placeBundle
    ];
  };

  # A disc image that travelled as a 7z: extract, convert to the RVZ the
  # emulator wants, keep nothing else. Space cost is transient (staging holds
  # the raw image); the refined artifact is deterministic, so the raw members
  # are the client's to delete afterwards.
  discArchiveRecipe = {
    single_archive = [
      steps.extract7z
      steps.pickLargest
      steps.convertRvz
    ];
  };

  # How a game is launched, one emulator per file.
  emulators = [
    (import ./emulators/ares.nix { inherit pkgs lib; })
    (import ./emulators/dolphin.nix { inherit pkgs lib discArchiveRecipe; })
    (import ./emulators/ryujinx.nix { inherit pkgs lib; })
    (import ./emulators/harkinian.nix { inherit lib steps; })
  ];

  # What a mod does to one game's own files, one game per file.
  mods = [
    (import ./mods/sunshine.nix { inherit pkgs; })
    (import ./mods/luigis-mansion-2.nix { inherit pkgs; })
    (import ./mods/totk-ultracam.nix { inherit pkgs lib; })
  ];

in
{
  inherit
    steps
    sceneArchiveRecipe
    discArchiveRecipe
    switchRecipe
    ;
}
// lib.foldl' (a: b: a // b) { } (emulators ++ mods)
