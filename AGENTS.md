# Working in this repository

## Builds and tests share the desktop

This machine is somebody's workstation, not a build farm. A Nix build here
runs in `nix-daemon` with `cores = 0` — every core it can find — and padmap,
a flake input, is a Rust crate that recompiles whenever its pin moves. Two of
those at once took all 24 cores and stopped the person at the keyboard.

- **Never run two Nix builds or checks concurrently.** One at a time, and
  wait for it.
- **Cap every Nix command:** `--max-jobs 2 --cores 4` (up to `--max-jobs 3`).
  A `nice` or a `systemd-run` scope on the shell does *not* reach the daemon's
  builders; these flags do.
- **`nice -n 19` anything heavy that runs outside Nix** — pytest, cargo,
  ffmpeg.
- **No permission is needed** to rebuild padmap or run the checks under
  those caps. Without them, do not start.
- Work that does not need this machine's hardware belongs on the cluster
  (`node1-3`, see `k8s/`) or a remote builder when one is configured. The
  controller e2e (`tests/e2e`) does need it: `/dev/uinput` and a real
  padmap daemon.

## The controller requirement

Two things drive the picker: the keyboard, and a controller padmap has
published. Nothing else. A controller is published by being held, from
wherever the picker or game is — never from a screen somebody had to find.
Every session — picker or game — starts with nobody seated.

That is enforced by `tests/e2e/test_controllers.py` against a real daemon
and real kernel devices:

```
nix run .#test-controllers --max-jobs 2 --cores 4 -- -q
GOTG_E2E_REQUIRE=1 ...          # a machine that cannot run it fails, not skips
```

Run it after any change to `src/ui/gotg_ui/{pads,clones,assign,gate,padmap}.py`,
`src/client/lib/padmap.sh`, or the padmap pin.

## Everything else

- Unit suites: `PYTHONPATH=src/ui:src python -m pytest tests/service tests/indexer tests/ui -q`
  (no pygame in that venv, by design — drawing is not tested there).
- Client (bash) suite: `nix build .#checks.x86_64-linux.client-tests --max-jobs 2 --cores 4`.
  `bats tests/client/*.bats` from the shell needs a built `share/gotg` and
  will not work bare.
- A flake sees only git-tracked files. A new module that is not `git add`ed
  is silently absent from every build.
- Requests to padmap go in `docs/requests/`, one file each, mirrored into
  `~/Documents/padmap/docs/requests/` where they get answered.
