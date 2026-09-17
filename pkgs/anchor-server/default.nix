# The Anchor server — what Ship of Harkinian's co-op talks through.
#
# Anchor is a client/server mod: every player runs their own Ship of
# Harkinian and the copies meet through this, which relays item gives, flag
# sets and each player's position so the others can draw them. The client
# has been in SoH since 9.x and defaults to the project's public server; on a
# sofa the game should not need the internet to talk to itself, so this runs
# one locally for the session.
#
# Small: a Go program with two JSON helpers as dependencies, no database, no
# configuration -- it listens on 43383 and writes a stats.json in its working
# directory, which is why the launcher gives it one.
{
  lib,
  buildGoModule,
  fetchFromGitHub,
}:
buildGoModule {
  pname = "anchor-server";
  version = "0-unstable-2026-08-19";

  src = fetchFromGitHub {
    owner = "garrettjoecox";
    repo = "anchor";
    rev = "bf7b43c10b19428ceba54772c7bae3abca44a345";
    hash = "sha256-eUogAhS/WGDK9wO5RtLZXlyj6CJIwjFSOIrTqjMXZeU=";
  };

  vendorHash = "sha256-gV5uQW5jk4Cd647RZwx58d728JQe+CjHPBrJXIIEeGs=";

  # The repository is the server; the Discord bot beside it is TypeScript
  # and not built here.
  subPackages = [ "." ];
  doCheck = false;

  postInstall = ''
    mv $out/bin/anchor $out/bin/anchor-server
  '';

  meta = {
    description = "Relay server for Ship of Harkinian's Anchor co-op";
    homepage = "https://github.com/garrettjoecox/anchor";
    license = lib.licenses.mit;
    mainProgram = "anchor-server";
    platforms = lib.platforms.linux;
  };
}
