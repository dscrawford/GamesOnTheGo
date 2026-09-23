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
      # --pad "padmap:N" below. The wrapper finds player N's clone by its
      # GUID, which carries a CRC of the real name taken before SDL renames
      # anything, and writes the name it finds in the pad list -- which is
      # why the list is rewritten below to say what Dolphin calls each clone.
      # What Dolphin calls padmap's clones, put in the pad list in place of
      # what gotg-pads calls them.
      #
      # The two disagree, and Dolphin's own log is the one that counts. For
      # the Xbox pad's clone gotg-pads reports SDL's joystick name, `Xbox 360
      # Controller` -- the same name as the raw pad padmap has grabbed -- and
      # Dolphin, running under `padmap-rs exec` with padmap's mapping for that
      # GUID, lists it as `SDL/0/padmap Player 1`. The binder took gotg-pads'
      # word for it, wrote `GBA1 <- SDL/0/Xbox 360 Controller`, and bound
      # player one to the grabbed pad: nothing moved at all, because player
      # one drives the menus. Measured with Dolphin's CI log on:
      #
      #   Added device: SDL/0/Xbox 360 Controller     (the raw pad, grabbed)
      #   Added device: SDL/0/padmap Player 1         (its clone)
      #   Added device: SDL/0/padmap Player 2         (the Steam Controller's)
      #
      # A clone is recognised by its GUID's name-CRC, which SDL cannot
      # rename, and every clone's name is its own, so its slot is 0.
      dolphinNames = pkgs.writeText "gotg-fsa-dolphin-names.py" ''
        import json
        import sys

        PREFIX = "padmap Player "


        def crc16(data):
            crc = 0
            for byte in data:
                crc ^= byte
                for _ in range(8):
                    crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
            return crc & 0xFFFF


        NAMES = {crc16(f"{PREFIX}{n}".encode()): f"{PREFIX}{n}" for n in range(1, 9)}

        try:
            rows = json.load(sys.stdin)
        except ValueError:
            sys.exit(1)
        for row in rows if isinstance(rows, list) else []:
            guid = str(row.get("guid") or "")
            try:
                pair = bytes.fromhex(guid[4:8])
            except ValueError:
                continue
            if len(pair) == 2 and (pair[0] | pair[1] << 8) in NAMES:
                row["name"] = NAMES[pair[0] | pair[1] << 8]
                row["slot"] = 0
        json.dump(rows, sys.stdout)
      '';

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

          # Ask what *Dolphin* will see, not what the session sees.
          #
          # `--pad padmap:N` is resolved by a step that runs in the session,
          # outside the sandbox -- and out there the raw pads are visible
          # beside padmap's clones, and the slots count a different set of
          # pads than Dolphin will. And the names come out of the pipe below
          # as Dolphin says them, not as gotg-pads does: see `dolphinNames`.
          #
          # So the enumerator runs inside the sandbox, through padmap, exactly
          # as Dolphin will. `GOTG_PADS` is what the resolver looks for, and
          # the session hands its environment to the step.
          # HIDAPI off, for the same reason `pads_enumerate` turns it off:
          # gotg-pads sets SDL_HINT_JOYSTICK_HIDAPI_STEAM itself, so a raw
          # Steam Controller is bindable when padmap is not running. In here
          # padmap *is* running, and that driver claims Valve's ids and then
          # hides the evdev *clone* wearing them -- player one simply absent
          # from the list, which the session's log records as `padmap has
          # published no pad for player 1; using keyboard`. The environment
          # outranks the hint the binary sets.
          cat >"$state/splitscreen/gotg-pads" <<'SHIM'
          #!/bin/sh
          export SDL_JOYSTICK_HIDAPI=0 SDL_JOYSTICK_HIDAPI_STEAM=0
          ${gotgPkgs.padmap-rs}/bin/padmap-rs exec -- ${gotgPkgs.gotg-pads}/bin/gotg-pads "$@" |
            ${pkgs.python3}/bin/python3 ${dolphinNames}
          SHIM
          chmod +x "$state/splitscreen/gotg-pads"
          export GOTG_PADS="$state/splitscreen/gotg-pads"

          # And wait for those clones to be *enumerable* before anything reads
          # the list.
          #
          # `padmap-rs exec` republishes on the way into a launch, so the
          # clones a game will use are seconds old when this runs -- and a
          # device node exists before udev has finished with it, so SDL lists
          # it a moment after padmap made it. padmap's own log has the two
          # events a fifth of a second apart (`player 1: forwarding input to
          # the clone`, then player 2) in the same second the GBA bindings
          # were written, and that launch wrote `padmap has published no pad
          # for player 2; using keyboard`.
          #
          # This is not the Steam Controller failure above -- that one was the
          # hint, and is fixed by the two lines in the shim. This is the pad
          # that simply was not there yet.
          #
          # So: poll until as many of padmap's pads are listed as padmap says
          # it has seated, and carry on regardless after a few seconds --
          # a game that starts with one pad bound is better than one that
          # never starts.
          ${pkgs.python3}/bin/python3 - "$GOTG_PADS" <<'WAIT' || true
          import json, os, subprocess, sys, time

          PREFIX = "padmap Player "

          def crc16(data):
              crc = 0
              for byte in data:
                  crc ^= byte
                  for _ in range(8):
                      crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
              return crc & 0xFFFF

          wanted = {crc16(f"{PREFIX}{n}".encode()) for n in range(1, 9)}

          def clones(rows):
              found = 0
              for row in rows:
                  guid = str(row.get("guid") or "")
                  crc = None
                  if len(guid) >= 8:
                      try:
                          pair = bytes.fromhex(guid[4:8])
                          crc = pair[0] | (pair[1] << 8)
                      except ValueError:
                          crc = None
                  if crc in wanted or str(row.get("name") or "").startswith(PREFIX):
                      found += 1
              return found

          runtime = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
          try:
              # Only the seats with a device behind them: the keyboard takes a
              # seat too and has no clone to wait for.
              seated = json.load(open(f"{runtime}/padmap/assignments.json"))
              seats = sum(1 for seat in seated if isinstance(seat, dict) and seat.get("path"))
          except (OSError, ValueError):
              seats = 0
          if seats:
              # Two ways to stop: the pads padmap says it seated are all
              # listed, or the list has stopped growing. The second matters
              # because assignments.json outlives the daemon that wrote it --
              # a file left from last night would otherwise cost every launch
              # the whole timeout.
              end = time.monotonic() + 5.0
              was = -1
              rows = []
              while time.monotonic() < end:
                  try:
                      rows = json.loads(subprocess.run(
                          [sys.argv[1]], capture_output=True, text=True, timeout=10).stdout)
                  except (OSError, ValueError, subprocess.SubprocessError):
                      rows = []
                  found = clones(rows)
                  if found >= seats:
                      break
                  if found and found == was:
                      print(f"gotg: padmap seated {seats} pad(s) and SDL lists {found}; "
                            "one of the GBAs may come up on the keyboard", file=sys.stderr)
                      break
                  was = found
                  time.sleep(0.25)
              else:
                  print(f"gotg: padmap seated {seats} pad(s) and SDL lists none; "
                        "the GBAs will come up on the keyboard", file=sys.stderr)
              # What the binder is about to read, said out loud. Every FSA
              # failure so far has been a pad that padmap had seated and this
              # list did not have -- SDL's Steam driver hiding a clone, or a
              # node udev had not finished with -- and each one cost an
              # evening to work out from the outside. One line, every launch.
              for row in rows if isinstance(rows, list) else []:
                  if isinstance(row, dict):
                      print(f"gotg: sdl sees {row.get('name')!r} "
                            f"slot {row.get('slot')} guid {str(row.get('guid'))[:12]} "
                            f"gamepad {bool(row.get('gamepad'))}", file=sys.stderr)
          WAIT
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
