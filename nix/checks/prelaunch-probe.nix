# The settings a launch writes before the game starts, run rather than read.
#
# A preLaunch is shell text that edits an emulator's config for the machine it
# finds itself on, and every part of it that does anything ran, until now, only
# on a real machine. The Deck profiles, the helpers that turn on `GOTG_MACHINE`,
# the idempotent cheat merge: each can be wrong in a way that builds, passes
# shellcheck and does nothing until somebody launches a game on that hardware
# and sees the frame rate, the memory or the dock mode be wrong. The jq helper
# the simplification of src/client/env pulled out of eight scripts is the case
# that asked for this: the text it replaced was not byte-identical to the text
# it became, and what had to hold afterwards was the file the script leaves.
#
# Each probe takes the preLaunch text a helper produces, runs it under bash
# with the options the launcher uses (errexit, nounset, pipefail) in a scratch
# state directory, with GOTG_MACHINE and GOTG_EXTERNAL_DISPLAY set the way the
# probe wants them, and asserts on the files it wrote. Nothing here builds an
# emulator: the text is taken from the helper directly (the environments'
# `preLaunch` attribute is the same string the launcher embeds), and only the
# store paths the snippet really runs are kept in its context. That is jq,
# the ini files, the two small exefs files and the probe's own fixtures; the
# emulator, its Python and the rest are dropped, and the branches that would
# run them are not entered.
#
# `LD_PRELOAD` is not set in the sandbox, so the gotcha that `cheatsOn` and the
# UltraCam copy loop guard against (sdl2-compat aborting a preloaded `cmp` or
# `grep`) is not reproduced; what it protects is that the loops still decide
# right, which is asserted. That the `LD_PRELOAD=''` prefix is there is the
# launcher greps' job.
{ pkgs }:
let
  inherit (pkgs) lib;
  helpers = import ../../src/client/env/helpers.nix { inherit pkgs lib; };

  # The platform's own preLaunch, with the emulator named by a string: it is
  # only ever interpolated into a command that the probe does not reach (the
  # first-run config generation, which needs the config to be absent).
  switchEnv = import ../../src/client/env/switch.nix {
    inherit pkgs helpers;
    gotgPkgs.ryubing = "/nonexistent/ryubing";
  };

  # The few store paths a snippet runs. Anything else in a preLaunch's context
  # is an emulator or a tool for a branch the probes do not enter, and keeping
  # it would build it.
  keptName = "(jq-[0-9.]+|Main\\.ini|maxlastbreath\\.ini|main\\.npdm|subsdk3|probe-.*)";
  keep =
    text:
    let
      context = builtins.getContext text;
      nameOf = path: lib.removeSuffix ".drv" (builtins.substring 33 (-1) (builtins.baseNameOf path));
      wanted = lib.filterAttrs (path: _: builtins.match keptName (nameOf path) != null) context;
    in
    builtins.appendContext (builtins.unsafeDiscardStringContext text) wanted;
  script = name: text: pkgs.writeText "probe-${name}.sh" (keep text);

  # Fixtures the Ryujinx mod helpers copy from.
  modDir = pkgs.runCommand "probe-mod" { } ''
    mkdir -p $out/exefs
    echo patch >$out/exefs/1.0.0.pchtxt
  '';
  cheats = pkgs.writeText "probe-enabled-cheats" ''
    AAAA-<one Cheat>
    BBBB-<two Cheat>
  '';

  scripts = {
    totk =
      script "totk"
        (helpers.totkUltraCam {
          fps = 120;
          width = 2560;
          height = 1440;
          shadows = 2048;
        }).preLaunch;
    botw = script "botw" (helpers.botwUltraCam { fps = 120; }).preLaunch;
    dram = script "dram" (helpers.ryujinxDram 2).preLaunch;
    heavy =
      script "heavy"
        (helpers.ryujinxModOnly {
          titleId = "0100000000000001";
          name = "heavy";
          dir = modDir;
          onDeck = false;
        }).preLaunch;
    light =
      script "light"
        (helpers.ryujinxModOnly {
          titleId = "0100000000000002";
          name = "light";
          dir = modDir;
          enabledCheats = cheats;
        }).preLaunch;
    lus = script "lus" (
      helpers.lusSettings {
        file = ''"$cfg"'';
        unsaid = {
          "gOne.Two" = 1;
          "gFlag" = true;
          "gBar" = 3;
        };
      }
    );
    switch = script "switch" switchEnv.preLaunch;
  };
in
pkgs.runCommand "check-prelaunch-probe"
  {
    nativeBuildInputs = [ pkgs.jq ];
  }
  ''
    failed=
    fail() { echo "FAIL: $*" >&2; failed=1; }

    # A scratch state directory with a Ryujinx config in it (unless SEED is
    # empty), printed so the caller keeps it.
    fresh() {
      d="$TMPDIR/$1"
      rm -rf "$d"
      mkdir -p "$d/config/Ryujinx"
      [ -z "''${SEED:-}" ] || printf '%s\n' "$SEED" >"$d/config/Ryujinx/Config.json"
      echo "$d"
    }

    # launch <dir> <script> [VAR=value ...]: the snippet, run as the launcher
    # runs it, with only what the launcher sets and what the probe adds.
    launch() {
      d="$1"; s="$2"; shift 2
      env -i PATH="$PATH" XDG_CONFIG_HOME="$d/config" state="$d/state" \
        gotg_fullscreen= install= "$@" \
        bash -euo pipefail "$s" 2>"$d/stderr" ||
        { fail "$s exited nonzero"; cat "$d/stderr" >&2; }
    }

    # config <dir> <jq filter>: the Ryujinx config satisfies the filter.
    config() {
      jq -e "$2" "$1/config/Ryujinx/Config.json" >/dev/null ||
        { fail "$1: Config.json is not $2"; jq -c . "$1/config/Ryujinx/Config.json" >&2; }
    }

    # has <file> <text>: the file contains the text.
    has() { grep -qF -- "$2" "$1" || { fail "$1 lacks: $2"; cat "$1" >&2; }; }

    SEED='{"dram_size":7,"vsync_mode":1,"enable_custom_vsync_interval":false,"custom_vsync_interval":0,"docked_mode":true,"update_checker_type":"On","show_confirm_exit":true,"start_fullscreen":false,"ignore_missing_services":false}'

    echo "== UltraCam, Tears of the Kingdom"
    d=$(fresh totk-desktop)
    launch "$d" ${scripts.totk}
    config "$d" '.dram_size == 2 and .vsync_mode == 2 and .enable_custom_vsync_interval == true and .custom_vsync_interval == 120'
    ini="$d/config/Ryujinx/sdcard/UltraCam/TOTK/Config"
    has "$ini" 'MaxFPS = 120.0'
    has "$ini" 'DynamicFPS = True'
    has "$ini" 'Docked = {2560.00, 1440.00}'
    has "$ini" 'Shadows = 2048'
    for f in main.npdm subsdk3; do
      [ -s "$d/config/Ryujinx/mods/contents/0100f2c0115b6000/UltraCam/exefs/$f" ] || fail "totk: $f not installed"
    done
    # Run again: nothing to install, and the settings are the same.
    launch "$d" ${scripts.totk}
    grep -q 'installed UltraCam' "$d/stderr" && fail "totk: reinstalled the exefs files on the second launch"
    config "$d" '.dram_size == 2 and .custom_vsync_interval == 120'

    d=$(fresh totk-deck)
    launch "$d" ${scripts.totk} GOTG_MACHINE=deck
    # The Deck profile: no cap above 60, the dock's render no larger than
    # 1080p, shadows at 512, the console's own 4 GiB.
    config "$d" '.dram_size == 0 and .vsync_mode == 0 and .enable_custom_vsync_interval == false'
    ini="$d/config/Ryujinx/sdcard/UltraCam/TOTK/Config"
    has "$ini" 'MaxFPS = 60.0'
    has "$ini" 'Docked = {1920.00, 1080.00}'
    has "$ini" 'Handheld = {1280.00, 720.00}'
    has "$ini" 'Shadows = 512'

    echo "== UltraCam, Breath of the Wild"
    mod="config/Ryujinx/mods/contents/01007ef00011e000/!!!BOTW Optimizer"
    d=$(fresh botw-desktop)
    launch "$d" ${scripts.botw}
    config "$d" '.dram_size == 7 and .vsync_mode == 2 and .custom_vsync_interval == 120'
    has "$d/$mod/romfs/UltraCam/maxlastbreath.ini" 'MaxFramerate = 120'
    has "$d/$mod/romfs/UltraCam/maxlastbreath.ini" 'Width = 1920'
    [ -s "$d/$mod/exefs/subsdk3" ] || fail "botw: subsdk3 not installed"

    d=$(fresh botw-deck-panel)
    launch "$d" ${scripts.botw} GOTG_MACHINE=deck
    config "$d" '.vsync_mode == 0 and .enable_custom_vsync_interval == false'
    has "$d/$mod/romfs/UltraCam/maxlastbreath.ini" 'MaxFramerate = 60'
    has "$d/$mod/romfs/UltraCam/maxlastbreath.ini" 'Width = 1280'
    has "$d/$mod/romfs/UltraCam/maxlastbreath.ini" 'Height = 720'

    d=$(fresh botw-deck-dock)
    launch "$d" ${scripts.botw} GOTG_MACHINE=deck GOTG_EXTERNAL_DISPLAY=1
    has "$d/$mod/romfs/UltraCam/maxlastbreath.ini" 'MaxFramerate = 60'
    has "$d/$mod/romfs/UltraCam/maxlastbreath.ini" 'Width = 1920'
    has "$d/$mod/romfs/UltraCam/maxlastbreath.ini" 'Height = 1080'

    echo "== Ryujinx helpers"
    d=$(fresh dram-desktop)
    launch "$d" ${scripts.dram}
    config "$d" '.dram_size == 2'
    d=$(fresh dram-deck)
    launch "$d" ${scripts.dram} GOTG_MACHINE=deck
    config "$d" '.dram_size == 7'
    # No config yet is a message, not a failure: the platform generates it.
    d=$(SEED= fresh dram-noconfig)
    launch "$d" ${scripts.dram}
    has "$d/stderr" 'no Ryujinx config yet'

    # A mod a Deck cannot afford: installed elsewhere, removed there if an
    # earlier launch left it, and never installed there to begin with.
    d=$(fresh heavy-desktop)
    contents="$d/config/Ryujinx/mods/contents/0100000000000001"
    launch "$d" ${scripts.heavy}
    [ -f "$contents/heavy/exefs/1.0.0.pchtxt" ] || fail "heavy: not installed off a Deck"
    launch "$d" ${scripts.heavy} GOTG_MACHINE=deck
    [ ! -e "$contents/heavy" ] || fail "heavy: still installed after a Deck launch"
    has "$d/stderr" 'removed the heavy mod'
    d=$(fresh heavy-deck)
    launch "$d" ${scripts.heavy} GOTG_MACHINE=deck
    [ ! -e "$d/config/Ryujinx/mods/contents/0100000000000001/heavy" ] || fail "heavy: installed on a Deck"
    # A mod without onDeck = false is installed on a Deck.
    d=$(fresh light-deck)
    launch "$d" ${scripts.light} GOTG_MACHINE=deck
    [ -f "$d/config/Ryujinx/mods/contents/0100000000000002/light/exefs/1.0.0.pchtxt" ] || fail "light: not installed on a Deck"

    # Cheats merge into the game's list: ours added, theirs and a line we
    # already have left as they were, and a second launch changes nothing.
    d=$(fresh cheats)
    enabled="$d/config/Ryujinx/mods/contents/0100000000000002/cheats/enabled.txt"
    mkdir -p "$(dirname "$enabled")"
    printf 'ZZZZ-<theirs Cheat>\nAAAA-<one Cheat>\n' >"$enabled"
    launch "$d" ${scripts.light}
    [ "$(wc -l <"$enabled")" = 3 ] || { fail "cheats: expected 3 lines"; cat "$enabled" >&2; }
    has "$enabled" 'ZZZZ-<theirs Cheat>'
    has "$enabled" 'AAAA-<one Cheat>'
    has "$enabled" 'BBBB-<two Cheat>'
    [ "$(grep -c 'switched a light cheat on' "$d/stderr")" = 1 ] || fail "cheats: should have switched exactly one on"
    launch "$d" ${scripts.light}
    [ "$(wc -l <"$enabled")" = 3 ] || { fail "cheats: second launch changed the list"; cat "$enabled" >&2; }
    grep -q 'switched a' "$d/stderr" && fail "cheats: second launch switched something on again"

    echo "== lusSettings"
    d=$(fresh lus-new)
    cfg="$d/lus.json"
    launch "$d" ${scripts.lus} cfg="$cfg"
    jq -e '.CVars.gOne.Two == 1 and .CVars.gFlag == 1 and .CVars.gBar == 3 and .Window.Fullscreen.Enabled == false' "$cfg" >/dev/null ||
      { fail "lus: new file"; cat "$cfg" >&2; }
    d=$(fresh lus-new-fullscreen)
    cfg="$d/lus.json"
    launch "$d" ${scripts.lus} cfg="$cfg" gotg_fullscreen=1
    jq -e '.Window.Fullscreen.Enabled == true' "$cfg" >/dev/null || fail "lus: fullscreen flag not set"

    # Only what is unsaid is written; the flag follows how this launch began.
    d=$(fresh lus-existing)
    cfg="$d/lus.json"
    printf '%s\n' '{"CVars":{"gOne":{"Two":9},"gFlag":0},"Window":{"Fullscreen":{"Enabled":true},"Other":1},"Extra":"keep"}' >"$cfg"
    launch "$d" ${scripts.lus} cfg="$cfg"
    jq -e '.CVars.gOne.Two == 9 and .CVars.gFlag == 0 and .CVars.gBar == 3
           and .Window.Fullscreen.Enabled == false and .Window.Other == 1 and .Extra == "keep"' "$cfg" >/dev/null ||
      { fail "lus: existing file"; cat "$cfg" >&2; }
    [ ! -e "$cfg.gotg-tmp" ] || fail "lus: left its temp file"

    # A settings file jq cannot read is left as it was, with no litter.
    d=$(fresh lus-broken)
    cfg="$d/lus.json"
    printf 'not json\n' >"$cfg"
    launch "$d" ${scripts.lus} cfg="$cfg"
    [ "$(cat "$cfg")" = 'not json' ] || fail "lus: an unreadable file was replaced"
    [ ! -e "$cfg.gotg-tmp" ] || fail "lus: left its temp file after jq failed"

    echo "== switch.nix"
    # The first-run branch (generate a config by starting Ryujinx) is skipped
    # by there being a config; the register branch by there being no install.
    d=$(fresh switch-desktop)
    launch "$d" ${scripts.switch} gotg_fullscreen=1
    config "$d" '.update_checker_type == "Off" and .show_confirm_exit == false and .start_fullscreen == true
                 and .ignore_missing_services == true and .docked_mode == true and .dram_size == 7'
    d=$(fresh switch-windowed)
    launch "$d" ${scripts.switch}
    config "$d" '.start_fullscreen == false'
    # A Deck: handheld unless a television is on it, and the console's 4 GiB.
    d=$(fresh switch-deck)
    launch "$d" ${scripts.switch} GOTG_MACHINE=deck
    config "$d" '.docked_mode == false and .dram_size == 0 and .ignore_missing_services == true'
    d=$(fresh switch-deck-dock)
    launch "$d" ${scripts.switch} GOTG_MACHINE=deck GOTG_EXTERNAL_DISPLAY=1
    config "$d" '.docked_mode == true and .dram_size == 0'
    # Pinned on every launch, not seeded once: a config that drifted is put back.
    launch "$d" ${scripts.switch}
    config "$d" '.update_checker_type == "Off" and .dram_size == 0'

    [ -z "$failed" ] || exit 1
    touch $out
  ''
