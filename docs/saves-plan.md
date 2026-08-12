# Implementation plan: cloud save sync

**Status:** designed, not started. No code has been written for any of this.
**Written:** 2026-08-01. **Audience:** whoever implements it next.

This document is a handoff. It assumes you know nothing about the feature and have not seen the
conversation it came out of. Everything marked *verified* was checked against source or against
live state on the development machine on the date above; everything else is flagged.

Read `README.md` and `client/README.md` first for the entry-id contract and the CLI shape.

---

## 1. Why

GOTG is one-directional: it downloads ROMs from a File Browser server and launches them in
nix-built emulator environments, and nothing is ever written back. Game saves therefore live
only on whichever machine ran the game. Playing on the Steam Deck and then on the desktop
produces two divergent saves with no way to reconcile them.

The goal is an **external endpoint for saves** — the existing self-hosted File Browser, or
Google Drive, or anything else — behind a backend abstraction so the choice isn't baked in.

---

## 2. Two findings that shape everything

### 2.1 `isolate = true` does not capture ares saves — verified

Live on the development machine:

```
~/Games/snes/world.super_metroid.ram    8192 bytes
~/Games/n64/usa.mario_golf.ram         32768 bytes
```

`world.super_metroid` **already sets `isolate = true`**
(`client/env/games/snes/world.super_metroid.nix`) and its save still landed next to the ROM.
Its isolated `{state}` directory contains only `data/ares/settings.bml`, `data/ares/Systems/`
and `data/hiro/gtk3.bml` — no saves at all.

The cause is in ares' `desktop-ui/emulator/emulator.cpp:27`:

```cpp
auto Emulator::locate(const string& location, const string& suffix, const string& path, maybe<string> system) -> string {
  if(!system) system = root->name();
  //game path
  if(!path) return {Location::notsuffix(location), suffix};
  //path override
  string pathname = {path, *system, "/"};
  directory::create(pathname);
  return {pathname, Location::prefix(location), suffix};
}
```

`path` is `settings.paths.saves`, empty by default (confirmed in
`~/.local/share/ares/settings.bml`: `Paths` has a bare `Saves` key with no value). Empty falls
through to "ROM path minus extension plus suffix". XDG is never consulted.

**Consequence:** predictable save paths require a **per-emulator redirect flag**. Isolation is
still wanted — it stops ten environments sharing one `~/.local/share/ares` — but it is not
sufficient on its own.

Note `pathname = {path, *system, "/"}` is plain concatenation, so **the ares saves path must end
in `/`** or you get `…/gotg/env/env-snesSuper Famicom/`. This is ares issue #1786.

| Env | Redirect flag | Evidence |
|---|---|---|
| ares (nes, snes, n64, gb, gbc, gba) | `--setting Paths/Saves={state}/saves/` | `desktop-ui/desktop-ui.cpp:122-151` parses `--setting name=value`; `settings/settings.cpp:111` binds `"Paths/Saves"`. `desktop-ui.cpp:252-259` restores CLI overrides before `settings.save()`, so this never pollutes the user's `settings.bml`. |
| dolphin (gamecube, wii) | `-u {state}/dolphin/` | `Source/Core/UICommon/CommandLineParse.cpp:85` — `parser->add_option("-u", "--user")` |
| cemu (wiiu) | `-m {state}/mlc` | `cemu --help` on the dev machine: `-m [ --mlc ] arg   Custom mlc folder location` |
| ryubing (switch) | `--root-data-dir {state}/ryujinx` | **UNVERIFIED** — the fork left GitHub and its argument parser could not be read. Documented for upstream Ryujinx only. Verify by launching. |
| harkinian ports | already `isolate = true`, `$XDG_DATA_HOME/<appName>` | Live: `~/.local/share/2ship/saves/file1.json` (302 KB), `file1backup.json`, `2ship2harkinian.json`; bulk `mm.o2r` (36 MB) |

ares save suffixes, from `mia/medium/*.cpp`: `.ram`, `.eeprom`, `.flash`, `.rtc`, `.iram`,
`.bsx`, `.dram`. Save states are `.bs1`–`.bs9`, `.bsu`, `.blu` and land in the **same** saves
path (`desktop-ui/program/states.cpp:5`).

Real layouts confirmed on the dev machine: Cemu `mlc01/usr/save/{system,00050010,00050000}/…`
(284 KB) with bulk in `graphicPacks` (14 MB) and `shaderCache`; Dolphin `GC/{USA,EUR,JAP}/`,
`GC/SRAM.raw`, `Wii/title/`, `Wii/shared2/`, `StateSaves/`.

### 2.2 File Browser dropped `?auth=` in v2.45.0 — verified

Bisected across tags in `http/auth.go`. v2.44.0 had:

```go
auth := r.URL.Query().Get("auth")
if auth != "" && strings.Count(auth, ".") == 2 {
    return auth, nil
}
```

In v2.45.0 and everything after (checked through v2.63.23) that block is **gone**. Only the
`X-Auth` header, or an `auth` cookie on GET, is accepted.

`client/lib/api.sh:40-50` builds every download URL as `?auth=$token`. **The production server
is 2.32.0** (checked 2026-08-01), so this works today — but it breaks on the next major upgrade.
This is pre-existing and not caused by the saves feature. Do not add a second consumer of a dead
API: use `X-Auth` for saves and migrate the existing calls in the same work.

### 2.3 `<remote_root>/.gotg/saves/` is safe from the importer — verified

`src/gotg/indexer/execute.py:81-99` (`cleanup_staging`) only removes directories whose
name starts with `STAGING_PREFIXES = (".gotg-extract-", ".gotg-zip-")` (line 49). Nothing else
under `<games_root>` is ever deleted, and `manifest.py` merges into `.gotg/manifest.json`
without touching siblings. Saves can live inside the existing library root — **no new File
Browser account, scope or share is needed.**

---

## 3. Decisions already made

These were settled during design. All four are cheap to reverse if you disagree, but change them
deliberately rather than drifting.

| Decision | Choice | Rationale |
|---|---|---|
| Which backend first | **File Browser**, abstraction in place from day one; rclone in Phase 3 | Zero new infra, reuses existing creds, server already running. The abstraction makes "or even Google Drive" a later commit rather than a rewrite. |
| Auto-sync on play | **Off by default**; `cmd_play`'s `exec` untouched in Phase 1 | The launch path is the most-tested code in the repo. Auto-sync requires dropping the `exec` (§8), which deserves its own phase. |
| Save states | **Excluded by default**, per-env `saveStates = false` | ares `.bs1`–`.bs9` are 10 MB+ each and are not portable across ares versions — a pulled state may not load. Memory saves always sync. |
| Platform scope, Phase 1 | **ares + harkinian only** | What is actually played on the dev machine. Dolphin/Cemu/Ryujinx need their flags empirically verified first (§2.1). |

---

## 4. Command surface

`gotg sync` is taken (it rebuilds nix GC roots). Use a `saves` noun with sub-dispatch — one case
arm in `client/bin/gotg`, all verbs inside `client/lib/cmd-saves.sh`.

```
gotg saves setup                       choose and test a backend
gotg saves status [<id>|--all]         local vs remote generation; writes nothing
gotg saves push   [<id>|--all] [--force]
gotg saves pull   [<id>|--all] [--force]
gotg saves adopt  [<id>|--all] [--yes] copy pre-isolation saves into the new layout
gotg saves list                        every env with saves on the remote      (Phase 2)
gotg saves history <id>                generations kept remotely, newest first (Phase 2)
gotg saves restore <id> <generation>                                           (Phase 2)
```

Plus a config key `saves_auto` = `off` | `pull` | `both`, and `gotg play --no-sync` (Phase 4).

**The unit of sync is the environment, not the game.** `env-snes` is shared by every SNES title
and its state directory holds all of their `.ram` files; splitting them requires a second source
of truth about which file belongs to which game, which does not exist. So a game id is accepted
and resolved through `manifest_find` → `env_attr` exactly as `play` does, and then the CLI states
plainly what it is doing:

```
pushing env-n64 — 12 files, 3 games, 84 KB
```

`--all` walks every root under `$GOTG_ROOTS_DIR`.

`status` must work with no network (report "remote unreachable, local generation N") and with no
built environment ("not built here"). `pull` calls `env_ensure` like `play` does, because it
needs `saves.json` to know where to put things.

---

## 5. Remote layout

`saves_root` defaults to `<remote_root>/.gotg/saves`, configurable, run through
`validate_remote_path`. Safe per §2.3.

```
<saves_root>/env-n64/latest.json
<saves_root>/env-n64/gen/000042-3f9a1c2b4d5e.tar.zst
<saves_root>/env-n64/gen/000041-8b7c0d1e2f3a.tar.zst
<saves_root>/env-snes/…
<saves_root>/env-n64-usa_legend_of_zelda_majoras_mask/…
```

- Keyed by **flake attr** — already the GC-root name, already regex-validated by `env_attr`, and
  unambiguous between `env-snes` and `env-snes-world_super_metroid`. One flat namespace.
- Generation zero-padded to 6 digits so **lexical order equals numeric order**. Both File
  Browser's `items[].name` and `rclone lsf` return names; you sort with `sort`, not `jq`
  arithmetic.
- The content hash is in the filename, so a bundle is identifiable from a listing alone even if
  `latest.json` is lost.

`latest.json` is the only thing read on the fast path — one GET decides whether anything needs
doing:

```json
{ "version": 1, "attr": "env-n64", "generation": 42,
  "parent": "sha256:8b7c0d1e…", "hash": "sha256:3f9a1c2b…",
  "bundle": "gen/000042-3f9a1c2b4d5e.tar.zst",
  "size": 18432, "files": 12, "device": "a3f91c02",
  "written_at": "2026-08-01T14:29:00Z" }
```

Local journal at `$GOTG_STATE_DIR/saves/<attr>.json`:

```json
{ "base_generation": 42, "base_hash": "sha256:…", "pushed_hash": "sha256:…",
  "pulled_at": "…", "adopted": true }
```

`base_*` is the remote generation this machine's tree descends from. That is the entire conflict
model in two fields.

---

## 6. Backend abstraction

`client/lib/remote.sh` — five functions, dispatched on `config_get saves_backend`. Everything
above this line speaks in *keys* (`env-n64/gen/000042-….tar.zst`), never URLs or remote syntax.

```bash
blob_put    <local-file> <key>   # upload <key>.part, then rename — atomic at the remote
blob_get    <key> <local-file>   # to <local>.part, then mv
blob_stat   <key>                # prints "<bytes> <sha256-or-empty>"; returns 1 if absent
blob_list   <prefix>             # one name per line, sorted, relative to the prefix
blob_delete <key>                # 0 if it was already gone
```

Validate every key before it becomes a URL or an argv entry:

```
^env-[a-z0-9][a-z0-9_-]*(/gen/[0-9]{6}-[0-9a-f]{12}\.tar\.zst|/latest\.json)$
```

### 6.1 File Browser — `client/lib/remote-filebrowser.sh`

Confirmed from filebrowser source `http/resource.go` (v2.63.23), `writeFile` at line 346:

| Op | Call | Notes |
|---|---|---|
| put | `POST /api/resources/<path>?override=true` | **raw body**, not multipart. **Success is 200, not 201** (`errToStatus` → `http/utils.go:77-80`, then `handle()` at `http/data.go:87`). Without `override=true` an existing file gives **409**. `writeFile` does `MkdirAll` on the parent, so **no directory-creation round trip is needed**. |
| mkdir | `POST /api/resources/<path>/` | trailing slash → `MkdirAll`. Not needed given the above. |
| rename | `PATCH /api/resources/<src>?action=rename&destination=<url-encoded>&override=true` | the atomic publish |
| delete | `DELETE /api/resources/<path>` | → **204** |
| list | `GET /api/resources/<dir>` | `.items[] \| select(.isDir\|not) \| .name` |
| stat | `GET /api/resources/<file>?checksum=sha256` | → `.checksums.sha256` (`http/resource.go:69-82`). Verifies a remote bundle **without downloading it.** |

`/api/tus` resumable upload exists. **Do not implement it** — bundles are KB–MB.

Permissions needed: `Create` for new files, `Modify` to override, `Delete` for retention.

Token goes in `X-Auth` via `curl --config -` on **stdin**, so the JWT never enters `argv`
(`/proc/<pid>/cmdline` is world-readable by default on Linux):

```bash
printf 'header = "X-Auth: %s"\n' "$token" | curl -sS --config - \
  --data-binary "@$file" -w '%{http_code}' \
  -X POST "$GOTG_SERVER/api/resources$(url_encode_path "$path")?override=true"
```

Add an `api_curl` helper to `client/lib/api.sh` doing exactly this, and move `api_fetch` and the
download URL off `?auth=` onto it (§2.2). Keep `api_raw_url` for the URL shape.

### 6.2 rclone — `client/lib/remote-rclone.sh` (Phase 3)

Reuse rather than hand-roll Google Drive OAuth. One dependency covers Drive, S3, WebDAV,
Dropbox, SFTP, and a `crypt` layer.

- `rclone_bin() { printf '%s' "${GOTG_RCLONE:-rclone}"; }` — same seam shape as `nix_bin()` in
  `client/lib/env.sh:24`.
- Remote from `config_get saves_rclone_remote`, e.g. `gdrive:GOTG/saves`, validated
  `^[A-Za-z0-9_.-]+:[A-Za-z0-9_./-]*$`.
- put: `rclone copyto <file> <remote>/<key>.part` then `rclone moveto … <remote>/<key>`
  (server-side on Drive/S3/WebDAV). get: `copyto`. stat: `lsjson --stat`. list:
  `lsf --files-only`. delete: `deletefile`.
- Flags: `--config "$conf" --retries 3 --low-level-retries 5 --timeout 60s --stats 0
  --log-level ERROR`.
- **Never `rclone sync` or `rclone move` on a directory** — those delete. Single-key operations
  only.
- **Do not shell out to `rclone config`.** It opens a browser for OAuth; under Steam that hangs
  forever — the same reasoning `client/lib/config.sh` already gives for not using a keyring.
  `gotg saves setup` prints the two commands for the user to run, then verifies with
  `rclone lsd`.
- Ignore `lsjson`'s `Hashes` — Drive gives sha256, Dropbox its own, WebDAV often none. Our hash
  lives in the filename and in `latest.json`.

---

## 7. Bundle format

**One zstd-compressed tar per environment**, members relative to `{state}`.

Why not per-file:

- **Consistency.** A save set is only meaningful as a set — Dolphin's `Wii/title/**` plus
  `fst.bin`; ares' `.ram` plus its state. Per-file upload lets two devices interleave halves of
  two different saves; a tar is all-or-nothing.
- **Round trips.** Cemu's `mlc01/usr/save` on the dev machine is dozens of small files. Per-file
  means a listing plus one request each; on a Deck on a hotspot that is one second versus thirty.
- **Content addressing.** One sha256 over the tar *is* the generation identity and the conflict
  detector. Per-file needs a side manifest to recover the same property.
- Saves are KB–MB, so there is no partial-transfer argument on the other side.

Build deterministically, so an unchanged save set hashes identically and a no-op push uploads
nothing:

```bash
tar --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner \
    --format=pax --pax-option=exthdr.name=%d/PaxHeaders/%f,delete=atime,delete=ctime \
    --zstd -C "$state" -cf "$tmp" --exclude=… <declared save globs>
```

Requires GNU tar ≥ 1.28 for `--sort` and ≥ 1.31 for `--zstd`; nixpkgs ships 1.35. Zeroing mtimes
is safe — none of these emulators order saves by mtime — and it is what makes "did anything
change?" a hash comparison instead of a diff.

**Hard size cap** `GOTG_SAVES_MAX_BYTES`, default 64 MB: refuse to bundle above it and `die`
naming the largest members. This is the guard that catches a glob accidentally matching a
Harkinian `.o2r` (36 MB on the dev machine, derived from the ROM, must never be uploaded) —
cheaper and more reliable than trusting the exclude list to be complete. Apply the same cap on
download (`curl --max-filesize`, and reject a `latest.json` whose declared `size` exceeds it).

---

## 8. Where the save paths come from

**Emit `$out/share/gotg/saves.json` from the env derivation** and read it from the GC root — a
file read, no nix evaluation, obeying the same rule `env_attr` already does (see the header
comment in `client/lib/env.sh`).

```json
{ "version": 1, "name": "env-n64",
  "saves": ["saves/**"],
  "excludes": [],
  "legacy": ["$GAMES/n64/*.ram", "$GAMES/n64/*.eeprom", "$XDG_DATA/ares"] }
```

**Why not `client/data/overrides.json`:**

- The save glob and the emulator flag that *creates* that path are the same decision. In separate
  files they will drift, and the failure is silent — someone changes `-u {state}/dolphin` to
  `{state}/user` and the backup quietly contains nothing. In `lib.nix` a single `saves` attribute
  can generate both, so they cannot disagree.
- `overrides.json` exists for what the CLI must know *before* anything is built; both its fields
  (`target`, `unzip`) are needed by `gotg list` offline. Save locations are not in that category.
- `overrides.json` is keyed by game; state dirs are keyed by env attr. A per-game key is the
  wrong shape for the shared `env-snes`.

Accepted costs: `saves.json` is unreadable until the env is built (fine — `pull` builds it,
`status` reports "not built here"), and editing a platform's `saves` needs `gotg sync` to take
effect (already true of everything in `client/env/`).

**Implementation.** `writeTextFile` has no `postInstall` hook, so `overrideAttrs` will not work.
Wrap instead:

```nix
app = pkgs.writeShellApplication { name = "gotg-play"; … };
in
pkgs.runCommand "gotg-env-${name}" { } ''
  mkdir -p $out/bin $out/share/gotg
  ln -s ${app}/bin/gotg-play $out/bin/gotg-play
  cp ${pkgs.writeText "saves.json" (builtins.toJSON manifest)} $out/share/gotg/saves.json
''
```

`env_is_built` tests `[[ -x … ]]`, which follows symlinks, so nothing downstream changes.

**Also move `{state}` out of the generated script.** `client/env/lib.nix:80` computes it in bash,
and the CLI now needs the same path. Add to `client/lib/env.sh`:

```bash
env_state_dir() { printf '%s/%s' "${GOTG_ENV_STATE_DIR:-$GOTG_STATE_DIR/env}" "$1"; }
```

and have `cmd_play` export `GOTG_ENV_STATE`. By default `$GOTG_STATE_DIR/env` is byte-identical
to today's `${XDG_STATE_HOME:-$HOME/.local/state}/gotg/env`, so existing state carries over
untouched — but it now follows `GOTG_STATE_DIR`, which every test already redirects, and the CLI
and the derivation stop computing the same path twice. Keep `lib.nix`'s fallback so `gotg-play`
still works run straight out of the store.

---

## 9. Conflict model

The realistic failure is: played on the Deck, then on the desktop without pulling.

**Push:**

1. Bundle locally, hash it. If `hash == journal.pushed_hash` → nothing to do, exit 0. This makes
   a launch on an already-synced machine free.
2. Fetch remote `latest.json`.
3. `remote.generation == journal.base_generation` → **fast-forward.** Upload
   `gen/<N+1>-<hash12>.tar.zst`, then publish `latest.json` with `parent = journal.base_hash`.
   Update the journal.
4. `remote.generation > journal.base_generation` → **divergence. Upload nothing.** Non-zero exit
   with a multi-line `die` naming all three exits, in the house style:

   ```
   error: env-n64 has moved on since this machine last synced.
        remote  generation 43  from device 7c1a  84 KB  2026-08-01T09:14Z
        local   generation 42  this device      91 KB  changed since the pull
      Take the remote (your local tree is archived first):  gotg saves pull usa.zelda
      Take yours (the remote generation is kept in history): gotg saves push --force usa.zelda
      Look before choosing:                                  gotg saves status usa.zelda
   ```

5. `--force` uploads as `remote.generation + 1`. **The losing generation is not deleted** — it
   stays in `gen/` and `gotg saves history` lists it.

**Pull:** always snapshot the current local tree to
`$GOTG_STATE_DIR/saves/local/<attr>/<utc>-<hash12>.tar.zst` (keep N) *before* extracting.

Deliberate choices, do not quietly reverse them:

- **mtime decides nothing.** Steam Decks suspend and their clocks drift; an mtime rule silently
  picks the wrong side. Times and device ids are printed for the human in `status` and are never
  inputs to the resolution.
- **Device id** is 8 random hex written once to `$GOTG_CONFIG_DIR/device`, plus an optional human
  `device_name`. Not the hostname — hostnames repeat, and "steamdeck" is not unique. Nothing
  identifying.
- **No merge.** Binary save files cannot be merged, and pretending otherwise is how data gets
  lost. The property this design delivers is: *no push, forced or not, ever removes a bundle that
  existed.*
- **The publish race** (two devices pushing simultaneously) can lose a `latest.json` pointer —
  neither File Browser nor rclone offers compare-and-swap. It cannot lose a *save*: the bundle is
  durable in `gen/` before `latest.json` is written. Mitigation: after publishing, re-read
  `latest.json`; if it is not ours, `warn` with the recovery command
  (`gotg saves restore <id> <gen>`).
- **Retention** (`saves_keep`, default 10, Phase 2) is a separate explicit path. It only deletes
  generations older than the newest N, never the one `latest.json` points at, and re-validates
  every name against the strict regex before it becomes an argument.

---

## 10. Auto-sync and dropping the `exec` (Phase 4)

`client/lib/cmd-play.sh:57` ends with `exec "$(env_bin "$attr")" "$target" "$@"`. Pushing after
play needs the shell to survive the emulator.

What actually changes:

- **Steam process tracking: no impact.** The generated `play-<id>.sh` is *already* a parent that
  `exec`s into `gotg`, and Steam tracks the process tree/cgroup, not a single pid. One more bash
  frame is neutral.
- **Signals: needs care.** Steam's Stop sends SIGTERM then SIGKILL after a grace period. A bash
  parent that merely `wait`s will handle SIGTERM itself and orphan the child unless signals are
  forwarded.
- **SIGKILL, OOM, power loss: no trap ever runs.** `trap … EXIT` does not fire on SIGKILL. Not
  fixable — design around it rather than defending against it.

**Keep the push, but never let correctness depend on it.**

Gate on `saves_auto`. When `off` (the Phase 1 default), `cmd_play` keeps the `exec` verbatim, so
the tested launch path is untouched for anyone who has not opted in. When on:

```bash
trap 'kill -TERM "$child" 2>/dev/null' TERM INT
"$(env_bin "$attr")" "$target" "$@" & child=$!
wait "$child"; status=$?
trap - TERM INT
saves_push_quiet "$attr" || warn "…"     # warn, never die
exit "$status"
```

A failed push must never fail the launch — the game already ran, the save is on disk, and dying
here looks to the player like the game crashed.

**The journal is what makes crashes survivable.** `cmd_play` also records the pre-launch content
hash. On the *next* `gotg play` or `gotg saves push`, an on-disk hash differing from both
`base_hash` and `pushed_hash` means there is unpushed work, and it goes then. A machine that lost
power pushes late rather than never, and a Steam Stop that SIGKILLs mid-upload is a delay, not a
loss.

`saves_auto=pull` is worth offering on its own: pulling before launch is the half that prevents
divergence in the first place, and it runs *before* the emulator starts, where there is no signal
problem at all.

---

## 11. Security

**Untrusted archive extraction.** The bundle comes from a shared File Browser or a Drive account.
Do all of:

1. Verify the sha256 of the downloaded bundle against both the filename and `latest.json`
   **before invoking tar at all**.
2. `tar -tvf` first, and reject the whole bundle if any member is absolute, contains a `..`
   component, is not a regular file or directory, or does not match one of the env's declared
   `saves` globs. **Symlinks, hardlinks, devices and fifos are rejected outright** — no save set
   here has a legitimate symlink, and symlink-then-write is the classic escape that `-P`-off does
   not stop.
3. Extract into a fresh empty staging directory with `--no-same-owner --no-same-permissions
   --no-overwrite-dir --delay-directory-restore`, then move into `{state}`. This is the project's
   existing write-tmp-then-mv rule, and it means a rejected extract leaves the live saves
   untouched.
4. Never as any user but the invoker; never `sudo`.

**Names from the remote become local paths** in `history`, `restore` and retention pruning. Check
every listed name against `^[0-9]{6}-[0-9a-f]{12}\.tar\.zst$` before it becomes an argument to
anything.

**Secrets.**

- The JWT must not reach `argv` — use `curl --config -` on stdin (§6.1). This is strictly better
  than the current `?auth=` URL, which appears in argv, in server access logs, and in any proxy
  in between.
- `rclone.conf` holds OAuth refresh tokens, i.e. full Drive access. gotg never copies it, never
  prints it, and never passes `RCLONE_CONFIG_*_TOKEN` on a command line. Apply the existing
  `config_check_perms` "must be `?00`" rule to it, with a `die` naming `chmod 600`.
- **Never logged:** the File Browser password, the JWT, any URL carrying a token, the contents of
  `rclone.conf`. `log`/`warn` print *keys* (`env-n64/gen/000042-…`), never URLs. No `set -x`.
- **Bundles are not encrypted.** On a shared File Browser, anyone with read access to
  `/Games/.gotg/saves` can read save files. Say so in the README. An rclone `crypt` remote solves
  it for free — a second good reason to have the rclone backend.

---

## 12. Migration — the part most likely to lose a save

Do not treat this as a footnote. The dev machine has real data in
`~/.local/share/{ares,dolphin-emu,Cemu,2ship}` and `~/Games/*/*.ram`. Turning on the redirect
flags makes all of it invisible in one commit, and the user finds out when Super Metroid starts
from the title screen. That is the "never silently destroy a save" rule applied to the upgrade
itself.

`gotg saves adopt`:

- Each env declares `legacyPaths` alongside `saves`, emitted into `saves.json`. Verified targets:
  ares → `$GOTG_GAMES_DIR/<platform>/<id>.{ram,eeprom,flash,rtc,iram,bsx,dram}` plus
  `~/.local/share/ares`; dolphin → `~/.local/share/dolphin-emu/{GC,Wii,StateSaves}`; cemu →
  `~/.local/share/Cemu/mlc01/usr/save`; harkinian → `~/.local/share/<appName>`.
- **Copies, never moves.** Dry-run by default; `--yes` to act. The originals stay exactly where
  they are, so a mistake costs nothing but disk.
- Runs automatically **once**, on the first `gotg play` after the upgrade, if the new state dir is
  empty and a legacy location is not. That is the only way a Steam-launched user ever learns this
  happened; it goes to the launch log and the launch continues regardless. `adopted: true` in the
  journal stops it repeating.

**`adopt` must ship in the same commit as the redirect flags.** Not the commit after.

The alternative — not isolating, and teaching the CLI where each emulator's shared directories
are — is worse: save paths would then depend on what else is installed on the machine (a system
dolphin shares `~/.local/share/dolphin-emu`), and `env-snes` and `env-snes-world_super_metroid`
would fight over the same files.

---

## 13. File-by-file changes

### New

| File | Contents |
|---|---|
| `client/lib/remote.sh` | the five-function blob interface, backend dispatch, key validation |
| `client/lib/remote-filebrowser.sh` | §6.1 |
| `client/lib/remote-rclone.sh` | §6.2, `rclone_bin` seam (Phase 3) |
| `client/lib/saves.sh` | reads `saves.json`, bundles, hashes, journal, conflict rules, retention |
| `client/lib/cmd-saves.sh` | `cmd_saves` sub-dispatch and the verbs |
| `client/tests/saves.bats` | §14 |
| `client/tests/remote.bats` | backend round trips, 409, traversal |
| `docs/saves.md` | user-facing docs, shaped like `docs/controllers.md` |

### Modified

| File | Change |
|---|---|
| `client/bin/gotg` | `source` lines, one `saves) cmd_saves "$@" ;;` arm, usage heredoc |
| `client/lib/common.sh` | `GOTG_SAVES_DIR`, `GOTG_ATTR_RE`, `validate_attr`, `validate_blob_key`, `iso_now` |
| `client/lib/config.sh` | **`config_write` must merge, not replace.** It currently rebuilds the whole object from four fixed args, so `gotg login` after `gotg saves setup` would silently wipe `saves_backend`. Change to `jq '. + $patch'` over the existing file. Also make `config_check_perms` take a path so it can guard `rclone.conf` |
| `client/lib/api.sh` | `api_curl` with `X-Auth` via `curl --config -`; move `api_fetch` and downloads off `?auth=` (§2.2) |
| `client/lib/env.sh` | `env_state_dir`, `env_saves_manifest` (reads `$(env_root)/share/gotg/saves.json`) |
| `client/lib/cmd-play.sh` | export `GOTG_ENV_STATE`; auto-adopt hook; conditional `exec` (Phase 4) |
| `client/env/lib.nix` | new `saves`, `saveExcludes`, `legacyPaths`, `saveStates` attrs; `runCommand` wrapper emitting `saves.json` (§8) |
| `client/env/{nes,snes,n64,gb,gbc,gba}.nix` | `isolate = true`; `args = [ "--setting" "Paths/Saves={state}/saves/" "{target}" ]`; `saves = [ "saves/**" ]` — **the trailing slash is load-bearing** |
| `client/env/helpers.nix` | `harkinianPort` gains `saves = [ "data/${appName}/saves/**" "data/${appName}/${appName}*.json" ]` and `saveExcludes = [ "data/${appName}/*.o2r" "…/logs/**" "…/mods/**" "…/imgui.ini" ]` — verified against the live `~/.local/share/2ship` layout |
| `client/env/gamecube.nix`, `wii.nix` | Phase 2. `args = [ "-u" "{state}/dolphin/" "-e" "{target}" ]`; `saves = [ "dolphin/GC/**" "dolphin/Wii/title/**" "dolphin/Wii/shared2/**" "dolphin/StateSaves/**" ]`; exclude `dolphin/{Cache,Shaders,Dump}/**` |
| `client/env/wiiu.nix` | Phase 2. `isolate = true`; `args = [ "-m" "{state}/mlc" "-g" "{target}" ]`; `saves = [ "mlc/usr/save/**" ]`; exclude `mlc/usr/title/**`, `config/Cemu/{shaderCache,graphicPacks}/**` |
| `client/env/switch.nix` | Phase 2, **after verifying the flag.** `args = [ "--root-data-dir" "{state}/ryujinx" "{target}" ]`; `saves = [ "ryujinx/bis/user/save/**" "ryujinx/bis/system/save/**" ]`; exclude `ryujinx/games/**` |
| `client/default.nix` | add `gnutar`, `zstd` to the wrapper PATH (`rclone` in Phase 3) |
| `flake.nix` | `client-tests` gains `gnutar`, `zstd` |
| `client/tests/helper.bash` | `export GOTG_ENV_STATE_DIR`; `fake_env` writes a `share/gotg/saves.json`; new `stub_rclone`, `write_saves_config`, `second_device` |
| `client/tests/mock_filebrowser.py` | `do_POST` for `/api/resources` (raw body, `override`, 409, 200), `do_DELETE` (204), `do_PATCH` rename, `do_GET` listing + `?checksum=sha256`; accept `X-Auth`, and accept `?auth=` **only** behind a `--legacy-auth` flag so a test can pin the ≥2.45 behaviour |
| `README.md`, `client/README.md` | command table, a Saves section, updated test counts |

---

## 14. Verification

### Manual — do this before writing any env file

Phase 0. Confirm each redirect flag actually puts files where §2.1 claims, in the discipline
`docs/controllers.md` already sets. A plan built on four unverified flags is worth very little.

```bash
gotg play world.super_metroid          # save in-game, then quit
find ~/.local/state/gotg/env/env-snes-world_super_metroid -name '*.ram'
ls ~/Games/snes/*.ram                  # must NOT reappear
```

Check specifically: ares' trailing slash and its per-system subdirectory; dolphin `-u` with an
absolute path; cemu `-m` creating the mlc skeleton on first run; and above all **ryubing's
`--root-data-dir`**, including whether it is honoured on a first run with no existing data dir.
Repeat for a 2Ship save.

### Automated

`nix flake check`, or `GOTG_BIN=$(which gotg) bats src/client/tests/`. Everything against
`mock_filebrowser.py` over real HTTP, in the existing spirit.

**Round trips and API surface**
- put/get is byte-identical; `?override=true` really overwrites; without it the mock answers 409
  and the client reports *that*, not a generic failure.
- `X-Auth` is accepted; with `--legacy-auth` off, a `?auth=` request is rejected. This pins §2.2
  so a server upgrade cannot silently regress.
- `blob_stat` reads the server-side sha256 without downloading.

**Sync semantics** — a second device is a second `GOTG_STATE_DIR` + `GOTG_CONFIG_DIR` against the
same mock.
- push then pull on device B reproduces device A's tree exactly.
- **Divergence:** B pulls gen 1, A pushes gen 2, B pushes → non-zero exit, message names
  `--force`, **and the remote `latest.json` is unchanged**.
- `--force` keeps the loser: `history` lists both, the old bundle still downloads and restores.
- pull snapshots first, and the snapshot restores.
- an unchanged tree pushes nothing (hash equality short-circuit).
- retention: 12 pushes with `saves_keep=10` leaves 10, and never removes the one `latest.json`
  names.

**Safety**
- **Traversal:** hand-build a bundle containing `../../../../tmp/pwned`, publish it with a
  matching hash → `gotg saves pull` fails and `/tmp/pwned` does not exist. Same for a member that
  is a symlink to `/etc/passwd`. Mirrors the existing `/tmp/pwned` launcher tests.
- hash mismatch: corrupt the bundle server-side → pull refuses, local tree untouched.
- **excludes:** a stand-in `.o2r` in the state dir is absent from `tar -tf` of the bundle.
- **size guard:** an env whose glob matches a large file dies naming the file, and nothing is
  uploaded.

**Wiring**
- `config_write` merge: `gotg login` after `gotg saves setup` preserves `saves_backend`.
- `saves_auto=both` with a stub env that writes a save file: the push happened, and the parent
  shell survived the child (proving the `exec` was dropped only on that path).
- **Every `client/env/*.nix` with `isolate = true` also declares `saves`** — a nix assertion in
  `client/env/default.nix`, or a bats test over the built roots' `saves.json`. Cheap, and it
  catches "added a platform, forgot the saves glob", the failure that silently backs up nothing.

**Seams:** `GOTG_RCLONE`, `GOTG_ENV_STATE_DIR`, `GOTG_DEVICE_ID`, `GOTG_SAVES_MAX_BYTES`,
`GOTG_NOW` (deterministic `written_at`).

For rclone, prefer a **real `rclone` against a local-filesystem remote** (`[test]\ntype = local`
in a tmp `rclone.conf`) over a stub — it exercises the actual argument handling, which is the
only part likely to be wrong. Fall back to a `stub_rclone` shim shaped like the existing
`stub_nix` if adding rclone to the check's `nativeBuildInputs` is unwanted.

---

## 15. Phasing

- **Phase 0** — verify the four redirect flags by launching (§14). Half a day. Do not skip.
- **Phase 1 (minimum shippable)** — `remote.sh` + File Browser backend + `saves.sh` +
  `gotg saves {setup,status,push,pull,adopt}` + `saves.json` emission from `lib.nix` + **ares and
  harkinian envs only**. `saves_auto` stays `off`; `cmd_play` untouched; `exec` stays. Includes
  the `config_write` merge fix and the `X-Auth` migration — prerequisites, not extras. **`adopt`
  ships in the same commit as the redirect flags.**
- **Phase 2** — dolphin, cemu, ryujinx envs; `list`, `history`, `restore`; retention.
- **Phase 3** — rclone backend. Genuinely separable: File Browser already answers "hosted
  manually", and rclone is what answers "or even Google Drive". Also unlocks `crypt`.
- **Phase 4** — `saves_auto`, dropping `exec`, the journal-driven catch-up push.

---

## 16. Open questions

1. ~~**Ryubing's `--root-data-dir` is unverified.**~~ **Answered 2026-08-01: it needs
   no flag at all.** ryubing 1.3.3 honours `XDG_CONFIG_HOME` — launched with it
   redirected, it created `Config.json`, `system`, `profiles`, `sdcard` and `bis`
   (the virtual NAND, where saves live) under the new path. So `isolate = true`
   alone is enough for Switch, and `switch.nix` wants
   `saves = [ "config/Ryujinx/bis/user/save/**" "config/Ryujinx/bis/system/save/**" ]`
   with `config/Ryujinx/games/**` excluded — no `args` change.
   Its `--root-data-dir` was never confirmed to exist and is now moot. Note it
   ignores `--help` and launches regardless, so probe it by watching where it
   writes rather than by asking it.
2. ~~**Does the File Browser account have `Create`/`Modify`/`Delete`?**~~
   **Answered 2026-08-01: read only.** No Modify, no Delete — so `blob_put`
   cannot work against this server at all, and the File Browser backend, which
   is the only one Phase 1 shipped, is unusable here. `gotg saves setup` is
   designed to catch exactly this and will fail at the upload probe. Either the
   account gains write on `<remote_root>/.gotg/saves`, or saves move to a
   different backend — which makes **Phase 3 (rclone) a prerequisite rather than
   an optional extra**, and rclone's `crypt` then also answers the "bundles are
   not encrypted" caveat for free.
3. **Cemu's `mlc01/usr/title`** holds installed updates and DLC. Not saves, but also not
   reconstructible from the ROM, and potentially gigabytes. Excluded here — state it in the README
   so nobody assumes a pull restores a playable Cemu setup.
4. **Dolphin's `Wii/` NAND** is shared across all Wii titles and includes system menu state, not
   just saves. Scoped to `Wii/title/**` and `Wii/shared2/**`, which is where per-game data lives,
   but this deserves one empirical check with a real Wii save before it ships.

---

## 17. House style reminders

From reading `client/lib/*.sh` — match these or the review will bounce:

- Every lib starts with `# shellcheck shell=bash` and a paragraph-length "why" comment. `bin/gotg`
  sets `set -euo pipefail`; libs do not re-set it.
- **Everything informational goes to stderr** (`log`/`warn`/`die`) so stdout stays usable for
  values. Functions return values via `printf '%s'`.
- Multi-line `die` messages are the house style, and they **name the fix**
  (`… then run: gotg sync`).
- Validate before anything becomes a path — see `validate_id`, `validate_platform`,
  `validate_remote_path` in `client/lib/common.sh`.
- Atomic writes: write `$file.tmp` (chmod first if secret), then `mv`.
- Three-way UI branch where a human might be waiting: tty → text, no-tty + display + zenity →
  dialog, else → silent.
- JSON is `jq` everywhere. A "game" is passed between functions as a one-line JSON string.
- Many small files. Test names are full sentences describing behaviour.
- Flake checks that must stay green: `shellcheck --external-sources --source-path=client`, bats,
  ruff.
