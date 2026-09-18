# Super Mario 64 for more than one, on one screen.
#
# sm64coopdx is a *networked* co-op port: every player runs their own copy and
# they meet over a socket. On one sofa that is four windows to arrange and a
# lobby to join by typing an address, so this launches them, joins them to each
# other, and puts the windows in one frame.
#
# Four Swords Adventures needed a bespoke layout in SplitScreenWrapper — a
# television with Game Boy Advances down its sides. This needs none: every
# window is the same size and shows the same kind of thing, which is the
# wrapper's built-in grid. So there is no new mode, only a session file naming
# N copies of one game — and one place where who hosts, which port, which save
# directory and which controller all agree.
{ pkgs, lib }:
let
  seat = import ./coop-seats.nix { inherit pkgs; };
  # A UDP port nobody is using. coopdx binds AF_INET6 with in6addr_any, so the
  # probe has to look the same way round or it would report a port free that
  # the game cannot have.
  freePort = pkgs.writers.writePython3Bin "gotg-sm64-coop-port" { } ''
      import socket

      sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
      sock.bind(("::", 0))
      print(sock.getsockname()[1])
      sock.close()
  '';

  # Wait until the host has the port, before letting a client ask to join.
  #
  # There is no handshake to watch for — the protocol is UDP, so nothing
  # listens in the sense a connect() could find. What *is* observable is that
  # the port stops being bindable, which is exactly the moment the host has it.
  # A client that starts first is not fatal (it retries) but it spends the
  # first seconds of the game on a "connecting" screen for no reason.
  waitForHost = pkgs.writers.writePython3Bin "gotg-sm64-coop-wait" { } ''
      import socket
      import sys
      import time

      port = int(sys.argv[1])
      deadline = time.monotonic() + 60

      while time.monotonic() < deadline:
          probe = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
          try:
              probe.bind(("::", port))
          except OSError:
              sys.exit(0)  # taken, so the host is up
          finally:
              probe.close()
          time.sleep(0.25)

      print(f"nothing took port {port}; starting anyway", file=sys.stderr)
      sys.exit(0)
  '';
in
{
  # `players` is the whole configuration, as it is for Four Swords: the layout,
  # the save directories, the controller numbers and who hosts all follow from
  # it, in one place.
  sm64CoopSplit =
    {
      gotgPkgs,
      base,
      players,
    }:
    let
      split = gotgPkgs.splitscreen;
      # The port the plain `pc` launch would have used, named from the base so
      # that a co-op launch and a solo one can never end up on different builds
      # of the game — and, because the base compiles into one directory keyed
      # by its source rather than into each environment's state, so that four
      # players is one compile rather than four.
      port = "${base.emulator}/bin/${base.bin}";
    in
    {
      # The frame is what gotg launches; the frame launches the games.
      emulator = split;
      bin = "splitscreen-session";
      # --workdir as well as the session: without it the frame keeps its logs
      # in a fresh /tmp directory per launch, which is where they were the one
      # time anybody needed them.
      args = [
        "{state}/splitscreen/session.json"
        "--workdir"
        "{state}/splitscreen"
      ];

      # Each player gets their own save directory, so `saves` is the set of
      # them rather than the single one the solo port keeps.
      saves = [ "coop/**" ];
      saveExcludes = [ "coop/*/tex/**" ];

      preLaunch =
        # The base's is what compiles the port against the ROM on a first run.
        # Dropping it would leave nothing to launch — and nothing to say why.
        (base.preLaunch or "")
        + ''
          gotg_coop="$state/coop"
          mkdir -p "$gotg_coop" "$state/splitscreen"

          # Chosen per launch rather than fixed: two sofas on one machine, or a
          # session that crashed with the socket still in TIME_WAIT, are both
          # "the port is taken" and neither is worth explaining to anybody.
          gotg_port="$(${freePort}/bin/gotg-sm64-coop-port)"

          gotg_instances='[]'
          for gotg_n in $(seq 1 ${toString players}); do
            gotg_save="$gotg_coop/p$gotg_n"
            mkdir -p "$gotg_save"

            # Which controller this copy reads: the one in its sandbox. Each
            # copy sees only its player's pad (coop-seats.nix), so the number
            # coopdx wants -- SDL's device index -- is always the first. It used
            # to be the player's place in `gotg controllers order`, which under
            # padmap counted the physical pad and its clone both.
            gotg_seat="$(${seat} "$gotg_n")"
            # Written into each player's own config because it is a *setting*,
            # not a flag: coopdx takes the controller number and the
            # read-it-without-focus switch from sm64config.txt and from nowhere
            # else. Only those two lines are touched, so everything a player
            # changes in the game's own menus survives the next launch.
            gotg_config="$gotg_save/sm64config.txt"
            touch "$gotg_config"
            ${pkgs.gnused}/bin/sed -i \
              -e '/^gamepad_number /d' -e '/^background_gamepad /d' "$gotg_config"
            {
              printf 'gamepad_number 0\n'
              # Three of the four windows are unfocused at any moment, and a pad
              # that only reports to the focused window would leave those three
              # players watching.
              printf 'background_gamepad true\n'
            } >>"$gotg_config"

            # Who hosts goes *first*, and never last, because of how the port
            # is parsed upstream:
            #
            #   arg_string("--client <ip>", argv[++i], ...);
            #   if ((i + 2) < argc) { arg_uint("--client <port>", argv[++i], ...); }
            #   else { gCLIOpts.networkPort = 7777; }
            #
            # With `--client <ip> <port>` at the end of the line, i + 2 == argc,
            # so the port somebody passed is dropped and 7777 is used instead.
            # Three players sat on a JOINING screen forever, dialling a port the
            # host was not on, and nothing anywhere said so. Keeping the flags
            # that follow means the count always works out.
            gotg_argv=(${lib.escapeShellArg port})
            if [ "$gotg_n" -eq 1 ]; then
              gotg_argv+=(--server "$gotg_port")
            else
              gotg_argv+=(--client 127.0.0.1 "$gotg_port")
            fi
            gotg_argv+=(
              --savepath "$gotg_save"
              --playername "P$gotg_n"
              --windowed
              --skip-intro --hide-loading-screen --skip-update-check --no-discord
            )

            # One argument per line into jq, rather than jq's own --args: a
            # positional that starts with a dash is read as an option there, and
            # every flag the game takes starts with two.
            gotg_command="$(
              printf '%s\n' "''${gotg_argv[@]}" |
                ${pkgs.jq}/bin/jq -Rsc 'split("\n")[:-1]'
            )"
            # Under a gamescope of its own, sized to the slot: the copy sees a
            # monitor exactly that big, so one that fullscreens itself, or a
            # graphics setting somebody changed, cannot leave its slot.
            gotg_instance="$(
              ${pkgs.jq}/bin/jq -nc --arg id "p$gotg_n" --argjson command "$gotg_command" \
                --argjson seat "$gotg_seat" \
                '{ id: $id, command: $command, gamescope: true,
                   devices: $seat.devices, isolate_input: $seat.isolate }'
            )"
            if [ "$gotg_n" -gt 1 ]; then
              gotg_instance="$(
                ${pkgs.jq}/bin/jq -c --arg port "$gotg_port" \
                  '.pre_launch = [["${waitForHost}/bin/gotg-sm64-coop-wait", $port]]' \
                  <<<"$gotg_instance"
              )"
            fi
            gotg_instances="$(
              ${pkgs.jq}/bin/jq -nc --argjson acc "$gotg_instances" \
                --argjson one "$gotg_instance" '$acc + [$one]'
            )"
          done

          # Written every launch rather than kept: it names this launch's port
          # and this machine's controllers, and a stale one would point four
          # copies of the game at a socket nobody is holding.
          ${pkgs.jq}/bin/jq -n --argjson instances "$gotg_instances" \
            '{ layout: { name: "grid" }, instances: $instances }' \
            >"$state/splitscreen/session.json"
        '';
    };
}
