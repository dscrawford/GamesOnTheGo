# The checks, one substantial one per file. They had grown to two thirds of
# flake.nix, which meant every one of them was read past to reach the next.
#
# What stays here is only what a check is *made of*: the packages it asserts
# about, and the Python set it borrows a venv from. A check that is nothing but
# "this package builds" stays here too, since a file would be longer than it.
{
  pkgs,
  packages,
  py,
}:

{
  importer = packages.gotg-importer;

  # Building the wrapper runs no tests (unlike the old buildPythonApplication
  # checkPhase); python-tests is the gate.
  proxy = packages.gotg-proxy;

  client = packages.gotg;

  python-tests = import ./python-tests.nix { inherit pkgs py; };
  shellcheck = import ./shellcheck.nix { inherit pkgs; };
  client-tests = import ./client-tests.nix { inherit pkgs packages; };
  recipes = import ./recipes.nix { inherit pkgs packages; };
  switchContent = import ./switch-content.nix { inherit pkgs; };
  platforms = import ./platforms.nix { inherit pkgs py; };
  environments = import ./environments.nix { inherit pkgs; };
  fullscreen = import ./fullscreen.nix { inherit pkgs; };
  aresSystem = import ./ares-system.nix { inherit pkgs; };
  inheritsPlatform = import ./inherits-platform.nix { inherit pkgs; };
  sm64Coop = import ./sm64-coop.nix { inherit pkgs; };
  rust = import ./rust.nix { inherit pkgs; };
  ruff = import ./ruff.nix { inherit pkgs; };
}
