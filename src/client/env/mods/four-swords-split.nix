# Four Swords Adventures, split across the screen — the shape the game was
# built for, on one television.
#
# FSA wants a Game Boy Advance per player: the interesting screen moves between
# the television and the little one depending on where you are standing, and a
# player indoors is looking at their own. Dolphin can emulate those GBAs
# (SIDevice=13) but opens each as a window of its own, so on a desktop the game
# arrives as five windows to arrange by hand every launch and to keep arranged
# while playing.
#
# SplitScreenWrapper is what arranges them: one frame holding the game with the
# GBAs down its sides, and one place that decides which window, which port and
# which controller belong to which player. That dependency is pulled here and
# nowhere else — a machine that never launches this never builds it.
{ pkgs, lib }:
{
  # `players` is the whole configuration. The layout, the window rules, the
  # Dolphin ports and the controller bindings all follow from it, and follow
  # from it in one place, which is the point.
  fourSwordsSplit =
    {
      gotgPkgs,
      base,
      players,
    }:
    let
      split = gotgPkgs.splitscreen;
      # The same Dolphin the platform would have used. Named from the base
      # rather than reached for again, so the split launch and the plain one
      # can never end up on different emulators.
      #
      # Wrapped, though, and this is the whole reason the wrapper exists: the
      # session runs outside padmap's sandbox (ownsSession, below) because a
      # nested compositor cannot start Xwayland inside one, and that left the
      # game seeing every raw pad on the machine beside padmap's. The other
      # split modes do not care -- they hand each copy its own seat -- but
      # this one is a single Dolphin binding pads by name, and with the raw
      # pads visible it bound the Steam Controller where player 1 was an Xbox
      # pad. So the sandbox goes around the game instead of around the
      # session: the compositor stays outside it, Dolphin goes inside, and
      # what Dolphin can see is padmap's pads and nothing else.
      #
      # Which is half of it. The step that writes those bindings runs in the
      # session, outside the sandbox, so it enumerates every pad on the
      # machine and "the first one" is not the first one Dolphin will see --
      # it wrote `GBA1 <- SDL/0/Steam Deck`, a name that does not exist
      # inside. So the pads are not counted.
      #
      # Nor are they named. They were: --pad "sdl:padmap Player N", which is
      # what the *kernel* calls padmap's clones and what Dolphin never hears.
      # A clone mirrors the identity of the pad behind it, so SDL finds
      # 045e:028e in its own database and hands Dolphin "Xbox 360
      # Controller"; the name padmap gave it is gone. Four Swords Adventures
      # had no controls at all, and only for the pads SDL recognises -- the
      # clone of something it has never heard of keeps its name, which is why
      # this worked for one pad and not another.
      #
      # --pad "padmap:N" below. The wrapper finds player N's clone by its
      # GUID, which carries a CRC of the real name taken before SDL renames
      # anything, and keeps the slot that tells two pads of one model apart.
      dolphin = pkgs.writeShellScript "gotg-fsa-dolphin" ''
        exec ${gotgPkgs.padmap-rs}/bin/padmap-rs exec -- \
          ${base.emulator}/bin/${base.bin} "$@"
      '';
    in
    {
      title = "Four Swords Adventures (${toString players} players)";

      # The frame is what gotg launches; Dolphin is what the frame launches.
      emulator = split;
      bin = "splitscreen-session";
      # A nested sway with a gamescope per copy, which is why this
      # cannot run inside padmap's user namespace. See ownsSession in
      # env/lib.nix for what that breaks and why nothing is lost.
      ownsSession = true;
      args = [
        "{state}/splitscreen/session.json"
        "--workdir"
        "{state}/splitscreen"
      ];

      # The GBA's own BIOS, which Dolphin will not boot an emulated GBA
      # without. It lives in the *Game Boy Advance* directory on the server,
      # because that is whose file it is — one copy, whichever platform needs
      # it.
      keys = {
        platform = "gba";
        into = "bios";
        files = [ "bios.zip" ];
      };

      # Added to the platform's rather than replacing it. The platform's is
      # where Dolphin is told to read a pad it does not have focus on, which
      # backend to draw with, and not to stop on the NKit dialog — every one of
      # which this launch wants at least as much as a plain one, and all of
      # which a bare `preLaunch =` silently drops.
      preLaunch =
        (base.preLaunch or "")
        + ''
          # ares takes the BIOS as the zip it travels in; Dolphin wants the file
          # inside it. Unpacked once, here, rather than kept on the server twice.
          if [ ! -s "$state/bios/gba_bios.bin" ] && [ -s "$state/bios/bios.zip" ]; then
            ${pkgs.unzip}/bin/unzip -o -j "$state/bios/bios.zip" gba_bios.bin -d "$state/bios" >/dev/null ||
              echo "gotg: could not unpack the GBA BIOS; the GBAs will not boot" >&2
          fi

          # Written on every launch rather than kept: it names this launch's disc
          # and this environment's directories, and a stale one would point a
          # four-player session at whatever was played last.
          mkdir -p "$state/splitscreen"
          ${split}/bin/splitscreen-fsa \
            --players ${toString players} \
            ${lib.concatMapStringsSep " " (n: ''--pad "padmap:${toString n}"'') (lib.range 1 players)} \
            --gc "$target" \
            --gba-bios "$state/bios/gba_bios.bin" \
            --dolphin ${lib.escapeShellArg dolphin} \
            --config-dir "$state/config" \
            -o "$state/splitscreen/session.json" >/dev/null
        '';
    };
}
