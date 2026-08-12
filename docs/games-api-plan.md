# Implementation plan: the games API — a catalog of references instead of a tree of links

**Status:** designed, not started. No code has been written for any of this.
**Written:** 2026-08-11. Revision 2, same day: architecture review applied, then the
processing pipeline inverted to run client-side. **Audience:** whoever implements it next.

This document is a handoff. Everything marked *verified* was checked against source on the
date above — the importer pipeline, the client's server contract, the service, and the
deployment manifests in `Kubernetes/GOTG/` — everything else is flagged as a decision or an
open question.

Read `README.md` first for the entry-id contract; it does not change.

---

## 1. Why

Today a game reaches a player through three renames of the same bytes:

1. A torrent completes under `/data/Torrents`, named whatever the release group chose.
2. The importer CronJob hardlinks — or extracts, converts, re-zips — it into `/data/Games`
   under the canonical entry-id name, and appends a row to `/Games/.gotg/manifest.json`.
3. File Browser serves `/Games` read-only; the client logs in with a username and password,
   fetches the manifest, and downloads by path.

The middle step exists mostly to give files canonical *names* — and a materialized tree, a
second server, and a second credential ride along with it. The refactor collapses this into
two layers and inverts the processing:

- **Layer 1 — the client.** A local cache of retrieved games. Installed locally means launch
  locally; the network is only for what is missing. (Most of this exists — see §2.3,
  including the one place it is broken.)
- **Layer 2 — the service.** The GOTG service (artwork proxy + saves store) grows a catalog:
  a SQLite index mapping entry ids to *where the bytes already are* — the torrent tree,
  unmoved — and streams them to the client over the same bearer-token API the saves use.
  The File Browser login goes away.

**The pipeline inversion.** The old flow was `source → process on the server → serve the
refined file` — which is why the importer's image carries `dolphin-emu` for one binary and
why the first plan draft needed a server-side "derived store". The new flow is
`source → download raw → process on the client → refined artifact, cached locally`:

- The catalog describes the *raw* source and names its shape (`handler`); the client's nix
  environment for that platform decides what to do about it.
- Flakes are what make this cheap: each environment already lazily pulls exactly the tools
  its platform needs (`path = [...]` in the env derivation — dolphin-tool and pyisotools
  arrive only for the GameCube mod pipeline today). A 7z'd disc image pulls `p7zip` and
  `dolphin-tool`; a scene rar pulls `unrar`; SNES pulls nothing.
- Steps chain, and the repo has already proven the pattern end to end:
  `kuriboSunshineDisc` (`client/env/helpers.nix`) does download → convert → extract → patch
  → rebuild on first run, caches the result under `{state}`, and redirects the launch
  target. `harkinianPort` and `firmware_unpack` are the same shape smaller.

Canonical naming becomes a property of the catalog row; canonical *format* becomes a
property of the client environment. The server stores and streams; it never transforms.

Deliberately deferred (make it simple, research later): the clever half of large-file
delivery — trusted one-shot download tokens, `X-Accel-Redirect`, caching. The first version
streams through the service with HTTP Range support. What is *not* deferred is the ingress
and volume plumbing that plain streaming already depends on (§3.3) — deferring those defers
correctness, not polish.

---

## 2. Findings that shape everything

### 2.1 Not every entry is a link — some sources need transformation. Verified.

The importer does real work beyond linking (`execute.py`): scene archives (`.rar`+`.sfv`)
are verified and extracted; 7z'd disc images are converted to RVZ with `dolphin-tool`; WiiU
decrypted dumps (`code/ content/ meta/`) are zipped for transport. Under the inversion these
all become **client-side recipes** (§3.5): the server serves the raw bytes, the catalog
carries the shape, and the environment that owns the platform's tools does the work once,
locally, cached. The server-side derived store from revision 1 is gone; so is the importer's
transformation half, its dolphin-emu image dependency, its space-margin logic, and the
uid-1000-writes / uid-65534-reads readability dance.

The costs move too, and they are accepted knowingly: every machine pays the extract/convert
once per game (a disc conversion took ~90s each on the cluster; a Steam Deck will feel a
multi-GB unrar), the raw download can be larger than the refined artifact it replaces, and
processing needs transient double disk space on the client. §5 carries these.

### 2.2 Raw sources bring multi-file entries back. Verified, and a reversal.

Every *published* entry today is a single file — the importer zips anything that is not
(`plan.py:160-176`). Raw sources are not so tidy: a scene release is a directory of `.rar`
volumes; a WiiU dump is a tree. So a catalog entry becomes a **list of files** (one, for the
common hardlink case), each downloaded individually — which keeps Range resume per file and
kills the `?algo=zip` streaming path for good. The client's dir-zip machinery
(`download.sh:171-193`, the no-resume branch, the pulsate case) is still deleted; what
replaces it is a loop over member files, each resumable, each checksummed. The `type` field
leaves the wire; `files` replaces it.

### 2.3 Layer 1 mostly exists — with one broken promise. Verified.

The client already keeps games under `~/Games`, launches installed games without asking the
network (`manifest_cached || manifest_ensure` on every launch-path command), caches the
catalog for `GOTG_MANIFEST_MAX_AGE` (86400s), and re-checks the destination after taking
the per-game download lock.

But the documented degrade-to-cache behavior **does not work**: `manifest_ensure` says
`manifest_refresh || warn "using the cached catalog"` (`manifest.sh:53`), and
`manifest_refresh` calls `die` on a failed fetch (`:32`), which `exit`s the shell — the `||`
cannot catch it. Stale cache + unreachable server is a hard failure for `gotg list` /
`install` / `steam sync` today. Fix (refresh *returns* failure; a bats case with
`GOTG_MANIFEST_MAX_AGE=0` and the server stopped) **before** the cutover phase — afterwards
the same service also gates downloads, so the path gets strictly more reachable.

### 2.4 The File Browser surface is two endpoints. Verified.

`POST /api/login` (username/password → JWT) and `GET /api/raw/<path>`. No listing, no
search. The client already speaks bearer-token HTTP to the GOTG service for saves and
artwork (`remote.sh`, `api.json`), so the cutover is a convergence — with one caveat:
`saves_api_curl` hardcodes `-sS --max-time 120`, which would kill progress bars and any
download over a few hundred MB. The shared helper carries *auth and base URL only*;
timeout and progress policy stay per-caller (§3.6).

### 2.5 The id derivation is already pure. Verified.

`slugify.py` (the whole entry-id contract) imports nothing. `classify.py` and `plan.py` are
pure given a scanned `Source`. Only `execute.py` and `scan.py` touch disk. The indexer
keeps scan → classify → plan verbatim; under the inversion the execute half is not
"replaced", it is **deleted** — classification's `handler` goes into the catalog row and
the client environments take it from there.

### 2.6 Checksums dominate importer runtime — the skip machinery must survive. Verified.

`sha256` is computed at import time ("checksumming dominates the runtime") and cached in
`.sha256` sidecars so re-runs are cheap. Sidecars retire (writing next to a seeding payload
would violate "sources are never modified"), so the skip moves into the catalog: file rows
carry `mtime`, the indexer fetches the index-only catalog view once at run start, and a
member whose `(path, size, mtime)` matches its row is skipped without hashing. Get this
wrong and every weekly run re-hashes the library.

### 2.7 Seeding is unaffected; deletion semantics are new. Verified / decision.

`/Games` is hardlinks — removing it never touches the torrents; with the inversion the
server writes nothing at all. Today's manifest merge only ever adds (`run.py:89`). The DB
makes lifecycle real: `seen_at` stamped by every confirming scan, vanished rows *reported*
rather than auto-deleted — a disappeared torrent is a person's decision, same spirit as the
importer never guessing a platform.

### 2.8 The hand-placed aux files need a new home. Verified.

The per-platform aux files (firmware and the like) are hand-placed in
`/Games/<platform>/` and fetched by hardcoded path. Not catalog material (by construction —
their names parse as valid entry ids). New home: `GOTG_FILES_DIR/<platform>/<name>`, served
by a shape-checked endpoint (§3.4). The hand-curated directory is the allowlist. These
files also feed client-side recipes that need them, which the client already stages into
each environment — nothing new required beyond the endpoint.

### 2.9 The deployment is half the refactor. Verified.

The service pod today mounts exactly one volume: its 5Gi saves PVC. Serving games means
mounting `jellyfin-data` — 16TB, **ReadWriteMany over Longhorn's share-manager NFS** — and
that volume has a documented failure mode on this cluster
(`Kubernetes/docs/rwx-recovery-plan.md`): on share-manager recovery, remounts are dropped
and consumer pods sit on stale NFS handles until restarted. Naively that would take out
saves and artwork too, since one process serves all three. §3.3 is the design answer. The
pod runs as 65534 with `fsGroup` set (a recursive chown walk over 16TB if the mount is not
read-only — it must be, and with the inversion it can be: the service never writes the
library). The catalog DB — unlike the saves — is *rebuildable*, which the backup notes must
say.

### 2.10 The spec lives in another repo, with stale pointers. Verified.

`IMPORTER_SPEC.md` is at `Kubernetes/GOTG/IMPORTER_SPEC.md`; the one pointer in this repo
says `Kubernetes/games/`. `keys.sh:11` cites §11 where the aux-files contract is §7a. Fix
while in there.

---

## 3. Design

### 3.1 The store: SQLite, in the service, single writer

The honest alternative first: at this library's size, a JSON document rewritten under one
lock — exactly how `saves.py` keeps `current.json` — would work and would add zero new
failure modes. SQLite earns its place on three things the document cannot do: per-row
upsert from a long-running indexer without rewriting the world, the `seen_at` sweep as a
query, and consistent reads while a scan is mid-run. Strong consistency comes from
topology, not the engine: **only the service process writes the DB.** The indexer talks to
the API, never the file.

In-process mechanics (stdlib `sqlite3`, and it will not tolerate leaving this unspecified —
connections are thread-bound by default and every request is its own thread):

- One write connection, guarded by a `threading.Lock` (the `SavesStore._lock` pattern).
- Read connections per-thread via `threading.local`.
- PRAGMAs at open: `journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=5000`.

```sql
CREATE TABLE entry (
  platform    TEXT NOT NULL,     -- 'n64'
  id          TEXT NOT NULL,     -- 'usa.legend_of_zelda_majoras_mask'
  handler     TEXT NOT NULL,     -- classification: 'single_file' | 'scene_archive' |
                                 -- 'single_archive' | 'wiiu_decrypted' | ...
  title       TEXT NOT NULL,
  imported_at TEXT NOT NULL,
  seen_at     TEXT NOT NULL,     -- last scan that confirmed every member exists
  PRIMARY KEY (platform, id)
);

CREATE TABLE entry_file (
  platform   TEXT NOT NULL,
  id         TEXT NOT NULL,
  name       TEXT NOT NULL,      -- relative name inside the entry; single-file entries
                                 -- use the canonical filename ('usa.foo.z64')
  path       TEXT NOT NULL,      -- absolute, inside an allowed root
  size_bytes INTEGER NOT NULL,
  mtime      INTEGER NOT NULL,   -- the indexer's skip-unchanged key
  sha256     TEXT,               -- refused by default when absent — see §3.5
  PRIMARY KEY (platform, id, name),
  FOREIGN KEY (platform, id) REFERENCES entry(platform, id) ON DELETE CASCADE
);
```

Config, validated at startup like everything else in `Config.validate`:
`GOTG_CATALOG_DB` (on the saves volume — the only writable path under
`readOnlyRootFilesystem`, with a comment that unlike the saves beside it, this file is
rebuildable by re-running the indexer); `GOTG_LIBRARY_ROOTS` (colon-separated — must never
contain the saves directory or the DB path, refused at startup, because a catalog row
effectively grants read on anything under a root); `GOTG_FILES_DIR`; `GOTG_INDEX_TOKEN`.

Auth invariants: the existing pre-route authentication check accepts either token, so
routes stay unfingerprintable to the unauthenticated; a `_principal()` helper distinguishes
`client` from `index` after that, and write routes require `index`. Refuse to start when
`GOTG_INDEX_TOKEN == GOTG_PROXY_TOKEN`; when it is unset, catalog writes answer 503 — never
fall back to the client token, or every laptop can rewrite the catalog.

### 3.2 The service API

| Endpoint | Behavior |
|---|---|
| `GET /catalog` | Client view, shape below. Small library — no pagination. |
| `GET /catalog?full=1` | Index principal only: adds `path` and `mtime` per file — what the indexer diffs against at run start so unchanged members are never re-hashed (§2.6). |
| `GET /games/<platform>/<id>/<name>` | Streams one member file (§3.3). `Accept-Ranges`, `Range` → 206/`Content-Range`, `Content-Length` always, `ETag` (the sha256) honored via `If-Range` so a resume against changed bytes restarts instead of splicing, `Content-Disposition: attachment; filename=<name>`. 404 for missing row *and* missing file (the latter logged loudly: the index is stale). |
| `HEAD /games/<platform>/<id>/<name>` | Headers only. (The handler must grow `do_HEAD` — today only GET/POST/PUT exist.) |
| `GET /files/<platform>/<name>` | Keys/firmware. `<name>` checked by shape (`^[a-z0-9][a-z0-9._-]{0,63}$`, no traversal), realpath-contained in `GOTG_FILES_DIR`, regular files only. 404 for absent — `keys.sh` stays on its warning path. |
| `PUT /catalog/<platform>/<id>` | Index principal. Body carries the entry and its file list. Upsert — but **409 when the stored entry's paths differ**, unless `?force=1`: two torrents producing the same id is a real error the hardlink collision used to surface, and an upsert must not swallow it. |
| `POST /catalog/sweep` | Index principal. Body: `{"since": "<run start>"}`. Answers with entries whose `seen_at < since` — the vanished report. Deletes nothing. No id list in the body (the 64KiB request cap stays), and refuses (needs `?confirm=1`) when it would report more than 20% of the catalog vanished — the half-run-indexer failure mode must not train anyone to ignore the report. |
| `DELETE /catalog/<platform>/<id>` | Index principal; the human path for removing an entry. Never touches the filesystem (`do_DELETE` also new). |

Catalog wire shape, client view:

```json
{ "version": 2,
  "games": [
    { "id": "usa.legend_of_zelda_majoras_mask",
      "platform": "n64",
      "handler": "single_file",
      "title": "Legend Of Zelda Majoras Mask",
      "files": [
        { "name": "usa.legend_of_zelda_majoras_mask.z64",
          "size_bytes": 33554432,
          "sha256": "…" } ] } ] }
```

`path` never reaches the wire; `type` is gone (§2.2). Download URLs are
`/games/<platform>/<id>/<name>`, and `name` is now the server-controlled string that
becomes a local path component — so the client grows `validate_filename` (no `/`, no `..`,
no leading dot, bounded, printable) applied to every member name in `manifest_find` beside
`validate_id` — the replacement for, not the removal of, what `validate_remote_path`
guarded. The service validates the same shape on PUT; the client does not get to depend on
that.

### 3.3 Serving bytes and staying alive (the §2.9 answer)

Two deployments, one image, one codebase — **the streaming half is a separate Deployment**
from the saves/artwork half. Same container, a flag (or the presence of
`GOTG_LIBRARY_ROOTS`) selects which route families are live. Rationale: the library volume
is RWX-over-NFS with a documented hang mode; a request thread stuck in D-state on a stale
NFS handle must not be able to take the saves store down with it. The split also makes
rollouts honest — the streaming pod restarting kills streams (resumable), not save pushes.

- The library mount is `readOnly: true` everywhere (the inversion means nothing in-cluster
  writes game bytes any more), with `fsGroupChangePolicy: OnRootMismatch` — never a
  recursive chown walk over 16TB.
- `/healthz` keeps touching no filesystem and no DB. Do not "improve" it.
- Streaming uses `os.sendfile` (stdlib; `wfile` is an unbuffered socket) with offset/count
  from the Range header, plain-read fallback for tests. Never more than one chunk of state
  per stream. Note: **this is a new code path** — the saves store reads whole bundles into
  memory and is not a precedent here.
- A semaphore caps concurrent streams (start at 4; 503 + `Retry-After` beyond) —
  `ThreadingHTTPServer` has no thread cap and the pod competes with jellyfin.
- A stream that fails mid-body sets `close_connection = True` — under HTTP/1.1 keep-alive,
  a half-sent body desynchronizes the next request on the connection.
- Containment is resolve → verify against `GOTG_LIBRARY_ROOTS` → open → re-verify the
  opened fd (`st_dev`/`st_ino`); torrent payload names are attacker-adjacent and
  realpath-then-open alone is a TOCTOU window.
- Ingress (in `Kubernetes/GOTG/api.yaml`, for the streaming host): `proxy-buffering: off`,
  `proxy-max-temp-file-size: 0`, `proxy-read-timeout` sized for a slow multi-GB pull. These
  land with the endpoints, not later — nginx's defaults spool responses to the controller's
  own disk, which makes streaming a lie.

### 3.4 The indexer (the importer, reduced to its pure half)

Same CronJob, same scan → classify → plan front half (§2.5); the execute half is deleted:

- Fetch `GET /catalog?full=1` once at run start. A member whose `(path, size, mtime)`
  matches its row: skipped, no hash (§2.6).
- Every plan op — link, extract, convert, archive alike — becomes a `PUT` naming the raw
  member files and the `handler`. **No filesystem write of any kind.** Staging sweep,
  space-margin logic, `PATH_PREFIX`/`server_path()`, and the `GAMES_ROOT` config all
  retire.
- The image shrinks: `dolphin-emu`, `zip` and `rhash` leave (transformation and sfv
  verification are client recipes now); `unrar` and `p7zip` stay only because `scan.py`
  lists archive members through them for classification.
- Hashing stays here (the indexer owns the expensive pass; the service stays
  request-shaped). A row without a hash is **refused** unless an explicit `--allow-unhashed`
  is set — bytes now come straight off a mutable torrent tree, so unverifiable rows got
  riskier, and `gotg info` should say "unverified" when it sees one.
- `POST /catalog/sweep {"since": …}` — only after a *complete* `--scan` pass over
  `SOURCE_ROOT` (a `--bootstrap` or failed run must not report the untouched library as
  vanished). Vanished rows land in the run log as warnings.
- **Stated decision, not a side effect:** `--once` mode, `qbit.py`, the qBittorrent tags
  (`gotg-imported`/`gotg-manual`/`gotg-error`) and the `processed.json` state PVC are all
  deleted. The deployed CronJob has run `--scan` without qBittorrent credentials since the
  beginning; the tags were the `--once` path's UI and go with it.
- Deploy: CronJob gains `GOTG_API_URL` + index-token Secret; `/data` mount can become
  read-only.

### 3.5 The client: download, then process — recipes in the environments

The new layer, and the heart of the inversion. `gotg install` becomes:

1. **Download** every member file of the entry — each with Range resume, each verified
   against its `sha256` — into `~/Games/<platform>/.gotg-raw/<id>/`.
2. **Process**, if the entry's `handler` calls for it, by running the environment's recipe
   once; the refined artifact lands at `game_local_path` and the raw members are deleted
   (they are re-downloadable; the refined file is deterministic). `single_file` entries
   skip straight to placing the one member.
3. **Launch** as today.

Recipes live in the env derivations, not in the catalog — the catalog says what a source
*is* (`handler`), the flake says what to *do about it*, which keeps tool closures lazy and
reviewable in nix where they already are:

- `scene_archive` → `unrar x`, keep the largest file, verify `.sfv` with `rhash` when
  present. (`unrar` is unfree-redistributable — the client flake needs the same allowance
  the importer image already made; `libarchive` is not a substitute, its RAR5 support is
  partial. Flagged as the one licensing wrinkle.)
- `single_archive` + a platform whose target format differs → `7z x`, then
  `dolphin-tool convert -f rvz` (GameCube/Wii). Tools ride the platform env's `path`.
- `wiiu_decrypted` → nothing at all: with per-member downloads the client fetches
  `code/ content/ meta/` directly and Cemu reads the tree — the zip existed only for
  single-file transport, which §2.2 just removed the need for.
- Chains compose left to right and a recipe can need the platform's aux files, which
  `keys_ensure` already fetched.

Mechanics follow `kuriboSunshineDisc` exactly: staging inside the target filesystem,
interrupted-run debris swept at start (`chmod -R u+w` first — store-sourced inputs arrive
read-only), space checked before extracting, the whole thing skipped when the refined
artifact already exists. The recipe runs under the per-game download lock the client
already takes.

The rest of the client change set:

- **`api.sh` collapses onto `api.json`.** Login/JWT/`X-Auth`/`config.json` credentials go.
  One `service_curl` provides *auth header and base URL only* — timeouts, `--max-filesize`,
  progress flags stay with each caller (§2.4). `gotg login` becomes `gotg setup` (the saves
  flow, promoted): url + token, verified by `GET /catalog` before writing. Migration is
  explicit: on first run against a `config.json` with a `password`, say what replaced it
  and offer to delete the dead credential — not leave it in a 0600 file forever.
- **`manifest.sh`**: fetch `GET /catalog`; `manifest_cached` also checks `.version == 2` so
  a stale v1 cache forces a refresh instead of yielding null ids (the actual flag-day
  hazard); id-derivation jq deleted (`.id` is on the wire); `game_local_path` from the
  canonical member `name` (or `<id>` for multi-file trees); the two remaining `.path`
  consumers — `cmd_list`'s installed-check jq and `cmd_info`'s `remote:` line — move to
  the new fields; **`manifest_refresh` returns failure instead of dying** (§2.3), with the
  offline bats case.
- **`download.sh`**: the member loop replaces the single fetch; `If-Range: <etag>` so
  resume against changed bytes restarts cleanly; per-member checksum verification;
  dir-zip branch, `GOTG_DOWNLOAD_TOKEN` plumbing, pulsate case deleted.
- **`keys.sh` / `firmware.sh`**: fetch `/files/<platform>/<name>`; 0600/.part/cache/flock
  all unchanged.
- **Tests**: catalog/download/keys/firmware bats move onto the real `gotg-proxy` binary the
  saves tests already start; `start_saves_service` grows `GOTG_CATALOG_DB`,
  `GOTG_LIBRARY_ROOTS`, `GOTG_FILES_DIR`, `GOTG_INDEX_TOKEN`, and seeding goes through the
  index API instead of `add_game`'s served-directory trick. Recipe tests stub the heavy
  tools (the `stub_nix` pattern) and assert the chain, not the conversion.
  `mock_filebrowser.py` is deleted with the last caller.

### 3.6 What deliberately does not change

The entry-id contract and everything downstream of `manifest_find` (env building,
launchers, Steam integration, saves, controllers). Layer 1's cache policy and offline
launching — minus the §2.3 bug.

---

## 4. Phases

Each phase lands green (`nix flake check`) and shippable on its own.

**Phase 0 — client groundwork.** The §2.3 fix (refresh returns, offline bats case) and
`validate_filename`. Standalone corrections worth shipping before anything else moves.

**Phase 1 — catalog store in the service.** `catalog.py` beside `saves.py`: SQLite store as
§3.1, store tests (upsert, 409-on-path-conflict, sweep-since, vanish rail, containment,
concurrent reads during writes, the threading mechanics). Routes wired with the two-token
split. Pure addition.

**Phase 2 — content endpoints.** `GET`/`HEAD` `/games/...` with Range/`If-Range`/sendfile,
`/files/...`, the semaphore, `do_HEAD`/`do_DELETE`. Tests: resume mid-file, resume after
bytes changed, missing row vs file, containment breach, symlink component, stream failure
closes connection, 503 past the cap. Still pure addition.

**Phase 2½ — deploy (the other repo).** api.yaml: the second Deployment (§3.3), read-only
library mount, fsGroup policy, ingress annotations, tokens; importer.yaml: env + Secret.
Manual `GOTG_FILES_DIR` population (copy the hand-placed aux files — a one-time step
called out because they degrade to warnings and a miss would be silent).
Verify: catalog empty, endpoints answer, saves unaffected.

**Phase 3 — the indexer.** §3.4, with **dual-publish**: the old link-and-transform path and
manifest.json writer keep running beside the API posts. Deliverable includes the diff tool
— which under the inversion is a *mapping* check, not an equality check: a derived old
entry (an RVZ) corresponds to a raw new entry (the 7z) with the same id and a `handler`
that will produce the equivalent artifact; sizes and hashes match only for the hardlink
majority. Exit criterion: every old id present and mapped, zero unexplained rows, on a
production run.

**Phase 3½ — client recipes.** The §3.5 pipeline behind the existing install flow, one
handler at a time — `single_file` (no-op, the bulk of the library), then `scene_archive`,
then `single_archive`+convert, then `wiiu_decrypted`. Each with its stubbed-tool bats
coverage, plus one real end-to-end per recipe run manually against the dual-published
catalog.

**Phase 4 — client cutover.** The rest of §3.5 in one phase, tests on the real service.
After this, `gotg` speaks only to the service. Deploy order still matters: service +
indexer proven green (Phase 3's mapping) before clients update.

**Phase 5 — retirement. Point of no return — gated on N weeks green after Phase 4 plus one
restore rehearsal.** Importer transformation/link path + manifest writer deleted; `/Games`
tree removed; File Browser decommissioned (or kept for human browsing of the torrent tree —
open decision); `mock_filebrowser.py` deleted; README and `IMPORTER_SPEC.md` rewritten,
stale pointers (§2.10) fixed. Until this phase, rolling the client back to File Browser
remains possible; write the retention window down when the phase starts.

---

## 5. Risks

- **The RWX volume is the big one** (§2.9/§3.3): a Longhorn share-manager recovery strands
  NFS mounts until pods restart. The deployment split contains the blast radius to
  downloads; saves/artwork stay up. Streams die and resume.
- **Per-client processing cost** (§2.1): the Deck unrars scene releases and converts discs
  itself, once per game per machine, with transient double disk space. Accepted — the
  library's transformation-needing tail is small, the recipe skips when the artifact
  exists, and the recipe declaration is catalog-driven (`handler`), so a server-side
  pre-process could return behind the same contract if this ever hurts. Watch it at Phase
  3½'s manual end-to-end runs.
- **Recipe drift**: a recipe is code in the client flake reproducing what the importer used
  to do in one place. The Phase 3 mapping check and per-recipe end-to-end runs are the
  guard; after Phase 5 the recipes *are* the definition.
- **Rollouts kill streams**: single replica, `strategy: Recreate` — every image rollout
  kills in-flight downloads. Resumable, but a 4GB pull may span a rollout; deploy the
  streaming half when nobody is downloading, which the split makes possible.
- **Partial catalog reads**: `GET /catalog` mid-indexer-run returns a consistent but
  partial library, and the client caches it for 24h. Accepted and documented — the next
  refresh heals it; a completed-generation marker is deferred until it ever actually bites.
- **SQLite on Longhorn**: single pod, WAL, tiny DB — fine; never scale the deployment past
  one replica without revisiting. Documented at the mount.
- **Two catalogs during Phase 3/4**: drift window guarded by the mapping tool; ends at
  Phase 5.
- **Thread/fd pressure**: the semaphore caps streams; page-cache pressure from multi-GB
  streams on a node also running jellyfin is real but bounded by the cap.

## 6. Deferred research (explicitly out of scope now)

- Trusted one-shot download tokens (service authorizes; something dumber streams).
- `X-Accel-Redirect` so nginx does the byte-moving.
- Catalog deltas / ETag on `/catalog` (small library; revisit if that changes).
- Parallel range requests in the client (would also need the ingress rps limit revisited).
- Recipe outputs shared between machines (a converted RVZ could ride the saves-style store
  instead of being re-derived per machine — only worth it if Phase 3½'s cost watch says so).
