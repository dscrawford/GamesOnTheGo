# dolphin, for the two disc platforms that share it. Split out of helpers.nix:
# what each emulator needs to be driven correctly is its own body of knowledge,
# and they were only ever neighbours in one file.
{
  pkgs,
  lib,
  discArchiveRecipe,
}:
{
  # dolphin, for the two disc platforms that share it.
  #
  # Two things here are about input rather than emulation, and both were learned
  # from a pad that worked in Dolphin's own configuration screen and did nothing
  # in the game:
  #
  # `-b` (batch) starts the game with no library window. Without it Dolphin opens
  # two windows, and only the render one reads a controller — so whichever window
  # the desktop happened to focus decided whether the pad did anything. The
  # configuration screen reads input regardless, which is what makes this look
  # like a binding fault when it is a focus fault.
  #
  # BackgroundInput is the setting that stops focus mattering at all. It is the
  # ini key; "Background Input" is only its label in the interface. Dolphin
  # writes this file itself, so it is edited in place on each launch rather than
  # seeded through configFiles, which only ever fills in a file that is absent.
  dolphinPlatform =
    { }:
    {
      emulator = pkgs.dolphin-emu;
      bin = "dolphin-emu";

      # Isolated because both of the input fixes below need this environment to
      # own its Dolphin configuration — bindings written into the player's own
      # install would be ours to get wrong on their behalf.
      isolate = true;

      # Which follows from isolating: Dolphin's data directory moves under
      # {state}, so a memory card written before this would otherwise be in a
      # directory nothing reads any more, which from the sofa is exactly what
      # losing it looks like. `gotg saves adopt` copies them forward.
      saves = [
        "data/dolphin-emu/GC/**"
        "data/dolphin-emu/Wii/**"
      ];
      legacyPaths = [
        {
          from = "$XDG_DATA/dolphin-emu/GC";
          into = "data/dolphin-emu";
        }
        {
          from = "$XDG_DATA/dolphin-emu/Wii";
          into = "data/dolphin-emu";
        }
      ];

      # GameCube pads only. A Wii game played with a GameCube controller is
      # covered; Wii remotes live in a different file with a different shape,
      # and nothing here pretends to write them.
      padEmulator = "dolphin";

      # Disc images that travelled as archives are extracted and converted to
      # RVZ here, on first install — the pipeline inversion's client half.
      recipes = discArchiveRecipe;

      args = [
        "-b"
        "-e"
        "{target}"
      ];
      preLaunch = ''
        # Dolphin writes these files itself and records only what differs from a
        # default, so there is usually no line to replace and often no section
        # either — which is why this sets a key rather than substituting one.
        gotg_ini_set() {
          gotg_ini_file="$1"
          gotg_ini_section="[$2]"
          gotg_ini_key="$3"
          gotg_ini_value="$4"

          mkdir -p "$(dirname "$gotg_ini_file")"
          touch "$gotg_ini_file"
          ${pkgs.gawk}/bin/awk \
            -v section="$gotg_ini_section" \
            -v key="$gotg_ini_key" \
            -v value="$gotg_ini_value" '
            BEGIN { line = key " = " value }
            # Leaving the section without having written the key: write it now,
            # ahead of the header that ends the section.
            /^\[/ {
              if (inSection && !written) { print line; written = 1 }
              inSection = ($0 == section)
            }
            inSection && index($0, key " ") == 1 { print line; written = 1; next }
            { print }
            END {
              if (!written) {
                if (!inSection) print section
                print line
              }
            }
          ' "$gotg_ini_file" > "$gotg_ini_file.gotg" &&
            mv "$gotg_ini_file.gotg" "$gotg_ini_file"
        }

        # Only where this environment owns its Dolphin configuration. Otherwise
        # this would be reaching into the settings of the player's own Dolphin
        # install, which is not ours to change. Tested at runtime rather than
        # against `isolate` here, so that a game file turning isolation on gets
        # this too.
        if [ "''${XDG_CONFIG_HOME:-}" = "$state/config" ]; then
          gotg_ini_set "$XDG_CONFIG_HOME/dolphin-emu/Dolphin.ini" \
            Input BackgroundInput True

          # Straight to the game; -b already skips the UI. Dolphin takes this
          # from its ini rather than the command line, so it reads the same
          # decision lib.nix made — True from Steam, False at a desk.
          gotg_ini_set "$XDG_CONFIG_HOME/dolphin-emu/Dolphin.ini" \
            Display Fullscreen "$([ -n "$gotg_fullscreen" ] && echo True || echo False)"

          # An NKit-processed disc opens a modal warning before the game, with a
          # "don't show this again" box — which is fine at a desk and is a
          # session that never starts on a sofa, or inside a split-screen frame
          # where nothing has told the dialog where to go. The library is
          # largely NKit RVZ, so this is answered once here rather than per
          # environment by whoever finds it.
          gotg_ini_set "$XDG_CONFIG_HOME/dolphin-emu/Dolphin.ini" \
            Interface SkipNKitWarning True

          # "Do you want to stop the current emulation?" — which nobody can
          # answer with a controller, and which is the last thing between a
          # game and the kill switch.
          #
          # It does not protect a save; it costs one. gotg-killswitch sends
          # SIGTERM, waits five seconds and then SIGKILLs, and Dolphin spends
          # those five seconds holding a modal dialog open instead of writing
          # its memory card. Off, the SIGTERM is taken and the shutdown is the
          # orderly one; on, every stop is the hard one.
          #
          # These consoles had a power switch, and pressing it was not a
          # question.
          gotg_ini_set "$XDG_CONFIG_HOME/dolphin-emu/Dolphin.ini" \
            Interface ConfirmStop False

          # Vulkan rather than Dolphin's OpenGL default: the Mesa/RDNA2
          # handhelds this targets run markedly faster on it.
          gotg_ini_set "$XDG_CONFIG_HOME/dolphin-emu/Dolphin.ini" \
            Core GFXBackend Vulkan

          # What reads as "a little slow" is usually shader compilation, not
          # throughput. Mode 2 (hybrid ubershaders) draws through the
          # ubershader while the specialized shader compiles in the
          # background, and the warm-up compile clears the cached backlog
          # before the game starts instead of as stutter inside it.
          gotg_ini_set "$XDG_CONFIG_HOME/dolphin-emu/GFX.ini" \
            Settings ShaderCompilationMode 2
          gotg_ini_set "$XDG_CONFIG_HOME/dolphin-emu/GFX.ini" \
            Settings WaitForShadersBeforeStarting True

          # Internal resolution, from the player's own preference rather than
          # this flake: what looks right depends on the screen in front of you,
          # which is not something a derivation can know.
          #
          # Dolphin scales by whole multiples of the GameCube's 640x528, and
          # labels them by the width that lands on: 640*N wide, so 3x is 1080p
          # and 4K is 6x. Read off Dolphin's own label format, not guessed.
          gotg_res=default
          if [ -f "$GOTG_USER_CONFIG/video.json" ]; then
            gotg_res="$(${pkgs.jq}/bin/jq -r '.resolution // "default"' \
              "$GOTG_USER_CONFIG/video.json" 2>/dev/null || echo default)"
          fi

          gotg_scale=""
          case "$gotg_res" in
            default | native) gotg_scale="" ;;
            720p) gotg_scale=2 ;;
            1080p) gotg_scale=3 ;;
            1440p) gotg_scale=4 ;;
            4k | 4K) gotg_scale=6 ;;
            5k | 5K) gotg_scale=8 ;;
            [1-8]x) gotg_scale="''${gotg_res%x}" ;;
            *)
              echo "gotg: unknown resolution '$gotg_res' in $GOTG_USER_CONFIG/video.json" >&2
              echo "gotg: expected default, 720p, 1080p, 1440p, 4k, 5k, or 1x-8x" >&2
              ;;
          esac

          # "default" writes nothing at all, deliberately. It means this is not
          # ours to manage, so whatever Dolphin or the player set in its own
          # settings screen survives — rather than being reset on every launch.
          if [ -n "$gotg_scale" ]; then
            gotg_ini_set "$XDG_CONFIG_HOME/dolphin-emu/GFX.ini" \
              Settings InternalResolution "$gotg_scale"
          fi
        fi
      '';
    };

}
