# GamesOnTheGo (GOTG)

A game library for a living room: a credential-holding service and indexer
(`src/gotg`, Python, stdlib-only), a bash client that builds per-emulator Nix
environments and launches games (`src/client`), and a pygame picker driven by
padmap-published controllers (`src/ui`). Everything is built and checked
through the Nix flake.

## Setup

```bash
direnv allow          # `use flake .`; also rebuilds ~/.local/state/gotg/app on src/client changes
```

The dev shell puts `gotg`, `gotg-ui`, `gotg-seat`, `padmap`, and
`gotg-test-controllers` on PATH, all running the **working tree**, not the
store. A flake sees only git-tracked files: a new module that is not
`git add`ed is silently absent from every `nix build`, while `gotg-ui` from
the shell still runs it.

## Build / Run

```bash
nix build .#gotg --max-jobs 2 --cores 4        # the client
nix build .#gotg-ui --max-jobs 2 --cores 4     # the packaged picker
gotg-ui                                        # the picker, from the checkout
gotg play <id>                                 # a game; see README for the rest
gotg steam picker                              # the copy Steam launches -- rebuilt only by this
```

### Builds share this desktop

This is a workstation, not a build farm. `nix-daemon` runs with `cores = 0`,
and padmap (a flake input) is a Rust crate that recompiles whenever its pin
moves; two builds at once took all 24 cores and stopped the person typing.

- **One Nix build or check at a time.** Wait for it.
- **Always `--max-jobs 2 --cores 4`** (up to `--max-jobs 3`). `nice` or a
  `systemd-run` scope on the shell does not reach the daemon; these flags do.
- **`nice -n 19`** anything heavy outside Nix (pytest, ffmpeg).
- Under those caps no permission is needed, padmap rebuilds included.
- Work that does not need this machine's hardware belongs on the cluster
  (`node1-3`, `k8s/qa`) or a remote builder when one exists.

## Test

```bash
PYTHONPATH=src/ui:src python -m pytest tests/service tests/indexer tests/ui -q   # unit, ~40s
PYTHONPATH=src/ui:src python -m pytest tests/ui/test_gate.py::test_name -q      # one test
nix build .#checks.x86_64-linux.python-tests --max-jobs 2 --cores 4              # same, in the sandbox
nix build .#checks.x86_64-linux.client-tests --max-jobs 2 --cores 4              # bats, whole suite
nix run .#test-controllers --max-jobs 2 --cores 4 -- -q                          # controller e2e, real devices
nix run .#test-controllers --max-jobs 2 --cores 4 -- -q -k "top_bar"             # one e2e test
```

- The dev venv has **no pygame** on purpose: `tests/ui` tests models only,
  drawing is untested. The e2e brings its own python with pygame.
- `bats tests/client/*.bats` bare does not work (needs a built `share/gotg`);
  use the Nix check.
- `GOTG_E2E_REQUIRE=1` makes the e2e **fail** rather than skip on a machine
  that cannot run it. Set it anywhere the requirement is meant to be enforced.
- After changing `src/ui/gotg_ui/{pads,clones,assign,gate,padmap}.py`,
  `src/client/lib/padmap.sh`, or the padmap pin: run the e2e.

## Lint & Typecheck

```bash
ruff check src/ui tests/ui tests/e2e                              # the picker (dev)
nix build .#checks.x86_64-linux.ruff --max-jobs 2 --cores 4        # src/gotg + tests: check AND format
nix build .#checks.x86_64-linux.shellcheck --max-jobs 2 --cores 4  # the client
```

Both must pass before a change is done. `ruff format` is enforced only on
`src/gotg`; the picker is `ruff check` (E, F, I, W, B, UP, line length 120).

## Code Style & Conventions

- **Prose comments are the house style.** Module and function docstrings say
  what broke, why this shape, and what it cost to find out. Match that
  density; do not strip it to "what the code does".
- **Models are pure and tested; drawing and sockets are not.** A screen is a
  frozen dataclass rebuilt from each event (`assign.Assignment`, `gate.Gate`)
  plus a `decide`/`apply` pair. Put logic there, keep `app.py` a loop.
- **Immutable by default**: `dataclasses.replace`, never mutate a view.
- `src/gotg` is **stdlib-only** — it faces the internet and holds every
  credential; that is its supply-chain posture. PyYAML is the indexer's
  optional extra, nothing else.
- The client is bash: `set -euo pipefail`, every tool overridable by env
  (`GOTG_PADMAP`, `GOTG_SEAT`…) so tests can substitute a recorder.
- Commit subjects are sentences: `fix(ui): leaving the controller screen
  keeps the controller`. Bodies explain the bug that was actually seen.
- Requests to padmap: one file in `docs/requests/`, mirrored into
  `~/Documents/padmap/docs/requests/` where they get answered; sync the
  answer back.

## Architecture

```
src/gotg/service     the proxy: credentials, saves store          (stdlib, pytest)
src/gotg/indexer     catalog builder; rules.yaml                  (pyyaml extra)
src/client/bin,lib   `gotg`: install/play/steam/controllers       (bash, bats)
src/client/env       one Nix environment per platform/game; mods/ per-game overrides
src/client/qa        the QA harness and its synthetic pad         (runs in k8s/qa)
src/ui/gotg_ui       the picker: app.py loop; models in assign/gate/clones/padstrip;
                     pads.py + controllers.py are the pygame half
src/ui/assets        controller SVGs -> assets/built at build time (generated)
config/              controllers/*.yaml, theme, icon rules -- meant to be edited by people
nix/checks           every CI gate, one file each; nix/checks/default.nix lists them
tests/e2e            the controller requirement, against a real daemon (see Test)
docs/requests        the padmap interface, as asks and answers
docs/controllers-usability.md   the controller plan and its decisions
```

The picker depends on the client the way a person does — through `gotg` —
never by importing it. `gotg-seat` sits beside the picker and the client
finds it by name.

## The controller requirement

Two things drive the picker: the keyboard (and mouse), and a controller padmap
has published. Nothing else -- not when padmap is missing, down, or too old.
`GOTG_ANY_PAD=1` is the only override, and it is never set by anything. A controller is published by being held, from
wherever the picker or game is — never from a screen somebody had to find.
Every session — picker or game — starts with nobody seated; padmap's daemon
is started `--fresh --follow <pid>` and ends with the session. Enforced by
`tests/e2e/test_controllers.py`; the rule itself is `clones.py` (match the
GUID's name-CRC, because SDL renames clones), `assign.attend` (one call per
frame, deliberately inseparable), and `gate.decide` (unseat, hold, map).

## Boundaries / Do Not Touch

- `flake.lock` — only via `nix flake lock --update-input <name>`; padmap's rev
  is also written in `flake.nix` and must match.
- `src/ui/assets/built/`, `result*`, `.direnv/`, `__pycache__` — generated.
- `src/gotg/indexer/slugify.py`, `plan.py` — ported verbatim, kept diffable
  against upstream; excluded from `ruff format`.
- `node_modules/`, `.swarm/`, `ruvector.db`, `.claude-flow/` — tool litter,
  gitignored, never source.
- Secrets live in `~/.config/gotg/config.json` (0600) and the k8s secret
  `gotg-qa-config`; nothing in the tree.
- Never stop or reconfigure a padmap daemon you did not start; match it by
  pid from its `state` event, never by argv (that has killed a user's real
  daemon from a test before).

## Commits & PRs

- Conventional prefix, sentence subject: `feat(controllers): …`,
  `fix(dev): …`, `test(ui): …`, `chore(padmap): follow main at <rev>`.
- Commit straight to `master`; it is a solo repo. Commit only when asked.
- Every commit should leave the unit suites and ruff green; run the client
  check and the e2e when the change touches what they cover.

## Gotchas

- `nix flake check` currently fails evaluating `packages.env-foreign-gl`
  (`foreign-gl.nix` needs `mesa`); build checks individually until fixed.
- SDL renames a padmap clone that mirrors a pad it knows (`Xbox 360
  Controller`), so device *names* cannot identify padmap's pads. The GUID's
  bytes 2–3 carry a CRC-16 of the real name; that is what `clones.py` reads.
- padmap grabs every pad for the length of a `begin` session, so the
  assignment screen answers no button but a long hold on an already-seated
  pad. Prefer `seating` mode (no grab) for anything on the grid.
- The picker `execvp`s into the game: its pid survives the hop, which is why
  the daemon follows it and the launcher's `--follow $$` is the same pid.
- With Steam running, a pad that appeared in the last ~second is grabbed by
  Steam and a hold on it reaches nobody; tests hold again, people do too.
- The picker Steam launches is a built copy — `gotg steam picker` refreshes
  it. `.envrc` auto-rebuilds only on `src/client` changes, never `src/ui`.
- The Deck (`steamdeck`, 100.80.53.67) does not accept this machine's ssh
  key; on-device checks need the user.
