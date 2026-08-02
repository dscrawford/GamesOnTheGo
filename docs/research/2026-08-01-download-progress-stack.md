# Is the hand-rolled curl + FIFO + zenity download stack overkill? Research report

*Generated: 2026-08-01 · Sources: 10 + local verification · Confidence: High (claims marked ✅ were tested locally)*

## Executive summary

Two separate questions got conflated here, and they have opposite answers.

**Keep curl.** aria2 does not verify checksums any earlier than you already do, its retry
story is equivalent, and its progress output is *harder* to machine-parse than what the code
does today. Its only real advantage is multi-connection speed on large files — a performance
decision, not a maintenance one.

**Delete the FIFO harness.** `pv -n` emits exactly the bare-percentage stream zenity wants,
and `set -o pipefail` / `${PIPESTATUS[0]}` propagate the downloader's real exit status. That
removes the `mkfifo` / `exec 9>` / `kill -0` / `stat` / `sleep` machinery from both copies.

**And there is a security finding that outranks both.** `api_raw_url()` puts the JWT in the
URL query string (`?auth=$token`), which is [CVE-2025-52901](https://www.miggo.io/vulnerability-database/cve/CVE-2025-52901)
in File Browser itself (CVSS 6.5). File Browser supports an `X-Auth` header; the client is
choosing the leaky path.

## 1. The security finding (act on this first)

`client/lib/api.sh:40-50` builds every download URL as:

```
$GOTG_SERVER/api/raw$encoded?auth=$token
```

- **CVE-2025-52901** — File Browser used access tokens as GET parameters, exposing the JWT
  "to anyone with access to the URLs users visit"; fixed in v2.33.9, CVSS 6.5 Medium
  ([miggo](https://www.miggo.io/vulnerability-database/cve/CVE-2025-52901)). The token lands
  in server access logs and any intermediate proxy's logs.
- **Local exposure, independent of the CVE:** the full URL is a curl argv, so the JWT is
  visible in `ps aux` to every local user for the whole duration of a multi-GB transfer.
- File Browser accepts the `X-Auth` header for `/api/raw`
  ([filebrowser.org/authentication](https://filebrowser.org/authentication)) — the same token,
  transmitted where it does not get logged.

The mitigation is small and independent of everything else in this report: pass
`X-Auth` instead of `?auth=`. To keep it out of argv too, feed it via `curl --config -` on
stdin rather than `-H` (a `-H` argument is equally visible in `ps`).

Note the tradeoff: `api_raw_url()` currently returns a self-contained URL, and moving auth
into a header means the header has to travel with it to every call site. That is a real but
modest refactor of `_curl_download`.

## 2. aria2 — mostly a wash

| Claim | Finding |
|---|---|
| HTTP resume | `--continue`, HTTP(S)/FTP only. Equivalent to `curl -C -`. |
| Checksum verification | `--checksum=sha-256=DIGEST`. **Verified after the file is downloaded, not during** — "if a hash of entire file is provided, hash check is only done when file has been already downloaded" ([aria2 manual](https://aria2.github.io/manual/en/html/aria2c.html)). On mismatch it exits non-zero; with `--check-integrity` it re-downloads from scratch. |
| Retries | `--max-tries` default 5, `--retry-wait` default 0. Comparable to the current `--retry 3 --retry-delay 2`. |
| Multi-connection | `--split` / `--max-connection-per-server`. Requires Range support **and a known size**. |
| No Content-Length | Splitting and resume both depend on size/range. The streamed-zip case degrades to a single connection with no resume — which is exactly what `_curl_download` already does by dropping `-C -` for `type == dir`. |
| Machine-parsable progress | `--summary-interval=SEC` emits a human-oriented multi-line summary; `--stderr` redirects console output. There is no bare-percentage mode. |

**The checksum timing point is the one worth stating plainly:** aria2's whole-file check
happens at the same moment `_verify_checksum` runs today — after the bytes are on disk,
before anything is installed. Verifying "during transfer" is only possible with piece hashes
(BitTorrent/Metalink), which a File Browser HTTP download does not provide. **So the current
staging-then-verify-then-`mv` design is not a weakness, and aria2 would not improve it.**

Parsing `--summary-interval` output for a dialog would be strictly worse than the current
`stat -c '%s'` on the partial file, which is simple and already correct.

**Verdict:** aria2 is not a maintenance win. Consider it only if multi-GB transfers from
your server are bandwidth-limited per-connection and the server honours Range requests —
that's a speed experiment, measurable in an afternoon, not a refactor.

## 3. `pv` — this is the real win

Verified locally against `pv-1.11.0` from nixpkgs:

- ✅ **`curl … | pv -n -f -s SIZE > file` emits bare percentages** (`100`) on stderr —
  precisely zenity's stdin format. `-f` is required because stderr is not a terminal here.
- ✅ **Exit status propagates**: with `set -o pipefail` a failing first stage gives `rc=7`,
  and `${PIPESTATUS[0]}` gives `7` independently. Either mechanism works.
- **Unknown size** (the streamed zip): without `-s` there is no percentage, but `-b`/`-F`
  still emit byte counts, and `zenity --pulsate` is already the code's answer for that case.
- ✅ **`pv -d PID` (`--watchfd`) works** — it watches a running process's file descriptors, so
  curl can keep writing the file itself with `-o` and `-C -` intact. **Caveat found by
  testing:** its numeric output is prefixed (`1:out.bin: 2`), not bare, so it needs a
  `sed -u 's/.*: //'` to feed zenity.

**The two shapes, and the tradeoff:**

- *Pipeline form* (`curl | pv -n | ...`) gives the cleanest output but curl now writes to
  stdout, so `-C -` can no longer discover its own resume offset — you'd compute the offset
  from the partial file and pass `-C <offset>` with `>>`. Three lines added against ~50 deleted.
- *Watchfd form* preserves `-o file -C -` exactly as-is and costs one `sed`. **This is the
  better fit for GOTG**, because resume-on-interrupt is a stated design goal in the file header.

Either way the `mkfifo`, the private temp dir, the `exec 9>`/`exec 9>&-` fd juggling, the
`while kill -0` loop, the `stat` polling and the `sleep "$PROGRESS_TICK"` all go away, in
both copies.

## 4. `zenity --auto-kill` — do not adopt

✅ Confirmed from `zenity 4.2.2 --help-progress`: `--auto-kill` "Kill parent process if Cancel
button is pressed."

That is blunter than what the code does now. The current `kill -0 "$zen_pid"` check catches
cancellation *and* "zenity failed to start at all" (the comment at `download.sh:80-82` says
so), and it deliberately leaves the partial file on disk so the next run resumes.
`--auto-kill` would SIGTERM the whole `gotg` process, skipping that. **Keep the explicit
check** — but note it only needs to survive in *one* place once the harness is extracted.

The FIFO, separately, exists only because the progress source is a polling loop rather than a
pipeline. With pv in the pipeline, zenity reads stdin directly and the FIFO is redundant.

## Key takeaways

1. **Fix the auth token first.** `?auth=$token` is CVE-2025-52901's exact pattern plus a
   `ps`-visible credential. Independent of everything else, smallest diff, highest value.
2. **Extract one `_with_progress` helper.** `download.sh:46-108` and `env.sh:74-107` are the
   same ~65 lines twice; that duplication is the actual maintenance cost, and it exists
   whether or not pv is adopted.
3. **Use `pv --watchfd` inside that helper** to delete the mkfifo/`kill -0`/`stat`/`sleep`
   machinery while keeping curl's `-o` + `-C -` resume intact. Costs one `sed -u`.
4. **Keep curl; skip aria2.** No checksum-timing advantage, no retry advantage, worse progress
   output. Revisit only as a measured speed experiment.
5. **Keep `--auto-close`, skip `--auto-kill`.** The current cancellation handling is more
   correct than the built-in, because it preserves the partial file for resume.
6. **The staging → verify → `mv` design is right.** aria2 confirms it: whole-file hashing
   after download is the only option over plain HTTP.

## Sources

1. [aria2c(1) manual, 1.37.0](https://aria2.github.io/manual/en/html/aria2c.html) — `--checksum` timing, `--continue`, retry defaults
2. [aria2 README](https://aria2.github.io/manual/en/html/README.html) — supported hash algorithms
3. [CVE-2025-52901 (miggo)](https://www.miggo.io/vulnerability-database/cve/CVE-2025-52901) — File Browser URL auth token leak, CVSS 6.5, fixed 2.33.9
4. [File Browser authentication docs](https://filebrowser.org/authentication) — `X-Auth` header
5. [filebrowser/filebrowser#3401](https://github.com/filebrowser/filebrowser/issues/3401) — auth inconsistency between `/api/raw` and `/api/resources`
6. [Baeldung — exit status of piped processes](https://www.baeldung.com/linux/exit-status-piped-processes) — `PIPESTATUS`, `pipefail`
7. [Tiger Computing — pipe commands and status](https://www.tiger-computing.co.uk/linux-tips-pipe-commands-status/) — same, cross-check
8. [ubuntuforums — exit code with zenity --progress](https://ubuntuforums.org/showthread.php?t=397778) — `PIPESTATUS[0]` with zenity
9. [pv(1)](https://docs.oracle.com/cd/E56342_01/html/E54074/pv-1.html) — `-n`, `-s`, `-f`, `--watchfd`
10. Local verification — `nix shell nixpkgs#{pv,aria2,zenity}`; `pv --help`, `aria2c --help=#all`, `zenity --help-progress`, plus three scripted tests of the pipeline form, the watchfd form, and exit-status propagation

## Methodology

Web search across two engines for the aria2 and File Browser questions, then direct reads of
the aria2 manual and the CVE record. Every claim about `pv`, `zenity` and shell exit-status
behaviour was verified by running it locally against the exact nixpkgs versions this project
would use, rather than trusting documentation — which is how the `pv --watchfd` output-prefix
caveat surfaced.

**Gaps:** whether this File Browser deployment is ≥2.33.9 (and therefore whether `?auth=` is
still even accepted) was not checked — that's a one-line query against the server. No
bandwidth measurement was made, so the aria2 multi-connection speed claim remains untested
for this server.
