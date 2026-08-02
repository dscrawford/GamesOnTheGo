# Hand-written mock vs the real server in the test sandbox

*Generated: 2026-08-01 · Sources: 4 + local sandbox experiments · Confidence: High on (c)/(e) — tested; Low on (b) — not answered*

Concerns `client/tests/mock_filebrowser.py`, `client/tests/helper.bash`, and the `client-tests`
check in `flake.nix:99-123`.

## Executive summary

**Running the real File Browser in the Nix build sandbox works.** I built a `runCommand` that
starts `filebrowser-2.63.15` on `127.0.0.1:8391` and served HTTP from it — localhost networking
is available inside the sandbox, no container and no NixOS VM required.

Doing so immediately found a real divergence: **File Browser 2.63.15 refuses passwords under 12
characters.** The test credentials are `tester` / `hunter2` (7 chars), which the mock accepts
happily. That is mock drift of exactly the kind the mock's own docstring promises to prevent —
and it points at a live client bug for any modern server.

## 1. The concrete finding: a 12-character password minimum

```
$ filebrowser users add tester hunter2 --perm.admin -d ./fb.db
Error: password is too short, minimum length is 12
```

`mock_filebrowser.py:36-37` and `helper.bash:60` both use `hunter2`. The mock has no password
policy, so the suite passes.

The client-side consequence is independent of testing: `cmd_login` in `config.sh:87` only checks
that the password is non-empty. Against a current File Browser, a user with a short password
gets `login rejected by <server> — check the username and password` — which is true but
unhelpful, because the real reason is length. Worth a specific message, or at minimum a note in
the error.

This is dormant today (production is 2.32.0) and becomes live on upgrade — which is already
on the list from the 82-advisory finding.

## 2. Real binary in the sandbox: verified working

```nix
pkgs.runCommand "probe" { nativeBuildInputs = [ pkgs.filebrowser pkgs.curl ]; } ''
  export HOME=$TMPDIR
  filebrowser config init -d $TMPDIR/fb.db          # rc=0
  filebrowser -d $TMPDIR/fb.db --address 127.0.0.1 --port 8391 --root $TMPDIR/srv &
  # → "Listening on 127.0.0.1:8391", serves requests, logs them
''
```

Observed in the build log: the server started, bound the port, and answered `/api/login` and
`/api/raw/...`. So:

- **Localhost networking is allowed** in a `runCommand` sandbox. (Outbound network is not — but
  nothing here needs it.)
- `$TMPDIR` is writable for the database and the served root.
- `filebrowser` is a single static Go binary, 36.9 MiB unpacked — no container runtime,
  no `nixosTest` VM. `testers.runNixOSTest` would be the idiomatic answer if you needed
  systemd, users, or real networking; for one binary on loopback it is a lot of machinery.

Port allocation and readiness-waiting are already solved in `helper.bash:26-50` (`pick_port`,
the curl retry loop) and would carry over unchanged.

## 3. Should you switch? Genuinely both ways

**For the real binary:** the accepted critique of hand-written mocks is that they only test the
behaviour you already assumed — *"a mock PostgreSQL will happily accept a query that real
PostgreSQL would reject"* — and that mock complexity grows silently *"because the contract was
never explicit"* ([Testcontainers guides](https://testcontainers.com/guides/testing-rest-api-integrations-using-mockserver/)).
`mock_filebrowser.py` has taken on an explicit obligation to track `http/resource.go`, and the
password finding shows it has already slipped.

**Against:** the mock does things the real server cannot. `--fail-after N` drops the connection
mid-transfer to produce a genuinely truncated download — that is how the resume test is real
rather than simulated, and you cannot ask a real File Browser to do it. `--legacy-auth`
similarly lets one suite test old-server behaviour and another test new.

**The honest answer is both, not either.** Keep the mock for fault injection (truncation,
legacy auth, error codes) and add *one* smoke test against the real binary that covers
login → list → download → upload → rename → delete. That test is where drift gets caught, and
it is maybe 30 lines given the existing helpers.

## 4. Version skew: it moves the risk usefully

Testing against nixpkgs' 2.63.15 while running 2.32.0 does not eliminate risk. But it moves it
in the useful direction: it tells you what breaks *when you upgrade*, which is a thing you need
to do anyway. The password-length discovery is precisely that dividend, found in the first
attempt.

Pinning a specific File Browser version in the flake for tests is reasonable and would let the
suite track production deliberately rather than by accident.

## 5. bats-assert — small, standard, worth doing

All three are in nixpkgs: `bats-assert-2.1.0`, `bats-support-0.3.0`, `bats-file-0.4.0`.

The suite currently hand-rolls **43** `[ "$status" -eq 0 ]` and **20** `[ "$status" -ne 0 ]`.
Those become `assert_success` / `assert_failure`. The concrete gain is failure diagnostics: the
bare `[ ... ]` form reports only that a test failed, while `assert_success` prints the command's
actual status and output. On a suite that shells out to a CLI and a live server, that is the
difference between a useful failure and a bisect. `bats-file` covers the
`[ -f ... ]` / `[ ! -e ... ]` checks similarly.

## Key takeaways

1. **Fix the password issue.** Not a test problem — `cmd_login` accepts a password a modern
   File Browser will reject, and the resulting error message misdirects. Live on upgrade.
2. **Add one real-binary smoke test; keep the mock.** Verified viable: localhost works in the
   sandbox and `filebrowser` is a single binary already in nixpkgs. The mock stays for fault
   injection the real server can't do.
3. **Don't reach for `nixosTest` here.** It's the idiomatic answer for services needing systemd
   or real networking; `runCommand` already does what's needed.
4. **Adopt bats-assert.** Mechanical change, ~63 call sites, and the payoff is diagnostics on
   failure rather than silence.
5. **Pin the tested File Browser version deliberately** so the suite tracks production on
   purpose rather than by whatever nixpkgs happens to ship.

## Sources

1. [Testcontainers — testing REST API integrations](https://testcontainers.com/guides/testing-rest-api-integrations-using-mockserver/) — mock drift, implicit contracts
2. [Testcontainers tutorial](https://www.guvi.in/blog/testcontainers-tutorial/) — real-service-in-test rationale
3. [When to mock vs hit live APIs](https://sparrowapp.dev/blogs/when-to-mock-vs-when-to-hit-live-apis-integration-testing-best-practices/) — the hybrid position
4. [Req API client testing](https://dashbit.co/blog/req-api-client-testing) — fault injection as the mock's durable advantage
5. Local experiments — a `runCommand` derivation running `filebrowser-2.63.15` on loopback
   (build log shows "Listening on 127.0.0.1:8391" and served requests); `filebrowser users add`
   rejecting a 7-character password; `nix eval` confirming the three bats helper libraries

## Methodology

The central question — can a real server run in a Nix build sandbox — was settled by building
a derivation that does it, rather than by reading about it. Web search covered the mock-vs-real
trade-off literature.

**Gaps:** sub-question (b), whether Pact-style consumer-driven contract testing applies when the
provider is third-party OSS you don't control, was **not answered** — it needs its own pass.
My working assumption is that provider verification requires running the provider's own test
suite against your contract, which you cannot do here, but that is unverified. I also did not
measure how much the real binary adds to `nix flake check` wall-clock (36.9 MiB fetch, plus
startup).
