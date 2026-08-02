# Saves backend: is the bespoke File Browser client overkill? Research report

*Generated: 2026-08-01 · Sources: 8 + local probes · Confidence: High on (a) and (e), Medium on (c)*

**Caveat:** `remote.sh` and `remote-filebrowser.sh` were uncommitted and being edited while this
was written. Findings may be premature against where that work is heading.

## Executive summary

The overkill question turned out to be the less important one. **Neither backend can work
against the current deployment**, for two independent reasons:

- **filebrowser** — the PVC is mounted `readOnly: true` at `/srv`, which is File Browser's
  `root`. Every `fb_blob_put` / `fb_blob_delete` fails at the filesystem layer.
- **rclone** — there is nothing for it to talk to. WebDAV is a *FileBrowser Quantum* (fork)
  feature, not upstream `filebrowser/filebrowser`. Probed the live server: `PROPFIND` on
  `/dav`, `/webdav`, `/api/dav` all return **404**.

So the saves feature needs new infrastructure regardless of which backend wins. That decision
should come before more of `remote-filebrowser.sh` gets written.

On the narrow question: yes, rclone would give away most of what the bash hand-rolls — and
notably **the exact atomicity trick** `fb_blob_put` implements by hand.

## 1. rclone cannot reach this File Browser

| Route | Verdict |
|---|---|
| Native rclone File Browser backend | Does not exist. rclone has ~70 backends; File Browser is not one. |
| File Browser WebDAV → `rclone webdav` | WebDAV is documented only for [FileBrowser Quantum](https://filebrowserquantum.com/en/docs/features/webdav/), the `gtsteffaniak` fork. Not upstream. |
| Live probe | ✅ `PROPFIND https://downloads.dcraw.net/{dav,webdav,api/dav}` → all `404`. |
| `rclone :http` backend | Documented as read-only — fine for `blob_get`, useless for `blob_put`. *(Read from docs, not tested.)* |

The `rclone` arm of the dispatch table in `remote.sh:30` is therefore not a fallback for the
same server — it implies a *different* server.

## 2. What rclone would give for free

The one that matters, quoted from [rclone's docs](https://rclone.org/docs/):

> Without the `--inplace` flag, rclone will first upload to a temporary file with an extension
> like a hash of the source file's fingerprint followed by `.partial`… When the upload is
> complete, rclone will rename the `.partial` file to the correct name.

That is precisely what `fb_blob_put` builds by hand: upload to `$key.part`, then `PATCH
?action=rename`. rclone does it by default, configurable via `--partial-suffix`, and the
[stated motivation](https://github.com/rclone/rclone/issues/3612) is the same one in the
comment at `remote-filebrowser.sh:36-37` — readers must never see a half-written file.

Also free: `rclone lsjson` (replaces the `jq '.items[]?'` listing), `rclone hashsum sha256`
(replaces `?checksum=sha256` parsing), retries, `--bwlimit`, and `crypt` if saves should be
encrypted at rest on a remote you don't fully trust.

**Cost:** `rclone-1.74.4` closure is **150.7 MiB**, against `curl` at 62.8 MiB and `jq` at
37.1 MiB — both already in the client's `makeBinPath`. Real, but not disqualifying for a
project that already ships `nix` in the wrapper.

## 3. Where writable saves could actually live

Ranked by how little new infrastructure they add to the existing cluster:

1. **A second PVC mounted writable under `root` in the same File Browser pod** — e.g.
   `/srv/saves`. Keeps the "same account, same credentials" property that
   `remote-filebrowser.sh:3-5` is explicitly designed around, and keeps the media volume
   read-only. Smallest change; the bespoke client survives unmodified.
2. **A second File Browser instance** with its own small writable PVC. Same code, new
   credentials — loses the design goal above.
3. **S3-compatible (Garage or SeaweedFS)** — rclone's best-supported target, but it's a new
   service to run and back up for a few MB of save bundles. *Inference, not sourced: this
   looks like poor value at this data size.*

Option 1 preserves the most and changes the least. It also means the "overkill" question
resolves as *keep the bespoke client* — not because it's better than rclone, but because it's
already written and the credential-reuse property is worth something.

## 4. Prior art on conflict resolution

Worth reading before hand-rolling generation numbers:

- **RetroArch Cloud Sync** detects a conflict as *"the server version differs from what was
  last synced AND the local version also differs from what was last synced"* — a three-way
  comparison against a last-synced marker. On conflict it changes nothing, logs it, and
  excludes the file from sync until resolved ([libretro docs](https://docs.libretro.com/guides/retroarch-cloud-sync/)).
- **Syncthing** renames the loser to `.sync-conflict-<date>-<time>-<modifiedBy>.<ext>` rather
  than discarding it ([Syncthing docs](https://docs.syncthing.net/users/syncing.html)).

Both refuse to silently discard a save. If the generation-number scheme in the key format
(`env-n64/gen/000042-3f9a1c2b4d5e.tar.zst`) resolves ties by picking a winner, that's a
weaker guarantee than either — and losing a save is the one failure users won't forgive.

## Key takeaways

1. **Blocked both ways.** Read-only mount kills the filebrowser backend; no WebDAV endpoint
   kills the rclone backend. Decide the infrastructure before writing more client code.
2. **Cheapest fix: a second writable PVC under `root`** in the existing pod. Keeps the media
   volume read-only and the single-credential design intact.
3. **rclone's default `.partial`-then-rename is the same trick `fb_blob_put` hand-rolls** —
   confirmation that the approach is right, whether or not rclone is adopted.
4. **Keep the bespoke client if option 1 is chosen.** 110 lines already written, and rclone
   costs 150 MiB of closure to replace code that works.
5. **Don't hand-roll conflict resolution.** RetroArch's three-way test and Syncthing's
   keep-both rename are the established answers.

## Sources

1. [rclone docs — `--partial-suffix`, `--inplace`](https://rclone.org/docs/) — default atomic upload behaviour
2. [rclone/rclone#3612](https://github.com/rclone/rclone/issues/3612) — motivation for `.partial` transfers
3. [rclone/rclone#5086](https://github.com/rclone/rclone/issues/5086) — atomic download counterpart
4. [rclone WebDAV backend](https://rclone.org/webdav/) — supported WebDAV servers
5. [FileBrowser Quantum — WebDAV](https://filebrowserquantum.com/en/docs/features/webdav/) — WebDAV is a fork feature
6. [FileBrowser Quantum — rclone guide](https://filebrowserquantum.com/en/docs/user-guides/other/rclone/) — requires WebDAV enabled
7. [RetroArch Cloud Sync](https://docs.libretro.com/guides/retroarch-cloud-sync/) — three-way conflict detection
8. [Syncthing — Understanding Synchronization](https://docs.syncthing.net/users/syncing.html) — `.sync-conflict-*` naming
9. Local probes — `PROPFIND` against the live server (404 ×3); `nix path-info -Sh` for closure sizes

## Methodology

Web search for the rclone/File Browser integration question, then direct probes against the
running server to settle whether WebDAV exists rather than inferring it from version numbers.
Closure sizes measured with `nix path-info -Sh` against the exact nixpkgs revision this
project uses.

**Gaps:** rclone's `:http` backend being read-only is taken from documentation, not tested.
Option 3 (S3-compatible) was not costed properly — it's ranked on judgement, not measurement.
