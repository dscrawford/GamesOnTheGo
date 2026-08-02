# rclone save backend — implementation plan

Execution plan for the second saves backend. Expands §"rclone (`lib/remote-rclone.sh`,
Phase 3)" of [saves-plan.md](saves-plan.md); read the "Backend abstraction" and
"Conflict model" sections there first, because none of that changes.

**This is no longer optional.** The File Browser account turned out to be
**read only** — no Modify, no Delete — so `blob_put` cannot work against that
server at all, and the only backend shipped in Phase 1 is unusable. Saves sync
does not currently work on this setup. Either that account gains write on
`<remote_root>/.gotg/saves`, or this plan is the way saves work.

---

## 0. What already exists

More than it looks. Phase 1 built the abstraction first precisely so this would
be additive:

- `client/lib/remote.sh` already dispatches all five operations to
  `rclone_blob_{put,get,stat,list,delete}` — the case arms are written and
  `remote_require_backend` already accepts `rclone`. Only the implementations
  are missing.
- Key validation (`validate_blob_key`, `validate_attr`) happens above the
  backend, so nothing here re-validates and nothing here can be handed a key
  that did not pass.
- `client/lib/saves.sh` — bundling, generations, journal, conflict rules,
  untrusted-archive verification — is backend-agnostic and does not change.
- `gotg saves setup rclone` currently refuses with "not built yet"
  (`cmd-saves.sh`); that arm becomes the real thing.
- `config_patch` / `config_set` merge, so writing `saves_backend` and
  `saves_rclone_remote` cannot clobber the login credentials.

So the deliverable is one new file, one changed `setup` arm, packaging, and
tests.

---

## 1. Decisions

| Decision | Choice | Why |
|---|---|---|
| Shell out, or use an API | **Shell out to `rclone`** | Hand-rolling Drive OAuth is a large amount of security-sensitive code to own. rclone is packaged, audited and already speaks fifty backends — the point of this is *not* to be a Drive client. |
| Which remote | **Whatever the user configures**, as `saves_rclone_remote` | Nothing here is Drive-specific. `gdrive:GOTG/saves`, an S3 bucket and a second machine over SFTP are the same three lines. |
| Who runs `rclone config` | **The user does**, `gotg saves setup` prints the commands | It opens a browser for OAuth. Under Steam that hangs forever with no window — the same reasoning `config.sh` already gives for not using a keyring. |
| Encryption | **Offer `crypt`, do not require it** | It answers the "bundles are not encrypted" caveat for free, but it makes remote checksums useless (see §3) and adds a passphrase to lose. Document both. |
| `rclone` in the wrapper PATH | **Yes, unconditionally** | ~50 MB of closure for everyone, including people using File Browser. The alternative — a second package output — is worse: two things to build, and `saves pull` failing on a machine that installed the wrong one. Revisit only if the closure becomes a real problem on the Deck. |

---

## 2. Task list

| # | Task | Depends on |
|---|---|---|
| 1.1 | `client/lib/remote-rclone.sh` — the five operations | — |
| 1.2 | `rclone_bin` seam + remote/config validation in `common.sh` | — |
| 1.3 | `gotg saves setup rclone` — print, then verify | 1.1 |
| 1.4 | `rclone` in `client/default.nix` PATH, and in the `client-tests` check | — |
| 1.5 | `client/tests/rclone.bats` against a real local-filesystem remote | 1.1 |
| 2.1 | README: which backend, and how to choose | 1.3 |
| 3.1 | `crypt` remote documented as the encryption answer | 1.3 |

---

## 3. The five operations

Only these subcommands. **Never `rclone sync` or `rclone move` on a directory** —
both delete on the far side, and the one promise this whole feature makes is
that it never removes a save.

| Operation | Command |
|---|---|
| `rclone_blob_put` | `copyto <local> <remote>/<key>.part` then `moveto <remote>/<key>.part <remote>/<key>` |
| `rclone_blob_get` | `copyto <remote>/<key> <local>.part`, verify, `mv` |
| `rclone_blob_stat` | `lsjson --stat <remote>/<key>` → `.Size` |
| `rclone_blob_list` | `lsf --files-only <remote>/<prefix>` |
| `rclone_blob_delete` | `deletefile <remote>/<key>` |

Notes that matter:

- **Upload to `.part` and rename**, exactly as the File Browser backend does, so
  a reader never sees a half-written bundle. On Drive `moveto` is a metadata
  operation, so this is cheap as well as correct.
- **`blob_stat` returns an empty hash.** The contract is
  `"<bytes> <sha256-or-empty>"`, and Drive reports MD5, not SHA-256 — with a
  `crypt` remote it reports a hash of the ciphertext, which is meaningless to
  us. This costs nothing: the conflict model verifies the hash *after*
  downloading, against both `latest.json` and the filename, and the File
  Browser backend's server-side checksum was an optimisation rather than a
  requirement. Return the size and an empty second field.
- **`blob_list` must return bare names**, not paths — `lsf --files-only` already
  does, and `saves.sh` sorts them lexically, which is why generations are zero
  padded.
- **A missing key is `return 1`, not an error.** "Nothing has been pushed yet" is
  the ordinary first-run case; `rclone` exits non-zero for both that and a
  genuine failure, so distinguish on stderr (`directory not found` /
  `object not found`) or check with `lsjson --stat` first.
- Add `--config "$(rclone_config)"` to every invocation so the tests can point
  at their own, and so a Steam launch does not depend on `$HOME` being what
  rclone expects.

---

## 4. Configuration and seams

```
saves_backend        = "rclone"
saves_rclone_remote  = "gdrive:GOTG/saves"
```

- `rclone_bin() { printf '%s' "${GOTG_RCLONE:-rclone}"; }` — the same shape as
  `nix_bin()` in `env.sh:24`.
- `rclone_config() { printf '%s' "${GOTG_RCLONE_CONFIG:-$HOME/.config/rclone/rclone.conf}"; }`
- Validate the remote before it becomes an argument:
  `^[A-Za-z0-9_.-]+:[A-Za-z0-9_./-]*$`. It is user-supplied and ends up in
  `argv`.

`gotg saves setup rclone`:

1. Refuse if `rclone` is not on PATH, naming it.
2. If `saves_rclone_remote` is unset, print what to run — `rclone config`, the
   remote name to choose — and exit without writing anything.
3. Otherwise write `saves_backend` and `saves_rclone_remote`, then **prove it**
   with the same probe the File Browser backend uses: put a file, get it back,
   compare, delete. That is what caught the read-only account, and it is the
   only reason we know this backend is needed at all.
4. Check the permissions on `rclone.conf` with the existing
   `config_check_perms <path>` — it holds an OAuth refresh token, which is full
   access to the account, and is worth rather more than the library password.

---

## 5. Testing

Per the saves plan: **a real `rclone` against a local-filesystem remote**, not a
stub. The only part likely to be wrong is argument handling, and a stub would
agree with whatever mistake the implementation makes.

```
[test]
type = local
```

with `GOTG_RCLONE_CONFIG` pointing at it and `saves_rclone_remote = "test:$TMP/remote"`.
`client/tests/rclone.bats` then mirrors `remote.bats` operation for operation —
byte-identical round trip, overwrite, sorted listing, absent key returning 1 not
dying, idempotent delete — so the two backends are held to one contract.

Then the useful part: run **`saves.bats` against both backends**, parameterised
on `GOTG_SAVES_BACKEND`, so divergence, traversal refusal and the size guard are
proven backend-independent rather than tested once and assumed.

`rclone` joins the `client-tests` check inputs in `flake.nix`.

---

## 6. Risks

| Risk | Handling |
|---|---|
| `rclone` exits non-zero for "absent" and "broken" alike | Distinguish explicitly; an absent `latest.json` must stay the quiet first-run path, not an error. |
| Drive rate limits | Bundles are kilobytes and pushed once a session. Not a real risk here; note it rather than engineer for it. |
| `rclone.conf` holds full account access | `config_check_perms`; never log its contents; never `set -x`. |
| A `crypt` remote makes `stat` hashes meaningless | Already the design — the hash field is optional and verification happens after download. |
| Closure size on the Deck | Accepted; revisit if it bites. |
| Two backends drift apart | Run the whole saves suite against both (§5). |

---

## 7. Open questions

1. **Which remote actually?** Drive was the example, but nothing here needs it
   to be Drive. An SFTP remote to the same box already serving the library would
   need no OAuth at all and no browser — worth considering before setting up
   Drive.
2. **Keep the File Browser backend?** It is written, tested, and unusable on
   this server. It stays as the reference implementation of the contract and
   costs nothing to keep, unless the account gains write.
3. **`crypt` from the start?** Cheaper to adopt before there are generations on
   the remote than after.
