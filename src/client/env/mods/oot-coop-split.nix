# Ocarina of Time for more than one, on one screen.
#
# Ship of Harkinian carries Anchor: every player runs their own copy of the
# game and the copies meet through a relay -- item gives, flag sets, and each
# player's Link drawn in the others' worlds. What it does not do is run four
# copies, point them at one relay, hand each its own controller and put the
# windows in one frame; that is this, the way sm64-coop-split.nix does it for
# Mario. The relay is the project's own server, run here for the session so
# a sofa needs no internet to talk to itself.
#
# Controllers are the one part that is not a setting. Ship of Harkinian maps
# whatever gamepad it finds first to port 1, and four copies would all find
# the same one -- so each copy is started in a sandbox where the only gamepad
# under /dev/input is its player's. See coop-seats.nix.
{ pkgs, lib }:
let
  anchorPort = 43383; # the relay listens here and nowhere else; it has no flag
  seat = import ./coop-seats.nix { inherit pkgs; };
in
{
  harkinianCoopSplit =
    {
      gotgPkgs,
      base,
      players,
    }:
    let
      split = gotgPkgs.splitscreen;
      server = gotgPkgs.anchor-server;
      soh = "${base.emulator}/bin/${base.bin}";

      # Start the relay unless one is already answering, and wait until it
      # does. From the launcher's own pre-launch rather than the wrapper's:
      # the wrapper sweeps a pre-launch step's process group when it is done,
      # which took the watcher with it and left the relay running for ever.
      # Here the watcher is its own session -- nothing that sweeps groups
      # reaches it -- and it holds the launcher's pid, which the frame
      # inherits through exec, so the relay goes exactly when the frame does.
      ensureRelay = pkgs.writeShellScript "gotg-anchor-ensure" ''
        dir="$1" parent="$2"
        up() { (exec 3<>/dev/tcp/127.0.0.1/${toString anchorPort}) 2>/dev/null; }
        up && exit 0
        mkdir -p "$dir"
        (cd "$dir" && ${pkgs.util-linux}/bin/setsid ${server}/bin/anchor-server </dev/null >anchor.log 2>&1 &)
        for _ in $(seq 1 40); do up && break; sleep 0.25; done
        up || { echo "gotg: the Anchor relay did not come up; see $dir/anchor.log" >&2; exit 1; }
        ${pkgs.util-linux}/bin/setsid ${pkgs.bash}/bin/bash -c '
          while kill -0 "$1" 2>/dev/null; do sleep 2; done
          ${pkgs.procps}/bin/pkill -x anchor-server 2>/dev/null || true
        ' watcher "$parent" </dev/null >/dev/null 2>&1 &
        exit 0
      '';
    in
    {
      title = "Ocarina of Time (${toString players} players)";
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

      # Each player is a Ship of Harkinian of their own: its saves, its
      # settings. The game's own extracted archive is shared, as a link.
      saves = [
        "coop/*/Save/**"
        "coop/*/shipofharkinian.json"
      ];
      saveExcludes = [
        "coop/*/logs/**"
        "coop/*/imgui.ini"
        "coop/*/*.o2r"
      ];

      preLaunch =
        # The plain game's extraction is this one's too: the archive is the
        # ROM's, not the variant's, and the base's first-run step below only
        # extracts when it finds none. A launch that already played Ocarina
        # of Time alone does not sit through the extraction again.
        ''
          gotg_plain="''${state%-[0-9]p}/data/soh"
          gotg_mine="''${XDG_DATA_HOME:-$HOME/.local/share}/soh"
          if [ ! -e "$gotg_mine/oot.o2r" ] && [ -e "$gotg_plain/oot.o2r" ]; then
            mkdir -p "$gotg_mine"
            for gotg_a in oot.o2r oot-mq.o2r shipofharkinian.json; do
              [ -e "$gotg_plain/$gotg_a" ] && [ ! -e "$gotg_mine/$gotg_a" ] && cp "$gotg_plain/$gotg_a" "$gotg_mine/$gotg_a"
            done
          fi
        ''
        # The base's is the first-run extraction of the archive from the ROM;
        # without it there is nothing for four copies to load.
        + (base.preLaunch or "")
        + ''
          gotg_data="''${XDG_DATA_HOME:-$HOME/.local/share}/soh"
          gotg_coop="$state/coop"
          mkdir -p "$gotg_coop" "$state/splitscreen" "$state/anchor"

          # The relay, up before any copy of the game asks for it. $$ is this
          # launcher, and the frame it execs into keeps the pid.
          ${ensureRelay} "$state/anchor" "$$" ||
            echo "gotg: no relay; the copies will play alone" >&2

          gotg_instances='[]'
          for gotg_n in $(seq 1 ${toString players}); do
            gotg_home="$gotg_coop/p$gotg_n"
            mkdir -p "$gotg_home"
            # One archive, extracted once, linked into every player's home;
            # the same for the mods directory, so a texture pack is everyone's.
            for gotg_a in oot.o2r oot-mq.o2r; do
              [ -e "$gotg_data/$gotg_a" ] && ln -sfn "$gotg_data/$gotg_a" "$gotg_home/$gotg_a"
            done
            [ -d "$gotg_data/mods" ] && ln -sfn "$gotg_data/mods" "$gotg_home/mods"

            # Settings start as a copy of the plain game's, so graphics choices
            # carry; then the few this launch decides, set on every launch:
            # the relay to talk to, the room and name to join it as, and a
            # window rather than a fullscreen that would cover the others.
            gotg_cfg="$gotg_home/shipofharkinian.json"
            if [ ! -f "$gotg_cfg" ]; then
              if [ -f "$gotg_data/shipofharkinian.json" ]; then
                cp "$gotg_data/shipofharkinian.json" "$gotg_cfg"
              else
                printf '{}\n' >"$gotg_cfg"
              fi
            fi
            ${pkgs.jq}/bin/jq --arg name "P$gotg_n" \
              '.CVars.gRemote.Anchor = ((.CVars.gRemote.Anchor // {}) + {
                 Enabled: 1, Host: "127.0.0.1", Port: ${toString anchorPort},
                 RoomId: "gotg", TeamId: "default", Name: $name })
               | .Window.Fullscreen.Enabled = false' \
              "$gotg_cfg" >"$gotg_cfg.gotg" && mv "$gotg_cfg.gotg" "$gotg_cfg"

            # This player's controller, as the one input device the copy can
            # see -- or none. See coop-seats.nix for why never everyone's.
            gotg_seat="$(${seat} "$gotg_n")"

            # Under a gamescope of its own, sized to the slot: the copy sees a
            # monitor exactly that big, so a fullscreen setting somebody saved,
            # or the extra windows the port opens, cannot leave the slot or
            # take a seat meant for another copy.
            gotg_instance="$(
              ${pkgs.jq}/bin/jq -nc --arg id "p$gotg_n" --arg soh ${lib.escapeShellArg soh} \
                --arg home "$gotg_home" --argjson seat "$gotg_seat" \
                '{ id: $id, command: [$soh], cwd: $home, gamescope: true,
                   devices: $seat.devices, isolate_input: $seat.isolate,
                   env: { SHIP_HOME: $home, SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS: "1" } }'
            )"
            gotg_instances="$(
              ${pkgs.jq}/bin/jq -nc --argjson acc "$gotg_instances" --argjson one "$gotg_instance" '$acc + [$one]'
            )"
          done

          ${pkgs.jq}/bin/jq -n --argjson instances "$gotg_instances" \
            '{ layout: { name: "grid" }, instances: $instances }' \
            >"$state/splitscreen/session.json"
        '';
    };
}
