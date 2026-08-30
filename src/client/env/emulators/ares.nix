# ares, for the cartridge platforms that share it. Split out of helpers.nix:
# what each emulator needs to be driven correctly is its own body of knowledge,
# and they were only ever neighbours in one file.
{ pkgs, lib }:
{
  # ares, for the cartridge platforms that share it.
  #
  # It does not consult XDG for saves. Emulator::locate reads settings.paths.saves,
  # which is empty by default, and falls back to the ROM's own path with the
  # extension swapped — so a memory save lands in ~/Games next to the ROM, where
  # env-snes and env-snes-world_super_metroid would also be writing over each
  # other. Paths/Saves is that setting, and **the trailing slash is load-bearing**:
  # ares concatenates it with the filename without inserting a separator.
  #
  # ares restores command-line overrides before it saves settings, so passing
  # this never rewrites the user's own settings.bml.
  #
  # The platforms share this because what they share is the part that has to be
  # right. A platform that needs to differ stops calling this and says so.
  aresPlatform =
    {
      platform,
      # The ares console section this platform's games appear under. Only set
      # where it has been read off a real settings.bml — ares creates the
      # section on first run, so it cannot be derived from the platform slug,
      # and a wrong name would write bindings nothing ever reads.
      console ? null,
      # Which core to run, as `ares --help` lists it. Unset, ares picks by file
      # extension and prompts when more than one core claims it (both Game Boy
      # cores do) — naming it here makes that choice once, not per launch.
      aresSystem ? null,
      # The directory ares files this platform's saves in (a subdirectory of
      # Paths/Saves, not Paths/Saves itself) — distinct from `aresSystem`, since
      # ares names it for the cartridge, not the core it ran.
      #
      # Measured on ares v148 with `--system "Game Boy Color"`: both a
      # CGB-enhanced and a CGB-only cart saved into "{state}/saves/Game Boy/",
      # never "Game Boy Color/" — the empty "Game Boy Color" directory under
      # env-gb is the older, guessed answer.
      #
      # This is what an older save is adopted into. A wrong guess writes it
      # somewhere ares never reads, indistinguishable from losing it — so an
      # unconfirmed directory adopts nothing.
      system ? null,
    }:
    {
      emulator = pkgs.ares;
      bin = "ares";
      isolate = true;
      padConsole = console;
      args = [
        # A launch from the sofa goes straight to the game; the windowed UI is
        # one Esc away when wanted. Expands to nothing from a terminal — see
        # the gotg_fullscreen block in lib.nix for why that is the default.
        "{fullscreen}"
        "--setting"
        "Paths/Saves={state}/saves/"

        # Audio latency, and it is not a preference — it is the difference
        # between sound and crackling.
        #
        # ruby/audio/sdl.cpp sizes its buffer as (latency * frequency) / 1000,
        # and ares v148 starts a fresh environment with latency 0. It never
        # recovers: initialize() writes the negotiated rate back into
        # `frequency` but never into `latency`, so Audio::latency() keeps
        # returning 0, hasLatency(0) stays false, and audioLatencyUpdate()
        # dutifully sets the setting to 0 again on every launch.
        #
        # A zero buffer is not silence, which is what makes it hard to place.
        # output() blocks while `bytesRemaining > _bufferSize`, so with a
        # buffer of 0 it waits for the device queue to drain *completely*
        # before writing each sample — the card is starved by construction and
        # the result is a permanent underrun.
        #
        # 60 has to be one of the values hasLatencies() lists — {10, 20, 40,
        # 60, 80, 100} — because a value outside it is rejected and replaced by
        # the same broken 0. Measured through PipeWire: without this the ares
        # node runs at quantum 960/48000, with it at 2880/48000, which is
        # exactly the (60 * 48000) / 1000 the source asks for.
        #
        # Frequency is pinned alongside it only so the buffer is right on the
        # very first initialize() rather than on the re-init a moment later;
        # that one does self-correct, since spec.freq is written back.
        #
        # Every ares platform, not just the one it was noticed on. The bug is
        # in ares, so it applies to all of them — the older environments here
        # escape it only because they were first run by an ares that still
        # negotiated a real latency, and would hit it the moment their state
        # was rebuilt.
        "--setting"
        "Audio/Frequency=48000"
        "--setting"
        "Audio/Latency=60"

        # No "now load a second ROM" dialog. A launch from here always names
        # exactly one game, and a modal file browser in front of it is
        # unanswerable from a sofa — there is no keyboard and the pad does not
        # drive a file dialog.
        #
        # Not hypothetical, and not only the 64DD. nintendo-64.cpp gates two
        # prompts on this: the 64DD disk, and a Transfer Pak asking for a Game
        # Boy cartridge for any cart whose database entry sets tpak. That is 19
        # games in ares' own table, ten of which are in this library — Pokémon
        # Stadium 1 and 2, Perfect Dark, Mario Golf, Mario Tennis among them.
        #
        # Safe for the rest: the flag only suppresses requests for *additional*
        # media, never the game named on the command line.
        "--no-file-prompt"
      ]
      ++ lib.optionals (aresSystem != null) [
        "--system"
        aresSystem
      ]
      ++ [ "{target}" ];
      saves = [ "saves/**" ];
      # Save states are tied to the ares that wrote them, so one carried from
      # another machine may simply refuse to load. Memory saves always travel.
      saveExcludes = [ "saves/*.bs[0-9]" ];
      # Where ares put them before it was told otherwise: beside the ROM, named
      # for it. `gotg saves adopt` copies these forward, into the directory ares
      # will actually read them from.
      legacyPaths = lib.optionals (system != null) (
        map
          (ext: {
            from = "$GAMES/${platform}/*.${ext}";
            into = "saves/${system}";
          })
          [
            "ram"
            "eeprom"
            "flash"
            "rtc"
            "iram"
            "bsx"
            "dram"
          ]
      );
    };

}
