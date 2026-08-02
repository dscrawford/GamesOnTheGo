# gotg — the GamesOnTheGo client

Downloads a game from the server the first time you launch it, then runs it in
the right emulator. Meant to be driven from Steam.

```
gotg login                  save the server URL and credentials (mode 600)
gotg refresh                re-fetch the catalog
gotg list                   every game, marking what is installed here
gotg info <id>              one game's details
gotg download <id>          fetch a game, nothing else
gotg install <id>           fetch + build its environment + write a Steam launcher
gotg play <id>              fetch and build what is missing, then launch
gotg saves <command>        setup / status / push / pull, between machines
gotg controllers <command>  list what SDL sees; apply bindings
gotg sync                   rebuild the GC roots after a git pull
```

## Layout

| File | Responsibility |
|---|---|
| `bin/gotg` | argument dispatch only |
| `lib/common.sh` | paths, logging, id and path validation |
| `lib/config.sh` | credentials in a 0600 file, `gotg login` |
| `lib/api.sh` | File Browser login and raw-download URLs |
| `lib/manifest.sh` | the catalog, id resolution, `list` and `info` |
| `lib/download.sh` | staging, resume, checksums, unzip, progress UI |
| `lib/env.sh` | which environment, GC roots, building one |
| `lib/launcher.sh` | rendering the Steam launcher |
| `lib/cmd-play.sh` | `install`, `play`, `sync` |
| `lib/remote.sh` | the five blob operations, and which backend serves them |
| `lib/remote-filebrowser.sh` | those five against File Browser |
| `lib/saves.sh` | bundling, generations, and verifying what arrives |
| `lib/cmd-saves.sh` | `saves setup`, `status`, `push`, `pull` |
| `lib/pads.sh` | generating emulator bindings from what SDL reports |
| `lib/cmd-controllers.sh` | `controllers list`, `controllers apply` |
| `env/<platform>.nix` | the emulator, arguments and settings for a platform |
| `env/games/<platform>/<id>.nix` | what one game changes about that |

`env/` is nix, built by the flake, but it is also shipped in the package: the CLI
reads the *names* of those files to work out which flake attribute a game wants,
which is how `list`, `info` and any launch of something already built stay clear
of nix entirely.

## Things worth knowing

**Tokens are never cached.** File Browser's JWTs expire in hours, so every
command logs in again — one cheap request that removes a class of failures where
a stale token only shows up at launch time.

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
nix flake check     # or: GOTG_BIN=$(which gotg) bats client/tests/
```

74 tests run against `tests/mock_filebrowser.py`, a stand-in that speaks enough
of the real API — including `Range` requests — that resume is exercised against a
genuinely truncated transfer rather than a simulated one.
