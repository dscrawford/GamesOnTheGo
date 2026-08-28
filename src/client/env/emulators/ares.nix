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
