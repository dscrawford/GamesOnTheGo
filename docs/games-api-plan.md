# Implementation plan: the games API — a catalog of references instead of a tree of links

**Status:** designed, not started. No code has been written for any of this.
**Written:** 2026-08-11, revised same day after architecture review. **Audience:** whoever
implements it next.

This document is a handoff. Everything marked *verified* was checked against source on the
date above — the importer pipeline, the client's server contract, the service, and the
deployment manifests in `Kubernetes/GOTG/` — everything else is flagged as a decision or an
open question.

Read `README.md` first for the entry-id contract; it does not change.

---

## 1. Why

Today a game reaches a player through three renames of the same bytes:

1. A torrent completes under `/data/Torrents`, named whatever the release group chose.
2. The importer CronJob hardlinks (or extracts, or converts) it into `/data/Games` under the
   canonical entry-id name, and appends a row to `/Games/.gotg/manifest.json`.
3. File Browser serves `/Games` read-only; the client logs in with a username and password,
   fetches the manifest, and downloads by path.

The middle step exists mostly to give files canonical *names* — and a whole materialized
tree, a second server, and a second credential ride along with it. The refactor collapses
this into two layers:

- **Layer 1 — the client.** A local cache of retrieved games. Installed locally means launch
  locally; the network is only for what is missing. (Most of this already exists — see §2.3,
  including the one place it is broken.)
- **Layer 2 — the service.** The GOTG service (the artwork proxy + saves store) grows a
  catalog: a SQLite index mapping entry ids to *where the bytes already are* — the torrent
  tree, unmoved — and streams them to the client over the same bearer-token API the saves
  already use. The File Browser login goes away.

Canonical naming becomes a property of the *catalog row*, not of the filesystem.

Deliberately deferred (make it simple, research later): the clever half of large-file
delivery — trusted one-shot download tokens, `X-Accel-Redirect`, caching. The first version
streams through the service with HTTP Range support. What is *not* deferred is the ingress
and volume plumbing that plain streaming already depends on (§3.6) — deferring those defers
correctness, not polish.

---

## 2. Findings that shape everything

### 2.1 Not every entry is a link — some are *derived* files. Verified.

The importer does real transformation work, not just linking (`execute.py`):

- **Scene archives** (`.rar` + `.sfv`): verified with `rhash`, extracted, largest file kept.
- **Disc images in 7z**: extracted and converted with `dolphin-tool` to RVZ.
- **WiiU decrypted dumps** (`code/ content/ meta/`): zipped into one `<id>.zip`, because a
  single file buys HTTP resume and a stable checksum.

These outputs exist *only* under `/Games` — deleting the tree loses them (recoverable only
by re-running the expensive extract/convert). So "store references back to the torrents"
covers the hardlink majority, but the design needs a **derived store**: a directory the
indexer owns where transformation outputs live, referenced by the same catalog. The row's
`kind` says which is which.

### 2.2 Every catalog entry today is `type: "file"`. Verified.

The manifest schema and the client both support directory entries (`?algo=zip` streaming,
unwrap-on-install), but no importer code path has ever emitted one — inherently multi-file
titles are zipped instead (`plan.py:160-176`). Consequence: the service's download surface
is **single files with Range resume**, full stop. The client's dir-zip machinery
(`download.sh:171-193`, the no-resume branch, the pulsate progress case) is dead code that
the cutover deletes rather than ports. The `type` field leaves the wire with it — v2 is a
breaking version anyway, and keeping a field only to ignore it is the bad third option.

### 2.3 Layer 1 mostly exists — with one broken promise. Verified.

The client already keeps games under `~/Games`, launches installed games without asking the
network (`manifest_cached || manifest_ensure` on every launch-path command), caches the
catalog for `GOTG_MANIFEST_MAX_AGE` (86400s), and re-checks the destination after taking the
per-game download lock.

But the documented degrade-to-cache behavior **does not work**: `manifest_ensure` says
`manifest_refresh || warn "using the cached catalog"` (`manifest.sh:53`), and
`manifest_refresh` calls `die` on a failed fetch (`:32`), which `exit`s the shell — the `||`
cannot catch it. Stale cache + unreachable server is a hard failure for `gotg list` /
`install` / `steam sync` today. This must be fixed (refresh *returns* failure; a bats case
with `GOTG_MANIFEST_MAX_AGE=0` and the server stopped) **before** the cutover phase, because
afterwards the same service also gates downloads, so the path gets strictly more reachable.

### 2.4 The File Browser surface is two endpoints. Verified.

`POST /api/login` (username/password → JWT) and `GET /api/raw/<path>` (`?algo=zip` unused per
§2.2). No listing, no search. Meanwhile the client already speaks bearer-token HTTP to the
GOTG service for saves and artwork (`remote.sh`, `api.json`). The cutover is a convergence,
not an invention — with one caveat: `saves_api_curl` hardcodes `-sS --max-time 120`, which
would kill progress bars and any download over a few hundred MB. The shared helper carries
*auth and base URL only*; timeout and progress policy stay per-caller (§3.5).

### 2.5 The id derivation is already pure. Verified.

`slugify.py` (the whole entry-id contract) imports nothing. `classify.py` and `plan.py` are
pure given a scanned `Source`. Only `execute.py` and `scan.py` touch disk. The indexer keeps
scan → classify → plan verbatim and replaces the execute half: hardlinks become catalog
rows; extract/convert/zip still run, into the derived store.

### 2.6 Checksums dominate importer runtime — the skip machinery must survive. Verified.

`sha256` is computed at import time ("checksumming dominates the runtime" —
`importer/README.md`) and cached in `.sha256` sidecars so re-runs are cheap. Sidecars retire
(writing next to a seeding payload would violate "sources are never modified"), so the skip
moves into the catalog: rows carry `mtime`, the indexer fetches the index-only catalog view
once at run start, and a source whose `(path, size, mtime)` matches its row is skipped
without hashing. Get this wrong and every weekly run re-hashes the whole library.

### 2.7 Seeding is unaffected; deletion semantics are new. Verified / decision.

`/Games` is hardlinks — removing it never touches the torrents. Today's manifest merge only
ever adds (`run.py:89`). The DB makes lifecycle real: `seen_at` stamped by every confirming
scan, vanished rows *reported* rather than auto-deleted — a disappeared torrent is a
person's decision, same spirit as the importer never guessing a platform.

### 2.8 Keys and firmware need a new home. Verified.

`prod.keys`, `title.keys`, `firmware.zip` are hand-placed in `/Games/<platform>/` and
fetched by hardcoded path. Not catalog material (by construction — `prod.keys` parses as a
valid entry id). New home: `GOTG_FILES_DIR/<platform>/<name>`, served by a shape-checked
endpoint (§3.3). The hand-curated directory is the allowlist — a hardcoded name list would
turn a new console's BIOS into a service rebuild.

### 2.9 The deployment is half the refactor. Verified.

The service pod today mounts exactly one volume: its 5Gi saves PVC. Serving games means
mounting `jellyfin-data` — 16TB, **ReadWriteMany over Longhorn's share-manager NFS** — and
that volume has a documented failure mode on this cluster
(`Kubernetes/docs/rwx-recovery-plan.md`): on share-manager recovery, remounts are dropped
and consumer pods sit on stale NFS handles until restarted. Today that takes out File
Browser and jellyfin; naively it would take out saves and artwork too, since one process
serves all three. §3.6 is the design answer. Also in this dimension: the pod runs as 65534
with `fsGroup` set (a recursive chown walk over 16TB if the mount is not read-only), the
importer writes as uid 1000 (derived files must be world-readable), and the catalog DB —
unlike the saves — is *rebuildable*, which the backup notes must say.

### 2.10 The spec lives in another repo, with stale pointers. Verified.

`IMPORTER_SPEC.md` is at `Kubernetes/GOTG/IMPORTER_SPEC.md`; the one pointer in this repo
says `Kubernetes/games/`. `keys.sh:11` cites §11 where the keys contract is §7a. Fix while
in there.

---

## 3. Design

### 3.1 The store: SQLite, in the service, single writer

The honest alternative first: at this library's size, a JSON document rewritten under one
lock — exactly how `saves.py` keeps `current.json` — would work and would add zero new
failure modes. SQLite earns its place on three things the document cannot do: per-row upsert
from a long-running indexer without rewriting the world, the `seen_at` sweep as a query, and
consistent reads while a scan is mid-run. Strong consistency comes from topology, not the
engine: **only the service process writes the DB.** The indexer talks to the API, never the
file.

In-process mechanics (stdlib `sqlite3`, and it will not tolerate leaving this unspecified —
connections are thread-bound by default and every request is its own thread):

- One write connection, guarded by a `threading.Lock` (the `SavesStore._lock` pattern).
- Read connections per-thread via `threading.local`.
- PRAGMAs at open: `journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=5000`.

```sql
CREATE TABLE entry (
  platform     TEXT NOT NULL,     -- 'n64'
  id           TEXT NOT NULL,     -- 'usa.legend_of_zelda_majoras_mask'
  filename     TEXT NOT NULL,     -- 'usa.legend_of_zelda_majoras_mask.z64' (ext matters to emulators)
  path         TEXT NOT NULL,     -- absolute, inside an allowed root
  kind         TEXT NOT NULL CHECK (kind IN ('source', 'derived')),
  size_bytes   INTEGER NOT NULL,
  mtime        INTEGER NOT NULL,  -- of path; the indexer's skip-unchanged key
  source_path  TEXT,              -- kind='derived' only: what it was derived from
  source_mtime INTEGER,           -- ditto; detects a re-downloaded source needing re-derive
  sha256       TEXT,              -- refused by default when absent — see §3.4
  title        TEXT NOT NULL,
  imported_at  TEXT NOT NULL,
  seen_at      TEXT NOT NULL,     -- last scan that confirmed path exists
  PRIMARY KEY (platform, id)
);
```

Config, validated at startup like everything else in `Config.validate`:
`GOTG_CATALOG_DB` (on the saves volume — the only writable path under
`readOnlyRootFilesystem`, with a comment that unlike the saves beside it, this file is
rebuildable by re-running the indexer); `GOTG_LIBRARY_ROOTS` (colon-separated, e.g.
`/data/Torrents:/data/Derived` — must never contain the saves directory or the DB path,
refused at startup, because a catalog row effectively grants read on anything under a
root); `GOTG_FILES_DIR`; `GOTG_INDEX_TOKEN`.

Auth invariants: the existing pre-route authentication check accepts either token, so routes
stay unfingerprintable to the unauthenticated; a `_principal()` helper distinguishes
`client` from `index` after that, and write routes require `index`. Refuse to start when
`GOTG_INDEX_TOKEN == GOTG_PROXY_TOKEN`; when it is unset, catalog writes answer 503 — never
fall back to the client token, or every laptop can rewrite the catalog.

### 3.2 The service API

| Endpoint | Behavior |
|---|---|
| `GET /catalog` | Client view, shape below. Small library — no pagination. |
| `GET /catalog?full=1` | Index principal only: adds `path`, `mtime`, `kind`, `source_*` — what the indexer diffs against at run start so unchanged sources are never re-hashed (§2.6). |
| `GET /games/<platform>/<id>` | Streams the file (§3.6). `Accept-Ranges`, `Range` → 206/`Content-Range`, `Content-Length` always, `ETag` (the sha256) honored via `If-Range` so a resume against changed bytes restarts instead of splicing, `Content-Disposition: attachment; filename=<filename>`. 404 for missing row *and* missing file (the latter logged loudly: the index is stale). |
| `HEAD /games/<platform>/<id>` | Headers only. (The handler must grow `do_HEAD` — today only GET/POST/PUT exist.) |
| `GET /files/<platform>/<name>` | Keys/firmware. `<name>` checked by shape (`^[a-z0-9][a-z0-9._-]{0,63}$`, no traversal), realpath-contained in `GOTG_FILES_DIR`, regular files only. 404 for absent — `keys.sh` stays on its warning path. |
| `PUT /catalog/<platform>/<id>` | Index principal. Upsert — but **409 when the stored row names a different `path`**, unless `?force=1`: two torrents producing the same id is a real error the hardlink collision used to surface, and an upsert must not swallow it. |
| `POST /catalog/sweep` | Index principal. Body: `{"since": "<run start>"}`. Answers with rows whose `seen_at < since` — the vanished report. Deletes nothing. No id list in the body (the 64KiB request cap stays), and refuses (needs `?confirm=1`) when it would report more than 20% of the catalog vanished — the half-run-indexer failure mode must not train anyone to ignore the report. |
| `DELETE /catalog/<platform>/<id>` | Index principal; the human path for removing a row. Never touches the filesystem (`do_DELETE` also new). |

Catalog wire shape, client view:

```json
{ "version": 2,
  "games": [
    { "id": "usa.legend_of_zelda_majoras_mask",
      "platform": "n64",
      "filename": "usa.legend_of_zelda_majoras_mask.z64",
      "size_bytes": 33554432,
      "sha256": "…",
      "title": "Legend Of Zelda Majoras Mask" } ] }
```

`path` leaves the wire; `type` too (§2.2). The download URL is `/games/<platform>/<id>` and
the local filename comes from `filename` — which is now the one server-controlled string
that becomes a local path component, so the client grows `validate_filename` (no `/`, no
`..`, no leading dot, bounded, printable) called in `manifest_find` beside `validate_id` —
the replacement for, not the removal of, what `validate_remote_path` guarded. The service
validates the same shape on PUT; the client does not get to depend on that.

### 3.3 Serving bytes and staying alive (the §2.9 answer)

Two deployments, one image, one codebase — **the streaming half is a separate Deployment**
from the saves/artwork half. Same container, a flag (or the presence of `GOTG_LIBRARY_ROOTS`)
selects which route families are live. Rationale: the library volume is RWX-over-NFS with a
documented hang mode; a request thread stuck in D-state on a stale NFS handle must not be
able to take the saves store down with it. The split also makes rollouts honest — the
streaming pod restarting kills streams (resumable), not save pushes.

- The library mount is `readOnly: true` (File Browser already does this), with
  `fsGroupChangePolicy: OnRootMismatch` — never a recursive chown walk over 16TB.
- `/healthz` keeps touching no filesystem and no DB. Do not "improve" it.
- The indexer (uid 1000) chmods derived output 0644 and verifies world-readability at index
  time — the streaming pod (uid 65534) discovering an unreadable file as a 500 mid-download
  is the wrong place to learn it.
- Streaming uses `os.sendfile` (stdlib; `wfile` is an unbuffered socket) with offset/count
  from the Range header, plain-read fallback for tests. Never more than one chunk of state
  per stream. Note: **this is a new code path** — the saves store reads whole bundles into
  memory and is not a precedent here.
- A semaphore caps concurrent streams (start at 4; 503 + `Retry-After` beyond) —
  `ThreadingHTTPServer` has no thread cap and the pod competes with jellyfin.
- A stream that fails mid-body sets `close_connection = True` — under HTTP/1.1 keep-alive, a
  half-sent body desynchronizes the next request on the connection.
- Containment is resolve → verify against `GOTG_LIBRARY_ROOTS` → open → re-verify the opened
  fd (`st_dev`/`st_ino`); torrent payload names are attacker-adjacent and realpath-then-open
  alone is a TOCTOU window.
- Ingress (in `Kubernetes/GOTG/api.yaml`, for the streaming host): `proxy-buffering: off`,
  `proxy-max-temp-file-size: 0`, `proxy-read-timeout` sized for a slow multi-GB pull. These
  land with the endpoints, not later — nginx's defaults spool responses to the controller's
  own disk, which makes streaming a lie.

### 3.4 The indexer (the importer, reshaped)

Same CronJob, same image, same scan → classify → plan front half; the execute half becomes:

- Fetch `GET /catalog?full=1` once at run start. A source whose `(path, size, mtime)`
  matches its row: skipped, no hash (§2.6). For derived rows, `source_mtime` decides whether
  to re-derive.
- **Hardlink ops** → `PUT` rows, `kind: "source"`, path pointing at the torrent payload. No
  filesystem write.
- **Extract / convert / archive ops** still materialize — into
  `GOTG_DERIVED_DIR/<platform>/<filename>`, then a `kind: "derived"` row. Staging,
  space-margin and sweep logic carry over; `cleanup_staging`, `_assert_under` and
  `_require_space` repoint at the derived dir. Config refuses `GOTG_DERIVED_DIR` inside
  `SOURCE_ROOT` (the scanner iterates `SOURCE_ROOT`'s children and would re-import its own
  output) — this check replaces the old `GAMES_ROOT != SOURCE_ROOT` guard. `PATH_PREFIX` and
  `server_path()` retire.
- Hashing stays here (the indexer owns the expensive pass; the service stays
  request-shaped). A row without a hash is **refused** by the indexer unless an explicit
  `--allow-unhashed` is set — bytes now come straight off a mutable torrent tree, so
  unverifiable rows got riskier, and `gotg info` should say "unverified" when it sees one.
- `POST /catalog/sweep {"since": …}` — only after a *complete* `--scan` pass over
  `SOURCE_ROOT` (a `--bootstrap` or failed run must not report the untouched library as
  vanished). Vanished rows land in the run log as warnings.
- An `--prune-derived` flag removes derived files no row references — the only deletion the
  system performs, and it is opt-in and filesystem-only.
- **Stated decision, not a side effect:** `--once` mode, `qbit.py`, the qBittorrent tags
  (`gotg-imported`/`gotg-manual`/`gotg-error`) and the `processed.json` state PVC are all
  deleted. The deployed CronJob has run `--scan` without qBittorrent credentials since the
  beginning; the tags were the `--once` path's UI and go with it.
- Deploy: CronJob gains `GOTG_API_URL` + index-token Secret; keeps `/data` (reads torrents,
  writes `/data/Derived`).

### 3.5 The client

- **`api.sh` collapses onto `api.json`.** Login/JWT/`X-Auth`/`config.json` credentials go.
  One `service_curl` provides *auth header and base URL only* — timeouts, `--max-filesize`,
  progress flags stay with each caller (§2.4). `gotg login` becomes `gotg setup` (the saves
  flow, promoted): url + token, verified by `GET /catalog` before writing. Migration is
  explicit: on first run against a `config.json` with a `password`, say what replaced it and
  offer to delete the dead credential — not leave it in a 0600 file forever.
- **`manifest.sh`**: fetch `GET /catalog`; `manifest_cached` also checks `.version == 2` so
  a stale v1 cache forces a refresh instead of yielding null ids (the actual flag-day
  hazard); id-derivation jq deleted (`.id` is on the wire); `game_local_path` uses
  `filename`; the two remaining `.path` consumers — `cmd_list`'s installed-check jq and
  `cmd_info`'s `remote:` line — move to `filename`/id; **`manifest_refresh` returns failure
  instead of dying** (§2.3), with the offline bats case.
- **`download.sh`**: URL from the catalog row id; resume with `-C -` for everything;
  `If-Range: <etag>` so resume against changed bytes restarts cleanly; checksum verification
  unchanged; dir-zip branch, `GOTG_DOWNLOAD_TOKEN` plumbing, pulsate case deleted.
- **`keys.sh` / `firmware.sh`**: fetch `/files/<platform>/<name>`; 0600/.part/cache/flock
  all unchanged.
- **Tests**: catalog/download/keys/firmware bats move onto the real `gotg-proxy` binary the
  saves tests already start; `start_saves_service` grows `GOTG_CATALOG_DB`,
  `GOTG_LIBRARY_ROOTS`, `GOTG_FILES_DIR`, `GOTG_INDEX_TOKEN`, and seeding goes through the
  index API instead of `add_game`'s served-directory trick. `mock_filebrowser.py` is deleted
  with the last caller.

### 3.6 What deliberately does not change

The entry-id contract and everything downstream of `manifest_find` (env building, launchers,
Steam integration, saves, controllers). Layer 1's cache policy and offline launching — minus
the §2.3 bug.

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
Manual `GOTG_FILES_DIR` population (copy `prod.keys`/`title.keys`/`firmware.zip` — a
one-time step called out because keys degrade to warnings and a miss would be silent).
Verify: catalog empty, endpoints answer, saves unaffected.

**Phase 3 — the indexer.** §3.4, with **dual-publish**: the link path and manifest.json
writer keep running beside the API posts. Deliverable includes the diff tool (old manifest
vs `GET /catalog`: same ids, sizes, hashes); exit criterion is **zero diffs on a production
run**. Hardlinks are free, so dual-publish costs nothing while it runs.

**Phase 4 — client cutover.** §3.5 in one phase, tests on the real service. After this,
`gotg` speaks only to the service. Deploy order still matters: service + indexer proven
green (Phase 3's diff) before clients update.

**Phase 5 — retirement. Point of no return — gated on N weeks green after Phase 4 plus one
restore rehearsal.** Importer link path + manifest writer deleted; `/Games` tree removed;
File Browser decommissioned (or kept for human browsing of the torrent tree — open
decision); `mock_filebrowser.py` deleted; README and `IMPORTER_SPEC.md` rewritten, stale
pointers (§2.10) fixed. Until this phase, rolling the client back to File Browser remains
possible; write the retention window down when the phase starts.

---

## 5. Risks

- **The RWX volume is the big one** (§2.9/§3.3): a Longhorn share-manager recovery strands
  NFS mounts until pods restart. The deployment split contains the blast radius to
  downloads; saves/artwork stay up. Streams die and resume.
- **Rollouts kill streams**: single replica, `strategy: Recreate` — every image rollout
  kills in-flight downloads. Resumable, but a 4GB pull may span a rollout; deploy the
  streaming half when nobody is downloading, which the split makes possible.
- **Partial catalog reads**: `GET /catalog` mid-indexer-run returns a consistent but partial
  library, and the client caches it for 24h. Accepted and documented — the next refresh
  heals it; a completed-generation marker is deferred until it ever actually bites.
- **SQLite on Longhorn**: single pod, WAL, tiny DB — fine; never scale the deployment past
  one replica without revisiting. Documented at the mount.
- **Derived divergence**: a re-downloaded source gets a new mtime; `source_mtime` triggers
  re-derive. Explicit test.
- **Two catalogs during Phase 3/4**: drift window guarded by the diff tool; ends at Phase 5.
- **Thread/fd pressure**: the semaphore caps streams; page-cache pressure from multi-GB
  streams on a node also running jellyfin is real but bounded by the cap.

## 6. Deferred research (explicitly out of scope now)

- Trusted one-shot download tokens (service authorizes; something dumber streams).
- `X-Accel-Redirect` so nginx does the byte-moving.
- Catalog deltas / ETag on `/catalog` (small library; revisit if that changes).
- Directory-type entries (dead code today; revisit only if a title genuinely cannot be a
  single file).
- Parallel range requests in the client (would also need the ingress rps limit revisited).
