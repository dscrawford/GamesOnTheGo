# Safe extraction of an untrusted tar from bash

*Generated: 2026-08-01 · Sources: 3 + local adversarial testing · Confidence: High (the core claims are tested, not read)*

Concerns `saves_verify_bundle` and `saves_extract` in `client/lib/saves.sh` (staged, uncommitted).

## Executive summary

The security posture is sound — nothing here is exploitable. But two of the five checks are
**dead code**, and the one genuinely fragile check can be replaced by something simpler and
locale-independent.

I built the classic symlink-then-write-through-it escape archive and ran it against three
extractors. **All three blocked it.** The hand-rolled checks are belt-and-braces over
defences that already exist in `tar` itself.

## 1. Checks (3) and (4) can never fire — verified

`saves.sh:242-246` rejects member names starting with `/` and names containing `..`. Those
names never reach the loop:

```
$ tar -tf hostile.tar
tar: Removing leading `/' from member names
tar: Removing leading `../' from member names
abs/evil.txt          # was /abs/evil.txt
climb.txt             # was ../climb.txt
```

GNU tar normalises both **in its own listing output**, so the loop tests post-sanitised
strings. The checks are unreachable, not wrong. Harmless, but they read as load-bearing
security when they are not — which is the kind of thing that makes a later reviewer relax the
check that actually matters.

## 2. The escape is already blocked — by all three extractors

Attack archive: a symlink member `escape -> ../../victim`, followed by a member
`escape/pwned.txt` that writes through it.

| Extractor | Result | Escaped? |
|---|---|---|
| GNU tar 1.35 | `escape/pwned.txt: Cannot open: Not a directory` → non-zero exit | No |
| bsdtar (libarchive 3.8.8) | `Cannot extract through symlink` → error exit | No |
| Python 3.12 `tarfile`, `filter='data'` | `LinkOutsideDestinationError` before writing | No |

This matches the documented behaviour: unless invoked with `-P`, bsdtar defends three classes —
absolute paths (bsdtar itself), dot-dot paths (libarchive's `cleanup_pathname()`), and
extraction through symlinks (`check_symlinks()`)
([bsdtar(1)](https://manpages.debian.org/stretch/libarchive-tools/bsdtar.1.en.html)). GNU tar
likewise refuses entries whose pathnames contain `..` *or whose target directory would be
altered by a symlink*.

So the load-bearing checks in `saves_verify_bundle` are really (2) the member-type check and
(5) the glob allowlist. Both are worth keeping — (5) especially, since it is the only one
that enforces *this project's* policy rather than generic filesystem safety.

## 3. The one fragile check, and a simpler replacement

```bash
kinds="$(tar -tvf "$bundle" | cut -c1 | LC_ALL=C sort -u | tr -d '\n')"
[[ "$kinds" == "-" ]] || die ...
```

This parses human-readable `tar -tvf` output and depends on column 1 being the type character.
It works on GNU tar, but it's a screen-scrape of a format with no stability guarantee, and it
writes tar's own warnings to stderr as a side effect.

**Suggested replacement** — assert the property directly, after extraction, on the staging
directory that is already isolated:

```bash
if find "$staging" -mindepth 1 ! -type f -a ! -type d -print -quit | grep -q .; then
  die "the bundle for $attr holds something that is not a plain file. Refusing to install it."
fi
```

Same guarantee, no output parsing, locale-independent, and strictly stronger — it inspects
what actually landed rather than what the listing claimed. `saves_extract` already extracts to
a private staging directory and moves files individually (`saves.sh:270-288`), so nothing
escapes into the state directory before the check runs.

The reference checklist worth keeping in mind is Python's `filter='data'`
([PEP 706](https://peps.python.org/pep-0706/)): reject absolute paths, reject `..`, reject
links pointing outside the destination, reject devices and FIFOs, and strip unsafe
permission bits. `saves.sh` covers all of these; that is a good sign, not a problem.

## Key takeaways

1. **Nothing is exploitable.** Tested, not assumed.
2. **Delete or re-comment checks (3) and (4).** They cannot fire — `tar -tf` sanitises names
   before printing them. Leaving them implies a protection that isn't there.
3. **Replace the `tar -tvf | cut -c1` parse with `find ! -type f` on the staging directory.**
   Fewer lines, no screen-scraping, and it checks reality instead of a listing.
4. **Keep the glob allowlist.** It is the only check enforcing project policy rather than
   generic safety, and no extractor will do it for you.
5. **Switching to bsdtar is optional.** Its protections are more explicit and purpose-built,
   but GNU tar blocked the same attack, and `tar` is already a dependency.

## Sources

1. [bsdtar(1) manual](https://manpages.debian.org/stretch/libarchive-tools/bsdtar.1.en.html) — the three attack classes libarchive defends against
2. [PEP 706 — Filter for tarfile.extractall](https://peps.python.org/pep-0706/) — the `data` filter as a reference checklist
3. [libarchive issue #1044](https://github.com/libarchive/libarchive/issues/1044) — symlink/hardlink extraction edge cases
4. Local adversarial testing — a purpose-built symlink-escape archive run against GNU tar 1.35, bsdtar 3.8.8 and CPython 3.12 `tarfile`; plus a hostile archive with absolute and `..` member names to confirm `tar -tf` normalisation

## Methodology

Rather than researching what these tools claim, I built the attack archives and ran them.
Web search was used only to confirm that the observed behaviour matches documented intent.

**Gap:** only tested as an unprivileged user on ext4. Extraction as root, or onto a filesystem
with different symlink semantics, was not exercised — though `--no-same-owner` in
`saves_extract` already covers the main root-specific hazard.
