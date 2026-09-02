# QA platform plan: headless game checks on the cluster

Goal: every catalog entry can be spun up headlessly, poked with a virtual
controller, and graded on three axes — controller works, audio is fine,
graphics look normal — first on demand, later as a sweep over the whole
library. Research behind every choice here:
[research/2026-09-01-game-qa-platform.md](research/2026-09-01-game-qa-platform.md).

## Decision

Containers on the existing cluster, not VMs. A pod with a headless Wayland
compositor, a PipeWire null sink, and a uinput virtual pad is a complete fake
gaming rig; node3's time-sliced 1080 Ti (`nvidia.com/gpu-0`, already
configured) covers the consoles that need real GL/Vulkan, and the CPU nodes
cover 8/16-bit under llvmpipe. VMs lose on every axis for gameplay QA: no
audio API in the NixOS test driver, no GPU sharing into VMs on a GeForce
card, llvmpipe too slow for N64+. The dotfiles do **not** grow a
virtualization layer for this. (nixosTest keeps a narrow role in Phase 4.)

## Architecture

One QA run = one pod (or one local invocation — same harness):

```
┌─ qa pod ──────────────────────────────────────────────┐
│ harness (python, in this repo)                        │
│  1. pipewire + pipewire-pulse, null sink "qa"         │
│  2. inputtino virtual Xbox pad   ← BEFORE emulator    │
│  3. cage (WLR_BACKENDS=headless) → gotg play <id>     │
│  4. capture: wf-recorder/ffmpeg video + qa.monitor wav│
│  5. input script: wait, Start, stick movement, waits  │
│  6. analyze → verdict.json + artifacts                │
└───────────────────────────────────────────────────────┘
  GPU tier: node3, resources.limits."nvidia.com/gpu-0": 1
  CPU tier: any node, LIBGL_ALWAYS_SOFTWARE llvmpipe
```

Checks (all thresholds start coarse; ares input scripts are timing-based, so
no exact-frame assertions):

| Axis | Check | Fail means |
|---|---|---|
| boots | emulator process alive + window appears + first frame not black (`blackdetect d=2`) | env/ROM broken |
| controller | video not frozen during scripted-input window (`freezedetect n=-60dB:d=2`) + rumble callback if game rumbles | pad not detected / mapping wrong |
| audio | non-silent (`silencedetect n=-50dB:d=2`), no mid-signal zero-RMS windows, click-count under threshold | sink routing, crackle (the ddbba26 class) |
| graphics | pHash of frame at fixed T vs per-game golden (Hamming ≤ 8); no golden → VLM single-frame pass/fail | render corruption/regression |

## Phases

### Phase 1 — harness, locally (the actual product) — DONE 2026-09-01
Shipped as `gotg qa <id>` (src/client/lib/cmd-qa.sh, qa-analyze.sh,
src/client/qa/, flake attr `qa-tools`). Verified end-to-end on the desktop:
gb Pokémon Yellow and n64 Diddy Kong Racing both pass all five axes
headlessly, with the pad navigating menus on its own. Findings from the
shakedown, encoded in the code comments where they bit:
- cage headless + XWayland + NVIDIA renders real GL fine, but the
  fullscreen handoff is racy → QA pins windowed (GOTG_FULLSCREEN=0).
- pipewire stream-restore overrides PULSE_SINK → a router moves the
  session's sink-inputs onto the QA sink and pins their volume.
- ffmpeg's pulse input records silence from monitor sources on this
  pipewire; parecord hears them → parecord is the recorder.
- It caught two real bugs on day one: the stale env-gb root predating the
  --system pin (qa now env_refreshes first), and 2ship2harkinian's O2R
  first-run dialog.

The O2R dialog turned out un-automatable headlessly, and the finding is worth
recording. HarbourMasters ports (SoH, 2ship) extract a multi-MB .o2r from the
ROM on first run behind a zenity "Generate now?" prompt. It cannot be answered
without a human:
- zenity's discovery ignores PATH and the derivation's `zenity` input (the
  compiled binary is byte-identical across an override; the real path is
  resolved by a mechanism independent of both), so a stub can't shadow it.
- Under the headless `cage` kiosk the dialog is a second toplevel the
  compositor won't route input to, and `WLR_LIBINPUT_NO_DEVICES=1` blocks
  input to windows entirely — a uinput keyboard reaches SDL (the pad works)
  but not GTK.
The archive is derived from the ROM, not from play, so QA seeds it: any .o2r
in the machine's real env state is copied into the run's isolated scratch
state (`qa_seed_bootstrap`), skipping the dialog while keeping saves isolated.
A game never bootstrapped on this machine is stopped before it hangs, with one
line telling the user to `gotg play <id>` once (`qa_require_bootstrap`).
Verified: usa.legend_of_zelda_majoras_mask passes all five axes headlessly —
the pad drives the intro through to the file-name entry screen.

Grading learned from the harkinian run: black is graded only within the input
window (N64 titles boot through seconds of legit black/logo screens), and exit
status 137 (SIGKILL after a trapped SIGTERM, how these ports exit) counts as a
clean stop alongside 0/124/143.
All the value is in the harness; the cluster is just a place to run it.
- `src/gotg/qa/`: session setup (pipewire, null sink, cage, pad), capture,
  analyzers, `verdict.json` writer. New CLI entry: `gotg qa <id>`.
- Virtual pad: inputtino Python bindings; fall back to a hand-rolled
  python-evdev uinput Xbox-360 clone if packaging inputtino for nixpkgs is
  a slog (the evdev route is ~100 lines and battle-understood).
- Input scripts: per-platform defaults (wait boot → Start → 5s stick wiggle
  → settle), overridable per game id under `src/gotg/qa/scripts/`.
- Golden frames: `gotg qa <id> --bless` records the reference screenshot.
- Runs on the desktop first (real GPU, real eyes on the artifacts while
  tuning thresholds). Exit criteria: correct verdicts on one known-good and
  one deliberately-broken game per tier (e.g. snes + n64).

### Phase 2 — containerize, run on node3 — IMAGE DONE 2026-09-02
`nix build .#qa-image` (src/client/qa/image.nix, entrypoint.sh) plus the
manifests and runbook in [k8s/qa/](../k8s/qa/README.md). Verified locally
under docker with **no host /nix mount and an empty state volume**: gb
Pokémon Yellow passes all five axes in-container. What the containerizing
actually turned on:
- **Emulator envs are baked into the image**, not built at runtime: a pod's
  nix store is a read-only image layer, so `gotg qa` can never build there.
  The entrypoint links them into the state volume's `roots/`, and
  `qa_tools_ensure` now no-ops when the tools are already on PATH.
- **A software GL stack (mesa/libglvnd/vulkan-loader) had to go in.** Without
  it the container has no GL at all — cage falls back to pixman, Xwayland to
  sw accel, and the emulator gets no context: a one-second capture. It is the
  CPU tier's whole renderer; the GPU tier's NVIDIA userspace arrives via CDI.
- **The QA python is now `gotg-qa-python`.** The client ships a python3 (vdf)
  and qa-tools shipped another (evdev); whichever won PATH decided whether the
  pad could be created. In the image the client's won and pad.py died on
  `import evdev`.
- `/dev/uinput` needs `privileged: true` — containerd's device cgroup refuses
  it to an unprivileged container regardless of node file permissions.

Not yet done: an actual Job on node3 (needs the image pushed to the in-cluster
registry), and therefore the GPU tier under CDI is still ungraded — everything
above was measured on llvmpipe.

### Phase 2 (original plan) — containerize, run on node3
- Nix-built OCI image (`dockerTools` / `nix2container`) with the harness;
  game env comes from the same flake logic the client already uses, ROM
  fetched from the in-cluster service (the credentials problem is already
  solved — it's the same catalog API).
- K8s Job spec: `runtimeClassName: nvidia`, `nvidia.com/gpu-0: 1`,
  `/dev/uinput` via hostPath + `securityContext` (or extend
  generic-cdi-plugin's device list later if privilege bothers us).
- Artifacts (video, wav, frames, verdict.json) to a Longhorn PVC.
- CPU-tier variant: same image, no GPU resource, `LIBGL_ALWAYS_SOFTWARE=1`.
- Exit criteria: `kubectl create job qa-usa-majoras-mask` produces a correct
  verdict + watchable artifacts.

### Phase 3 — the sweep
- A small dispatcher (CronJob, same pattern as the indexer) walks the
  catalog and fans out Jobs: CPU tier wide across node1/2, GPU tier queued
  on node3's slices (bump `gpu-0` replicas if Jellyfin + 2 QA pods coexist
  fine; no memory isolation, 11 GB card, retro emulators are small).
- Results table in the service (or a flat JSON the UI can render): per game,
  per axis, last-checked, artifact links. Regressions = verdict flips.
- Optional: VLM check (Claude, single frame per game) only for games missing
  goldens, batched.

### Phase 4 — client integration tests via nixosTest (separate track)
- What VMs are actually good at here: `testers.runNixOSTest` with
  `enableOCR`, llvmpipe virtio-gpu, driving the *client* — install flow,
  launcher generation, "frontend boots and menu text appears" via
  `wait_for_text`. Lives in `checks.` of this flake, runs anywhere with
  /dev/kvm (CI, or cluster Jobs via a kvm device plugin if wanted).
- Explicitly not for gameplay/audio/controller QA.

### Phase 5 (optional) — watch a failing session live
- Selkies sidecar (k8s-native, browser) or the existing sunshine.nix mod
  pointed at the QA session for interactive debugging. Streaming is a debug
  add-on, not the platform.

## Risks / open questions

- **wlroots headless on NVIDIA in a container**: cage+EGL on the 1080 Ti via
  CDI should work but is the least-verified link; fallback is Xvfb (+
  VirtualGL for GLX apps), which is boring and known-good. Decide in Phase 1
  on the desktop.
- **ares determinism**: no input-replay facility → coarse assertions only.
  If flake rate is annoying, RetroArch cores with BSV replay could serve as
  a deterministic *reference* rig for the same ROMs (not the shipping
  emulator, so lower value — only if needed).
- **Ryujinx/Dolphin under time-sliced GPU**: two heavy Vulkan apps sharing a
  slice with Jellyfin transcodes is untested; may need to serialize GPU-tier
  jobs (queue depth 1) at first.
- **/dev/uinput in pods**: needs privileged-ish hostPath; acceptable on a
  private cluster, revisit with a device plugin if it spreads.
