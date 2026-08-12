# gotg-importer

Organizes completed game torrents into the canonical `/Games/<platform>/<entry>` tree
using renamed hardlinks, so the originals keep seeding at zero extra space.

The pipeline is split so that everything except two thin I/O layers is pure and
testable off-cluster:

```
scan (I/O)  ->  classify  ->  plan  ->  execute (I/O)
```

`slugify.py` and `plan.py` are the validated reference implementation, ported
verbatim from `Kubernetes/games/` so the two copies stay diffable. `slugify.py` is
byte-identical; `plan.py` differs only in its import block and one added handler.

## Runtime interface

The Kubernetes CronJob depends on these exact knobs — treat them as a public API.

| Env var | Meaning |
|---|---|
| `QBIT_URL` | e.g. `http://qbittorrent.default.svc.cluster.local:8080` |
| `QBIT_USER`, `QBIT_PASS` | WebUI credentials (from a Secret) |
| `QBIT_CATEGORY` | work-queue category (default `games`) |
| `GAMES_ROOT` | absolute path of the organized tree, e.g. `/data/Games` |
| `SOURCE_ROOT` | where torrents live, e.g. `/data/Torrents` |
| `PATH_PREFIX` | prefix written into manifest paths, e.g. `/Games` |
| `STATE_DIR` | writable dir for the idempotency state file |

| Flag | Meaning |
|---|---|
| `--once` | process one pass and exit (what the CronJob runs) |
| `--dry-run` | print the plan, write nothing |
| `--bootstrap DIR [DIR...]` | process explicit paths instead of polling qBittorrent |

Exit `0` on success including "nothing to do"; non-zero only on hard failure (bad
config, unwritable volume, qBittorrent unreachable).

> `--bootstrap` accepts several paths **or** one comma-separated list. A path that
> exists as given is never split, because No-Intro directory names contain commas
> (`Legend of Zelda, The - Twilight Princess HD (USA) (En,Fr,Es)`).

## Classification

Priority order, first match wins. Anything unidentified becomes `manual` with a
reason — the importer never guesses a platform, because a wrong guess files a game
under an id the client then hands to the wrong emulator.

| Handler | Detected by | Action |
|---|---|---|
| `wiiu_decrypted` | `code/` `content/` `meta/` with a `code/*.rpx` | zip to `<id>.zip` |
| `wiiu_nus` | TMD + `*.h3` + 8-hex content, no decrypted output | `manual` (decrypt deferred) |
| `scene_archive` | `*.rar` plus `*.r00`/`*.sfv` | extract the inner ROM |
| `no_intro_set` | DAT-style dir name, or many ROMs of one platform | 1G1R curate, hardlink |
| `single_file` | one ROM by extension | hardlink (no curation) |
| `excluded` | listed in `rules.yaml` | skip |

`rules.yaml` (a ConfigMap in-cluster) carries the DAT-directory and extension maps,
so adding a platform is a config edit rather than a release.

## Operational notes

Learned from the first real bootstrap over the live library:

- **Memory.** `unrar` allocates the archive's whole RAR5 dictionary, which is
  hundreds of megabytes on a large scene release. 512Mi is not enough; the
  CronJob asks for 2Gi.
- **Source and destination must share a filesystem.** Imports are hardlinks, so
  `GAMES_ROOT` and `SOURCE_ROOT` have to be two paths on one mount — which is why
  the CronJob mounts `jellyfin-data` once at `/data` rather than binding each
  subdirectory separately. Getting this wrong fails loudly with EXDEV.
- **Interrupted runs are safe.** The catalog is published after each source and
  staging directories are swept at the start of the next run, so an OOM kill or
  an evicted pod costs only the work in flight.
- **Checksumming dominates the runtime.** Hashing is what makes a first import
  take minutes; `--no-checksum` skips it at the cost of client-side verification.

## Development

```bash
nix build .#gotg-importer     # runs the test suite as part of the build
nix flake check               # tests + ruff

# See what a real payload would become, without writing anything:
GAMES_ROOT=/tmp/games SOURCE_ROOT=~/Games STATE_DIR=/tmp/state \
  ./result/bin/gotg-importer --dry-run --bootstrap ~/Games/n64
```
