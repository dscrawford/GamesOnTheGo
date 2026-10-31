# Web API contract

What a browser UI (a SPA served by this same service, or one on a listed
origin) may rely on. Everything is JSON unless a byte stream is named. Errors
are always `{"error": "<message>"}` with the status code. Auth is the existing
bearer mechanism (`Authorization: Bearer <token>`); this document adds none.
The contract is additive: new fields may appear, existing ones do not change.

## Same-origin and CORS

Serve the SPA from the service's own origin and nothing here applies. For a
SPA on another origin, set `GOTG_CORS_ORIGINS` to a comma-separated list of
exact origins (`https://play.example.net,http://localhost:5173`, or `*`).
Then, for a listed `Origin`:

- responses carry `Access-Control-Allow-Origin` (echoed, with `Vary: Origin`;
  `*` stays `*`) and `Access-Control-Expose-Headers` for `Content-Range`,
  `Accept-Ranges`, `ETag`, `Content-Length`, `Retry-After`, `X-Gotg-Generation`,
  `X-Gotg-Hash`;
- `OPTIONS <any path>` is a tokenless `204` preflight allowing
  `GET, HEAD, PUT, POST, DELETE, OPTIONS` and the headers `Authorization,
  Content-Type, Range, If-Range, X-Gotg-Parent, X-Gotg-Device`.

Unlisted origins get no CORS headers. `/admin` never gets any. Every response
carries `X-Content-Type-Options: nosniff`. Bad origins fail startup.

## `GET /catalog`

With no query parameters: the original reply
`{"version": 2, "games": [...], "files_url"?, "files_urls"?}`, every game, unchanged.

With **any** of the parameters below the reply is paged:

```json
{"version": 2, "total": 5, "offset": 0, "limit": 50, "games": [ <game> ],
 "facets": {"platform": {"n64": 3}, "region": {"usa": 3}, "handler": {"single_file": 4}},
 "files_url": "...", "files_urls": ["..."]}
```

| param | meaning |
|---|---|
| `limit` | 1-500, default 50 |
| `offset` | >= 0, default 0 |
| `q` | search: every whitespace-separated term must appear in title or id (case-insensitive) |
| `platform`, `region`, `handler` | exact match; repeat or comma-separate for OR |
| `min_size`, `max_size` | total bytes of the game's files |
| `sort` | `title` (default), `id`, `size`, `platform`; ties by platform, id |
| `order` | `asc` (default), `desc` |
| `facets=1` | add `facets`: counts over the **whole** catalog, not the match |

A `<game>` in paged replies and in the single-game reply:
`{id, platform, handler, title, region, file_count, size_bytes, files: [{name, size_bytes, mtime, sha256}]}`.
`region` is the id's prefix (`usa.zelda` -> `usa`). Bad parameters are `400`.
`full=1` / `paths=1` keep their restricted meanings.
The catalog has no per-user "installed" state; installed-ness is the client's.
The file `sha256` and `size_bytes` are what a UI compares against local files.

`GET /catalog/<platform>/<id>` -> one `<game>`, or `404`.

## `GET /games/<platform>/<id>/<name>` and `GET /files/<platform>/<name>`

Raw bytes (`application/octet-stream`), `HEAD` supported. For in-browser
emulators:

- `Accept-Ranges: bytes`; `ETag: "<sha256>"` on `/games`.
- `Range: bytes=N-`, `bytes=N-M` and `bytes=-N` (suffix) -> `206` with
  `Content-Range: bytes S-E/TOTAL`. Start past the end, or `bytes=-0` -> `416`
  with `Content-Range: bytes */TOTAL`. Multi-range, malformed or inverted
  ranges are ignored (`200`, whole file).
- `If-Range: "<etag>"` with a stale validator -> whole file, `200`.
- `503` + `Retry-After: 5` when all stream slots are busy.

## `GET /saves`

The caller's environments (one per emulator environment, `env-<name>`), from
the principal's user namespace. Optional `?known=env-n64:<sha256>,env-snes:<sha256>`
is the hash of the copy the caller holds.

```json
{"version": 1, "saves": [{
  "attr": "env-n64",
  "head": {"generation": 3, "hash": "...", "size": 123, "device": "deck", "written_at": "2026-01-01T00:00:00Z"},
  "generations": 3,
  "devices": ["deck", "desktop"],
  "overwritten_generations": [1],
  "has_conflict": true,
  "sync": "in_sync" | "behind" | "diverged" | null
}]}
```

- `overwritten_generations`: kept generations that a `?force=1` push replaced
  (a fork someone chose to discard).
- `sync` (only with `known`): `in_sync` = caller holds the head; `behind` =
  it holds an older kept generation (pull fast-forwards); `diverged` = a hash
  the server does not keep as an ancestor (needs a decision). `null` without `known`.
- `has_conflict` = `overwritten_generations` non-empty or `sync == "diverged"`.

Existing routes are unchanged: `GET|PUT /saves/<attr>`, `/meta`, `/history`,
`/gen/<n>` (a PUT's `409` body is the current head metadata). Forced pushes now
record `forced_over: <generation>` in that generation's metadata.
`503` when the deployment holds no saves store.
