# gotg — the GamesOnTheGo client

Downloads a game from the server the first time you launch it, then runs it in
the right emulator. Meant to be driven from Steam.

```
gotg login                  save the service URL and token (mode 600)
gotg refresh                re-fetch the catalog
gotg list [pattern]         games matching a regex; 50 at a time
gotg info <id>              one game's details
gotg download <id>          fetch a game, nothing else
gotg install <id>           fetch + build its environment + write a Steam launcher
gotg play <id>              fetch and build what is missing, then launch
gotg saves <command>        setup / status / push / pull / adopt, between machines
gotg controllers <command>  list what SDL sees; order; apply bindings
gotg configure <id> [var]   open that environment's own emulator settings
gotg steam <command>        add / remove / art / list non-Steam games
gotg sync                   rebuild the GC roots after a git pull
```

## Layout

| File | Responsibility |
|---|---|
| `bin/gotg` | argument dispatch only |
| `lib/color.sh` | the palette, and the rules for when there is none |
| `lib/common.sh` | paths, logging, id and path validation |
| `lib/config.sh` | the service url + token in a 0600 api.json, `gotg login` |
| `lib/manifest.sh` | the catalog, id resolution, `list` and `info` |
| `lib/download.sh` | staging, resume, checksums, unzip, progress UI |
| `lib/env.sh` | which environment, GC roots, building one |
| `lib/launcher.sh` | rendering the Steam launcher |
| `lib/cmd-play.sh` | `install`, `play`, `sync` |
| `lib/cmd-configure.sh` | `configure` — the emulator's own settings screen |
| `lib/cmd-steam.sh` | `steam` — which game, which launcher, which account |
| `steam/shortcuts.py` | Steam's binary shortcuts.vdf, read and written |
| `steam/artwork.py` | grids, heroes, logos and icons, from two sources |
| `steam/libretro.py` | the source that needs no key, matched on No-Intro names |
| `lib/keys.sh` | console keys, fetched from beside the games |
| `lib/firmware.sh` | console firmware, cached once and hardlinked into each environment |
| `lib/cmd-complete.sh` | the lists shell completion asks for |
| `completions/gotg.bash` | bash completion, installed where bash looks |
| `lib/saves.sh` | what a save is: bundles, generations, verifying and placing them |
| `lib/remote.sh` | save and retrieve, against the one GOTG service |
| `lib/cmd-saves.sh` | `saves setup`, `status`, `push`, `pull`, `adopt` |
| `lib/pads.sh` | generating emulator bindings from what SDL reports |
| `lib/pads-dolphin.sh` | the same for Dolphin, which needs no table |
| `lib/pads-ryujinx.sh` | Ryujinx bindings kept and healed, rather than generated; motion switched on |
| `lib/pads-cemu.sh` | Cemu's `<motion>` flag, for a profile whose pad has a gyro |
| `lib/cmd-controllers.sh` | `controllers list`, `controllers apply` |
| `env/<platform>.nix` | the emulator, arguments and settings for a platform |
| `env/games/<platform>/<id>.nix` | what one game changes about that |

`env/` is nix, built by the flake, but it is also shipped in the package: the CLI
reads the *names* of those files to work out which flake attribute a game wants,
which is how `list`, `info` and any launch of something already built stay clear
of nix entirely.

## Things worth knowing

**Colour marks the exceptions.** Ordinary output is plain, because if
everything is highlighted then nothing is: what takes a colour is a warning, an
error, a game that is installed, saves that have changed since the last sync.
`NO_COLOR` turns it off, `FORCE_COLOR` turns it on without a terminal, and
`TERM=dumb` is believed; otherwise colour appears only when both streams are a
terminal. That last rule is why nothing here has to know about Steam — a launch
has both streams redirected to a log file, so it is uncoloured by the same rule
that uncolours a pipe. Errors and warnings still say "error" and "warning", so
the colour is never the only thing carrying the meaning.

**Downloads stage outside the platform directory** and are moved in only once
complete and verified. A half-downloaded file can never look like an installed
game to a launcher.

**`play` builds an environment only when one is missing**, and before it starts
downloading, so the failure most likely to need a person happens first rather
than at the end of a ten-gigabyte transfer. A launch that finds its GC root — the
usual one — never runs nix. When it does have to build, Steam gives it no
terminal, so it pulses a zenity dialog and writes the real error to the launch
log; `gotg install <id>` from a terminal is still the smoother first run.

## Tests

```bash
nix flake check     # or: GOTG_BIN=$(which gotg) bats src/client/tests/
```

Every suite runs against the real GOTG service — the same process that runs in
the cluster — seeded through its own index API, because the catalog, download
and conflict rules all live server-side now and a mock would only ever test a
copy of them. Resume is exercised against genuinely truncated transfers, not
simulated ones. `bats --jobs 8`: each test isolates its own state directory and
picks a free port, so they run concurrently.
