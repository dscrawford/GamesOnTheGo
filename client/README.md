# gotg — the GamesOnTheGo client

Downloads a game from the server the first time you launch it, then runs it in
the right emulator. Meant to be driven from Steam.

```
gotg login                  save the server URL and credentials (mode 600)
gotg refresh                re-fetch the catalog
gotg list                   every game, marking what is installed here
gotg info <id>              one game's details
gotg download <id>          fetch a game, nothing else
gotg install <id>           fetch + build its emulator + write a Steam launcher
gotg play <id>              fetch if needed, then launch
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
| `lib/emulator.sh` | which emulator, GC roots, launch arguments |
| `lib/launcher.sh` | rendering the Steam launcher |
| `lib/cmd-play.sh` | `install`, `play`, `sync` |

## Things worth knowing

**Tokens are never cached.** File Browser's JWTs expire in hours, so every
command logs in again — one cheap request that removes a class of failures where
a stale token only shows up at launch time.

**Downloads stage outside the platform directory** and are moved in only once
complete and verified. A half-downloaded file can never look like an installed
game to a launcher.

**`play` never builds anything.** Steam's launch environment cannot evaluate nix,
so if an emulator is missing it says to run `gotg install` from a terminal rather
than failing in a way Steam renders as the game closing instantly.

## Tests

```bash
nix flake check     # or: GOTG_BIN=$(which gotg) bats client/tests/
```

27 tests run against `tests/mock_filebrowser.py`, a stand-in that speaks enough
of the real API — including `Range` requests — that resume is exercised against a
genuinely truncated transfer rather than a simulated one.
