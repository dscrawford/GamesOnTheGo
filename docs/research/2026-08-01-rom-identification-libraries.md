# Is `slugify.py` + `plan.py` 1G1R overkill? Research report

*Generated: 2026-08-01 · Sources: 12 · Confidence: Medium-High*

## Executive summary

Yes — the *identification and 1G1R half* of the importer is a reimplementation of
[igir](https://igir.io), which is actively maintained (last push 2026-07-31, 880 stars,
GPL-3.0) and **already in nixpkgs as `igir-5.3.0`**. igir does DAT-by-URL, hash matching up
to SHA256, `--single` 1G1R with `--prefer-region/--prefer-language/--prefer-revision/--prefer-retail`,
hardlinking by default, archive extraction, and CSV reporting — all headless CLI.

But it is *not* a drop-in replacement for the whole importer. igir knows nothing about
qBittorrent, scene RAR sets, Wii U NUS payloads, the GOTG entry-id contract, or
`manifest.json`. The realistic move is **hybrid**: delegate identification + 1G1R to igir,
keep a much smaller slugifier that turns DAT-verified game names into entry ids.

There is **no Python library** for this. Searched PyPI for `romdat`, `dat-parser`, `nointro`,
`logiqx`, `retool-redux`, `romsorter` — all absent. `retool` on PyPI is an unrelated 0.1.0
stub, not unexpectedpanda's tool. So the choice is "shell out to igir" or "keep the code" —
there is no third option where an `import` deletes `slugify.py`.

## 1. igir — what it covers, and what it doesn't

**Covers** ([igir.io](https://igir.io/), [1G1R docs](https://igir.io/roms/1g1r/),
[DAT scanning](https://igir.io/dats/scanning/)):

| GOTG code today | igir equivalent |
|---|---|
| `slugify.parse()` region/revision/status tags | DAT lookup by CRC32/MD5/SHA1/SHA256 |
| `slugify.select_1g1r()` ranking | `--single` + `--prefer-region USA,WORLD,EUR,JPN` `--prefer-revision newer` `--prefer-retail` `--prefer-parent` |
| `plan.plan_no_intro_set()` grouping by slug | parent/clone relationships from the DAT (inferred if absent) |
| `execute._link()` hardlink + EXDEV handling | `igir link --link-mode hardlink` (hardlink is the default) |
| `execute.sha256_file()` / `write_sidecar()` | `--input-checksum-max SHA256` |
| `execute._extract()` unrar dance | `igir extract` |
| `plan.EXT_PLATFORM` extension→platform guessing | DAT membership — the DAT *is* the platform |

Notably `--dat` accepts a URL, so DATs can be pinned in the flake and refreshed without a
release: `--dat "https://raw.githubusercontent.com/libretro/libretro-database/master/dat/System.dat"`.
Formats: Logiqx XML (No-Intro/Redump/TOSEC), MAME ListXML, MAME software lists, ClrMamePro,
Hardware Target Game Database SMDBs — archived or not.

Critical detail from the 1G1R docs: *"1G1R rules are applied against DATs before any ROM
matching happens"* — selection is deterministic and independent of which files happen to be
on disk. `plan.plan_no_intro_set()` groups only the files present, so its answer changes
when the torrent is incomplete. That is a real correctness difference, not a style one.

**Does not cover:**

- qBittorrent polling, tagging, the processed-state backstop (`qbit.py`, `state.py`, `run.py`)
- Scene RAR classification and `.sfv` verification (`classify._is_scene_archive`, `execute._verify_archive`)
- Wii U NUS / decrypted `code/content/meta` detection and zip packing (`classify._is_wiiu_*`, `execute._archive`)
- **Arbitrary output filenames.** igir names output files from the DAT's ROM name; `{datName}`/`{region}`
  tokens control *directories*, not the filename. It cannot emit `usa.legend_of_zelda_majoras_mask_rev1.z64`.
- `manifest.json` in the GOTG schema. There is a CSV `report` command
  ([reporting docs](https://igir.io/output/reporting/)) with FOUND/MISSING/DUPLICATE/UNUSED/DELETED
  statuses — the exact column set is not documented on the page and would need `igir --help` to confirm.
- Auto-discovery of No-Intro DATs from DAT-o-MATIC (you supply the URL; DAT-o-MATIC gates bulk downloads).

## 2. Retool — do not adopt

[Retool](https://github.com/unexpectedpanda/retool) is DAT-in/DAT-out: it emits a filtered
DAT you then feed to a ROM manager, and never touches ROMs. Its 1G1R is generally considered
better than igir's because it uses hand-curated clone lists rather than inferred parent/clone
relationships ([why Retool's 1G1R is better](https://unexpectedpanda.github.io/retool/retool-1g1r/)).

However, **its README states Retool is no longer maintained.** The successor fork
[retool-redux](https://github.com/coreyemtp/retool-redux) has 0 stars and was last pushed
2025-11-03 — not a credible dependency. `nixpkgs#retool` is 2.4.9, i.e. the unmaintained
original. It is also CLI/GUI only, not an importable library, despite being Python.

The part still worth having is the *data*:
[retool-clonelists-metadata](https://github.com/unexpectedpanda/retool-clonelists-metadata)
was updated 2026-07-26 and is BSD-3. Those curated clone lists are exactly what would have
prevented the LodgeNet/GameCube variant bug — they are a data file you could consume
directly without depending on the app.

**Verdict:** skip the tool, consider the clone lists as an optional data source later.

## 3. Hash-based vs filename heuristics

Filename parsing breaks in ways `slugify.py` cannot fix by adding more regex:

- **Renamed files.** Any file a human or a previous tool touched is unidentifiable. Hashes don't care.
- **Silent corruption.** A truncated or bad dump has a perfect filename. Only the hash catches it.
  GOTG currently computes SHA-256 *after* deciding what the file is — the digest proves the transfer,
  not the identity.
- **Variant disambiguation.** `slugify.py:206` documents this exact failure: 135 titles picked a
  LodgeNet/GameCube/Arcade variant over the standard cartridge because ranking fell through to
  filename order. Parent/clone data in the DAT states which is canonical; heuristics guess.
- **Region tags that aren't regions.** The `_REGION_MAP` collapse (Korea→world, Canada→usa) is a
  policy encoded in code. `--prefer-region`/`--prefer-language` make it a flag.
- **New No-Intro conventions.** Every convention change is a code change + release here; with DATs
  it's a refreshed download.

The tradeoff is honest: hash matching requires having the right DAT. Anything not in a DAT
(scene releases, Wii U NUS, homebrew) still needs the current heuristic path. That is why this
is a hybrid, not a replacement.

## 4. `python-slugify` vs hand-rolled `title_slug`

[`python-slugify` 8.0.4](https://pypi.org/project/python-slugify/) is in nixpkgs as
`python3.14-python-slugify-8.0.4`. It handles Unicode transliteration, which `title_slug`
does not (it only strips two apostrophe variants, so `Pokémon` → `pok_mon` today — worth
checking against the real library).

But it does **not** do the two things that matter most here: dropping leading articles and
restoring No-Intro's trailing `", The"` form. `_restore_article()` would survive either way.
The `&` → `and` rule needs `replacements=[["&", " and "]]`.

**Verdict: not worth it.** `title_slug` is 8 lines; replacing it swaps 8 lines of obvious code
for a dependency plus a config object, and the article logic stays regardless. The one real
finding is the Unicode gap — fix that with `unicodedata.normalize('NFKD', s)` and one
`.encode('ascii','ignore')`, in place.

## 5. Migration shape and cost

**Keep the entry-id contract by making igir the identifier, not the namer.** igir writes
DAT-canonical names into a staging tree; GOTG reads the DAT-verified name and slugifies it.
The `^[a-z]{3,5}\.[a-z0-9][a-z0-9_]*$` contract is unaffected — it's applied one step later,
to a name that is now *verified* rather than *guessed*.

Sketch:

```
igir link --dat <pinned URLs> --input <payload> --output <staging>
     --single --prefer-region USA,WORLD,EUR,JPN --prefer-revision newer --prefer-retail
     --link-mode hardlink --input-checksum-max SHA256 report
   → GOTG reads the staging tree (or the CSV report)
   → title_slug(dat_game_name) + region from the DAT tag → entry_id
   → hardlink into /Games/<platform>/<entry_id>.<ext>, write manifest.json
```

What can be deleted from `slugify.py`: `_REGION_PRIORITY`, `select_1g1r()`, `_rev_sort_key()`,
the `_NON_RETAIL` set, `is_retail`/`confident` plumbing — the ranking and the guessing,
roughly 120 of 233 lines. What stays: `_split_tags`, `_restore_article`, `title_slug`,
`_parse_region`, `_parse_revision` — now parsing a *DAT-supplied* name, which is well-formed
by construction, so the low-confidence quarantine path shrinks too. In `plan.py`,
`plan_no_intro_set()`'s grouping and 1G1R branch go; the hardlink op stays.

**Cost and risk:**

- New runtime dep: `igir` (Node/TypeScript, GPL-3.0). Subprocess only — same shape as the
  existing `unrar`/`rhash`/`zip` calls, so GPL-3 is not a linking concern.
- DAT sourcing is the real work. `--dat` takes URLs, but you must pin ones that stay valid.
- The three `no_intro_set` platforms (n64, snes, nes) are all that move. `scene_archive`,
  `wiiu_decrypted`, `wiiu_nus` and `single_file` keep the current path — so `classify.py` is
  untouched and the test suite for those handlers stays valid.
- Not a weekend rewrite, but the blast radius is one handler.

## Key takeaways

1. **`igir` is in nixpkgs, actively maintained, and covers the exact 380 lines that are most
   likely to be wrong.** That is the strongest available argument for delegating.
2. **No Python library exists** for No-Intro naming or DAT XML on PyPI. Anyone claiming
   otherwise is thinking of a tool, not a package.
3. **Skip Retool** — unmaintained, fork is not credible. Its clone-list *data* is still live
   and is the better long-term fix for the variant-selection bug if you stay hand-rolled.
4. **Skip `python-slugify`** — it doesn't solve the article problem, which is the hard part.
   Do fix the Unicode gap in `title_slug` in place.
5. **Scope the migration to the `no_intro_set` handler only.** Scene archives and Wii U have
   no library answer and should stay as they are.

## Sources

1. [igir.io](https://igir.io/) — official docs: commands, link modes, checksums, archive support
2. [igir 1G1R preferences](https://igir.io/roms/1g1r/) — `--single` and the `--prefer-*` flag list
3. [igir DAT scanning](https://igir.io/dats/scanning/) — URL support, supported DAT formats
4. [igir DAT introduction](https://igir.io/dats/introduction/) — `--dat <path|glob|url>`
5. [igir reporting](https://igir.io/output/reporting/) — CSV report, FOUND/MISSING/DUPLICATE statuses
6. [igir output path options](https://igir.io/output/path-options/) — filenames come from the DAT, not templates
7. [emmercm/igir GitHub API](https://github.com/emmercm/igir) — 880 stars, GPL-3.0, pushed 2026-07-31
8. [unexpectedpanda/retool README](https://github.com/unexpectedpanda/retool/blob/main/readme.md) — "no longer maintained", DAT-in/DAT-out
9. [Why Retool's 1G1R is better](https://unexpectedpanda.github.io/retool/retool-1g1r/) — curated clone lists vs inference
10. [coreyemtp/retool-redux](https://github.com/coreyemtp/retool-redux) — fork, 0 stars, last push 2025-11-03
11. [unexpectedpanda/retool-clonelists-metadata](https://github.com/unexpectedpanda/retool-clonelists-metadata) — BSD-3 clone lists, updated 2026-07-26
12. [python-slugify on PyPI](https://pypi.org/project/python-slugify/) — 8.0.4; nixpkgs `python3.14-python-slugify-8.0.4`

## Methodology

Two search engines plus direct PyPI and GitHub API queries. `nix eval` against the local
nixpkgs confirmed packaging for `igir`, `retool`, and `python-slugify`. PyPI was queried
directly for six candidate package names to establish the negative result.

**Gaps:** igir's CSV column names are not documented on the reporting page — confirm with
`igir --help` before designing a report-driven pipeline. Per-payload invocation cost (igir
is built for whole-collection runs, GOTG imports one torrent at a time) was not measured.
