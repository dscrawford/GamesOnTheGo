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
store, and exports `GOTG_SEAT` and `GOTG_BIN` pointing at them — so a game
launched from the picker here meets *this* checkout's gate, whatever PATH it
inherited. A flake sees only git-tracked files: a new module that is not
`git add`ed is silently absent from every `nix build`, while `gotg-ui` from
the shell still runs it; `.envrc` lists anything untracked under `src/client`,
`src/ui` or `config` on entry.

`.envrc` also compares `~/.nix-profile`'s locked rev with HEAD, since that is
what a launch from Steam or a bare terminal runs. Keep it current:

```bash
git push && nix profile upgrade gotg gotg-ui   # the profile follows origin
gotg sync                                      # and the copies Steam launches
```

The upgrade alone is not what Steam runs. Its picker entry starts
`~/.local/state/gotg/picker` first and the client's `app` root beside it --
both made by `gotg sync`, both left where they were by `nix profile upgrade`.
A Deck upgraded that way kept running the day-old picker, and a fix that
had shipped looked like one that did not work. On a machine where a full
sync is too long for now, the two roots alone are `nix build <flake>#gotg-ui
-o ~/.local/state/gotg/picker` and `#gotg -o ~/.local/state/gotg/app`.

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
nix build .#controllers-image --max-jobs 2 --cores 4                            # the same suite, for k8s/controllers
```

- The dev venv has **no pygame** on purpose: `tests/ui` tests models only,
  drawing is untested. The e2e brings its own python with pygame.
- `bats tests/client/*.bats` bare does not work (needs a built `share/gotg`);
  use the Nix check.
- `GOTG_E2E_REQUIRE=1` makes the e2e **fail** rather than skip on a machine
  that cannot run it. Set it anywhere the requirement is meant to be enforced.
- After changing `src/ui/gotg_ui/{pads,clones,assign,gate,padmap}.py`,
  `src/client/lib/padmap.sh`, or the padmap pin: run the e2e.
- **Never while the user is playing.** The fake pads are real devices, and
  a real daemon in seating mode seats them: one landed as player two in the
  user's game. The suite fails fast if the real socket
  (`$XDG_RUNTIME_DIR/padmap/padmap.sock`) exists; do not override that.
- **Prefer the cluster.** `k8s/controllers/` runs the same suite in a
  privileged pod with `/dev/uinput`, which is where it belongs — 56 of 71
  pass there and ten are strict xfails, each naming the padmap request that
  fixes it (`test_pairing.py`: four people pairing, joins mid-game); the five
  that walk padmap's wizard do not yet pass (see that README). Build and push the image tagged by its store hash,
  apply the Job, read the logs.

## Lint & Typecheck

```bash
ruff check src/ui tests/ui tests/e2e                              # the picker (dev)
nix build .#checks.x86_64-linux.ruff --max-jobs 2 --cores 4        # src/gotg + tests: check AND format
nix build .#checks.x86_64-linux.shellcheck --max-jobs 2 --cores 4  # the client
nix build .#checks.x86_64-linux.rust --max-jobs 2 --cores 4        # rust/: test, clippy -D warnings, fmt
```

All must pass before a change is done. In a checkout, `cargo test` in `rust/`
needs SDL3 and libwayland on `PKG_CONFIG_PATH` (`nix shell nixpkgs#cargo
nixpkgs#rustc nixpkgs#pkg-config nixpkgs#sdl3.dev nixpkgs#wayland.dev`). `ruff format` is enforced only on
`src/gotg`; the picker is `ruff check` (E, F, I, W, B, UP, line length 120).

## Code Style & Conventions

- **Prose comments are the house style.** Module and function docstrings say
  what broke, why this shape, and what it cost to find out. Match that
  density; do not strip it to "what the code does".
- **Models are pure and tested; drawing and sockets are not.** A screen is a
  frozen dataclass rebuilt from each event (`assign.Assignment`, `gate.Gate`)
  plus a `decide`/`apply` pair. Put logic there, keep `app.py` a loop.
- **Immutable by default**: `dataclasses.replace`, never mutate a view.
- **Rust, Python and Nix** are this repo's languages (plus the bash client
  that predates the rule). No new C: native programs are crates in `rust/`,
  built through the flake with `cargoLock`. `gotg-pads` and
  `gotg-killswitch` were C until 2026-09.
- `src/gotg` is **stdlib-only** — it faces the internet and holds every
  credential; that is its supply-chain posture. PyYAML is the indexer's
  optional extra, nothing else.
- The client is bash: `set -euo pipefail`, every tool overridable by env
  (`GOTG_PADMAP`, `GOTG_SEAT`…) so tests can substitute a recorder.
- Commit subjects are sentences: `fix(ui): leaving the controller screen
  keeps the controller`. Bodies explain the bug that was actually seen.
- Requests to padmap: one file in `docs/requests/`, copied into
  `~/Documents/padmap/docs/requests/`, where an agent picks it up. A file
  present is open; padmap answers by deleting it in the commit that does the
  work (`git -C ~/Documents/padmap log --diff-filter=D -- docs/requests/`).
  Sync the answer back by deleting GOTG's copy too, and bump the pin to use it.

## Architecture

```
src/gotg/service     the proxy: credentials, saves store          (stdlib, pytest)
src/gotg/indexer     catalog builder; rules.yaml                  (pyyaml extra)
src/client/bin,lib   `gotg`: install/play/steam/controllers       (bash, bats)
src/client/env       one Nix environment per platform/game; mods/ per-game overrides
src/client/qa        the QA harness and its synthetic pad         (runs in k8s/qa)
rust/crates          gotg-pads (what SDL sees) and gotg-killswitch (the exit chord
                     and the overlay bar; pure models + overlay/painter)   (cargo)
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

One thing drives the picker: a controller padmap has published -- and the
keyboard is one of them. It used to be the fallback that always worked, which
was the hole the rule exists to close: a controller is a keyboard in hardware,
so "anything that types" meant pads nobody had assigned. The keyboard now
takes a seat like everything else (`keys.py`, padmap's `seat_keyboard`), and
until it has, **the only key heard anywhere is the space bar that asks for the
seat** -- on the grid, at the launch gate and at the door. Nothing else drives
anything, not when padmap is missing, down, or too old. The mouse is still the
mouse: padmap has no seat for one, and it is the way out of a window whose
keyboard has not paired. `GOTG_ANY_PAD=1` is the only override, and it is
never set by anything.
A controller that is also a keyboard or mouse (a Steam Controller's lizard
mode, a Bluetooth Xbox pad's extra HID collections) is held with `EVIOCGRAB`
while the picker runs -- `hush.py` -- because SDL delivers a key, not the
device it came from. A controller is published by being held, from
wherever the picker or game is — never from a screen somebody had to find.
Every session — picker or game — starts with nobody seated; padmap's daemon
is started `--fresh --follow <pid>` and ends with the session. Every launch,
`gotg play` or Steam, meets the gate first and it is never silently skipped:
with no padmap it opens anyway, says why, and counts down. **From the picker
the gate runs in the picker's own window** (`seat.before_launch`, then
`GOTG_SEAT_MET=1` so the client does not ask again) -- it used to be a second
process with a second window, which flickered and threw away the seat that had
just been taken. Seats survive a launch when the daemon follows this session's
pid (`gate.Gate.ours`, `PADMAP_FOLLOW`): what somebody paired in the picker is
what they play with, and a daemon from another evening is still forgotten. Enforced by
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
- **Dolphin does not use that rename.** Under `padmap-rs exec` it lists a
  clone as `SDL/0/padmap Player N` (its CI log says so: `Added device:`),
  while gotg-pads reports the same clone as `Xbox 360 Controller` -- which is
  also the name of the raw pad padmap has grabbed. Binding Dolphin by
  gotg-pads' name bound player one to a dead device and nothing moved in
  Four Swords Adventures. padmap's own `emit` names Dolphin ports right; the
  FSA mod rewrites gotg-pads' output to Dolphin's names before the GBA binder
  reads it. To see what Dolphin sees, set `[Logs] CI = True` and
  `WriteToFile = True` in the environment's `Logger.ini`; the list lands in
  `data/dolphin-emu/Logs/dolphin.log`.
- Controllers are keyboards too, **on every screen that reads input**. The
  Steam Controller Puck is four keyboards and four mice in hardware until
  something sends lizard-off over hidraw; a Bluetooth Xbox pad has
  `Keyboard`/`Mouse`/`Consumer Control` nodes on the same `Uniq` as its
  joystick. The picker held those nodes from the start and `gotg-seat` did
  not, which is the whole of "the game starts as soon as I pair": lizard mode
  types Enter when A is pressed, and the door took Enter as "start now".
  Anything with a keyboard shortcut runs `hush.Hush()` for its own lifetime. Over Bluetooth every device's `Phys` is the
  *adapter's* address (the phone's media keys share it with the pad), so
  siblings are matched by `Uniq`, never by `Phys`.
- padmap grabs every pad for the length of a `begin` session, so the
  assignment screen answers no button but a long hold on an already-seated
  pad. Prefer `seating` mode (no grab) for anything on the grid.
- The picker `execvp`s into the game: its pid survives the hop, which is why
  the daemon follows it and the launcher's `--follow $$` is the same pid.
- **Latency is a thing the tests measure.** `tests/e2e` times a press from
  the source's `write()` to the clone's `read()`: under a frame (16.7 ms),
  against 0.03 ms seen in practice. Seating open used to cost ~108 ms of it
  and `gotg-seat` closed seating before the game to get it back; padmap
  throttled that scan, the strict xfail turned into an XPASS, and the close
  is gone -- a pad switched on mid-level can take a seat again.
- **Readying up is per person, pairing is per pad, and neither is the
  other.** Everybody seated holds A for a second and a half -- the same length
  pairing takes, one ring each, and the room goes when the last one closes
  (`ready.py`). Both the pause before
  a hold counts and the hold itself are per seat: measured across the room,
  as they were, two people holding A locked each other out for good, because
  the room was never quiet for either of them. A seated keyboard readies with
  Enter held, for the same reason it has a seat at all.
- **How long pairing takes is ours to ask for.** `theme.timeouts.pair_hold`
  (1.5 s) rides on every `seating` as `hold`, and `PADMAP_HOLD_SECONDS` is
  exported for the daemon so padmap's own wizard takes the same length.
  padmap's default is 0.25 s, which claimed a seat for anybody picking a
  controller up. An older daemon ignores the field and keeps its quarter
  second, so the e2e measures the length through the daemon rather than
  trusting that it was sent.
- A clone's identity is `mirror` unless an environment's `padIdentity` says
  otherwise (`src/client/env/lib.nix` -> `pads.json` -> `padmap_identity`).
  Only the decompiled ports ask for `xbox360`, and `padmap.sh` refuses it for
  Ryujinx: every clone is one GUID under it and Ryujinx blanks the name CRC
  to make its device id. Applying it clears `PADMAP_SKIP_DAEMON_CHECK`,
  because the picker's daemon was started mirrored and padmap only replaces
  a differently-identified daemon when it is asked.
- Emulator port bindings must be written *after* the gate, from padmap's
  published clones (`pads_seating` takes them by GUID). Written before it,
  they name raw pads that `padmap-rs exec` then hides -- a seated
  controller, dead in the game. `gotg-pads` (SDL3) reports a clone's GUID
  exactly as padmap's `env.sh` does, even when SDL renames the clone.
- With Steam running, a pad that appeared in the last ~second is grabbed by
  Steam and a hold on it reaches nobody; tests hold again, people do too.
- The picker Steam launches is a built copy — `gotg steam picker` refreshes
  it. `.envrc` auto-rebuilds only on `src/client` changes, never `src/ui`.
- **padmap and the drawings name controls differently.** A profile answers
  `leftshoulder`; an N64 drawing calls the same thing `L`, and padmap spells
  a stick direction `leftstick_left` where the ares table spells it `leftx-`.
  `bindings.pad_controls(console)` is the translation, read off the table that
  binds them. Without it a press lit nothing, silently, for a day.
- **A stale profile is invisible from in here.** A `gotg-seat` three days old
  had no ready-up door, so a pad was seated and the game started at once
  while every test in this repo passed — they all run the working tree.
  Guarded by the shell's `GOTG_SEAT`/`GOTG_BIN` and `.envrc`'s rev check.
- Every exit from `gotg-seat` says why on stderr (`gotg-seat: no door:
  skipped, 0 seated`) and in the trace. It used to `return 0` in silence,
  which on a terminal is indistinguishable from the gate never running.
- **"It feels laggy" was pacing, not Python.** `GOTG_UI_FPS=1 gotg-ui` prints,
  once a second, what a frame cost: `draw` (this program), `present` (SDL
  putting it on the panel), `idle` (the rest before the next frame), and the
  worst frame -- plus a line naming the renderer, driver and refresh rate. A
  real evening's trace: draw 1.4 ms, present 1.1 ms, 62 fps on a 165 Hz panel.
  A present that short is not waiting for the panel: pygame's plain window
  accepts `vsync=1` and ignores it, so `clock.tick(60)` timed the frames and
  they landed two and three refreshes apart. `display.py` presents through an
  SDL renderer (real vsync, linear scaling -- nearest at a 2.7x factor makes
  motion step unevenly on its own) and `pace.py` makes every frame the same
  whole number of refreshes (165 Hz -> every second one), drops to ten frames
  a second when nothing moves, and wakes on the first event. Images load with
  `display.image`, never `convert_alpha()`, which needs a display surface the
  renderer's window does not have. Cover art decodes on a worker
  (`decode.py`): ten covers in one frame was 13.5 ms.
- **The overlay over a game is the kill switch's painter.** `gotg-killswitch`
  (launched beside every game) watches the exit chord and padmap's socket;
  a bar comes down for a pad joining and for the exit hold. Drawing is a
  separate process -- `gotg-killswitch --paint`, fed ~100-byte frames over a
  non-blocking pipe -- because a display call that stalls (a round trip, a
  vsynced present, a connect) in the chord's own loop was a kill switch that
  did nothing. Three ways over a game: gamescope's `GAMESCOPE_EXTERNAL_OVERLAY`
  (one slot, shared with mangoapp), layer-shell's overlay layer (sway draws
  it above fullscreen), and an override-redirect X11 window (cage, which QA
  uses, has no layer-shell). `gotg qa <id> --overlay-at N` has a pad
  join (a stand-in padmap socket) and the virtual pad hold the chord short of
  the kill, N seconds in, and grades the recording for it; headless sway and cage
  with `grim` check it locally without a window on anybody's screen.
- When the picker does something on a real machine the tests do not show:
  `GOTG_UI_TRACE=/tmp/gotg-trace.log gotg-ui`, ask the person to press the
  buttons in a numbered order, then read the file (one JSON object per
  line: pads opened and whether they are clones, every press and its
  verdict, padmap events and commands, keyboard nodes held). Two real bugs
  were found that way in one evening; neither reproduced with fake pads.
- The Deck answers `ssh deck@192.168.0.80` from here with no password (it
  also has a tailnet name, `steamdeck`/100.80.53.67). Nothing of GOTG's is
  installed on it -- no `gotg`, no `padmap` -- so an on-device check means
  copying the modules over and running them against its real `/proc` and
  `/sys`, which is how `tests/ui/fixtures/input-devices-steam-deck.txt` and
  the `hidraw-steam-deck/` tree were captured.
- **A Deck calls itself a Steam Controller.** Its built-in controls report
  `Vendor=28de Product=1205` under the name `Valve Software Steam
  Controller`, character for character what a Puck reports, so no name rule
  can draw it as a handheld. `config/icons.yaml` has an `ids:` table, tried
  before the names, and `gotg_ui/devices.py` resolves a seat's `node` to
  `vendor:product` -- through `/proc/bus/input/devices` for an evdev node and
  `/sys/class/hidraw/*/device/uevent` for the hidraw-only pads (a Deck and a
  Steam Controller have no joystick evdev node at all). With Steam Input
  running, the Deck's controls arrive instead as an anonymous
  `Microsoft X-Box 360 pad 0` (28de:11ff) with no phys or uniq -- there is
  nothing there to tell it from a real Xbox pad, and it draws as one.
