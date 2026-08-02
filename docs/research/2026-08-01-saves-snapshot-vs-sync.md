# Saves: hand-rolled snapshot store vs restic/kopia vs sync tools

*Generated: 2026-08-01 · Sources: 9 · Confidence: High on (a)/(c), Medium on (b)*

**Caveat:** the bundling, pruning, conflict and restore logic are not written yet. This is
about the design implied by the key schema in `common.sh:32`, not about code that exists.

## Executive summary

The key schema — `env-n64/gen/000042-3f9a1c2b4d5e.tar.zst` plus `latest.json` — is a
**content-addressed snapshot repository**: numbered generations, content hash, compressed
bundle, pointer to head. restic, borg and kopia are all exactly that, and all three subsume
the tar.zst bundling, generation numbering, dedup, include/exclude globs, encryption and
`--keep-last N` retention that would otherwise have to be written.

But there's a deeper issue than "should I use a library": **the design is a backup store,
and the stated use case is sync.** "Play on the Deck, continue on the desktop" is a
bidirectional problem, and no amount of generation numbering solves it — the hard part is
deciding what happens when both ends moved. Worth settling which one this is before writing
the pruning logic.

Closest prior art: **Ludusavi** (in nixpkgs at 0.31.0) does game-save backup, knows where
saves live from a community manifest, pushes to any rclone remote, and refuses to clobber on
conflict. It is not a drop-in — its unit is a *game*, GOTG's is an *environment* — but it is
the reference design for every question on this list.

## 1. restic / borg / kopia subsume the mechanics

All three are content-addressed, deduplicating, compressed snapshot stores with include/exclude
globs, encryption at rest, and retention policies. Whatever bundling and pruning code would go
into `gotg saves`, they already have — battle-tested and with restore semantics worked out.

The reason to hesitate is **backend fit**. These need a repository, not a blob store: locks,
an index, pack files. The 5-verb `blob_put/get/stat/list/delete` interface in `remote.sh` is
not enough to host one. Each needs its own supported backend — restic speaks S3, SFTP, REST
server and `rclone:` remotes; kopia additionally speaks WebDAV natively. Which means adopting
one *also* forces the infrastructure decision from the previous report.

For a few MB of save data, a repository's locks/indexes/packfiles are real but modest
overhead. The bigger cost is operational: repo init, password management, prune scheduling.
*(That weighting is judgement, not a sourced measurement.)*

## 2. Backup ≠ sync — this is the actual decision

| | What it gives | What it doesn't |
|---|---|---|
| restic/borg/kopia | Durability, history, "undo my bad save" | Nothing about which machine wins |
| `rclone bisync` | Bidirectional, conflict suffixes | See below |
| Syncthing | Continuous bidirectional, `.sync-conflict-*` rename | A daemon on every machine |

**`rclone bisync`**: redesigned in v1.66 around a snapshot model, which
[reduced the risk from changes made mid-sync](https://rclone.org/bisync/) — changes missed in
one run are picked up by the next rather than becoming critical errors. It's integration-tested
against all backends. But the docs still frame it as an advanced command where reading the
Limitations section matters to avoid data loss. **Not something to put saves on without
durable backup underneath.**

If the answer is "both" — and for saves it usually is — the layering is: a snapshot store for
durability, plus an explicit conflict check on the sync path. Not one mechanism pretending to
do both jobs.

## 3. Ludusavi is the prior art worth reading

[Ludusavi](https://github.com/mtkennerly/ludusavi) (Rust, nixpkgs `ludusavi-0.31.0`):

- **Knows where saves live.** The [ludusavi-manifest](https://github.com/mtkennerly/ludusavi-manifest)
  is a YAML database of save locations compiled from PCGamingWiki, covering thousands of titles.
- **Custom games** let you define your own paths with globs and placeholders, overriding a
  manifest entry when the name matches — the same job `saves.json` does, in an established format.
- **Cloud via rclone**, any remote, with custom rclone args (`--transfers=1` etc.).
- **Conflict handling is the safe kind**: it checks sync state before backing up, and if local
  and cloud had already diverged it *"will warn you about the conflict and leave the cloud data
  alone"* — the same refuse-to-guess posture as RetroArch Cloud Sync.
- **Scriptable**: `ludusavi backup --preview`, `--path`, `manifest`, and commands that read a
  game list from stdin.

**Why it isn't a drop-in:** Ludusavi's unit is a game; GOTG's is an environment (`env-snes` is
shared by every SNES title). Its cloud configuration is documented through the GUI, with no CLI
path shown for the cloud half. And its backup target is a directory tree, not a 5-verb blob
store. Adopting it means reshaping the saves model around games — a real design change, not a
dependency swap.

What is worth stealing regardless: the conflict posture, the manifest-as-data idea, and the
observation that hand-maintaining per-emulator globs is a job someone already does at scale.

## Key takeaways

1. **Decide backup vs sync before writing the pruning logic.** The current key schema answers
   the backup question and leaves the sync question untouched; that gap won't close by itself.
2. **If backup: don't hand-roll the snapshot store.** restic/borg/kopia already do generations,
   dedup, globs, compression, encryption and retention. They need a real repository backend, so
   this decision is coupled to the infrastructure one.
3. **If sync: `rclone bisync` is much better than it was, but still carries a documented "read
   the limitations" warning.** Syncthing is the sturdier option for a handful of small files,
   at the cost of a daemon everywhere.
4. **Read Ludusavi before finalising `saves.json`.** Not to adopt wholesale — the game-vs-
   environment mismatch is real — but its conflict handling and manifest format are the
   answers to two problems currently on the roadmap.
5. **Never silently pick a winner.** Ludusavi, RetroArch and Syncthing all independently
   converged on refuse-or-keep-both. That agreement is worth more than any one of them.

## Sources

1. [Ludusavi cloud backup docs](https://github.com/mtkennerly/ludusavi/blob/master/docs/help/cloud-backup.md) — rclone remotes, conflict posture
2. [Ludusavi CLI docs](https://github.com/mtkennerly/ludusavi/blob/master/docs/cli.md) — scriptable commands, stdin game lists
3. [ludusavi-manifest](https://github.com/mtkennerly/ludusavi-manifest) — YAML save-location database from PCGamingWiki
4. [Ludusavi README](https://github.com/mtkennerly/ludusavi/blob/master/README.md) — custom games, globs, placeholders
5. [rclone bisync](https://rclone.org/bisync/) — v1.66 snapshot redesign, limitations framing
6. [rclone bisync command reference](https://rclone.org/commands/rclone_bisync/) — `--compare`, conflict suffixes
7. [Decky Ludusavi](https://decky.net/en/blogs/news-en/saving-games-decky-ludusavi) — emulator coverage where Steam Cloud stops
8. [RetroArch Cloud Sync](https://docs.libretro.com/guides/retroarch-cloud-sync/) — three-way conflict detection *(carried from the previous report)*
9. Local checks — `nix eval` confirmed `restic-0.19.1`, `borgbackup-1.4.4`, `kopia-0.23.1`, `syncthing-2.1.2`, `ludusavi-0.31.0` all in nixpkgs

## Methodology

Web search across the backup-tool and save-sync literature, then direct reads of Ludusavi's
cloud-backup and CLI docs. nixpkgs availability confirmed locally by `nix eval`.

**Gaps:** closure-size measurements failed (`nix path-info -Sh` returned empty for these
attributes) — the sizing claim in (e) is unanswered. Whether Ludusavi's cloud sync can be
driven entirely from the CLI is unclear; the docs only describe the GUI path, and that
determines whether it could be used headlessly under Steam.
