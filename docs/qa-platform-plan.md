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

**Running on the cluster — DONE 2026-09-02.** Pushed to the in-cluster
registry and run as Jobs in `default` on node3: gb Pokémon Yellow and n64
Diddy Kong Racing both pass, fetching their ROM from the in-cluster library
service and writing artifacts to the Longhorn volume. What the cluster added
on top of the container work:
- **No `runtimeClassName: nvidia`.** This cluster injects the driver through
  CDI (containerd `enable_cdi` + generic-cdi-plugin); naming a runtime class
  wedges the pod on "no runtime for nvidia is configured".
- **The catalog lives behind `gotg-library`, not `gotg-api`** — the latter is
  proxy-only in-cluster and answers `/catalog` with 503.
- **`/etc/nsswitch.conf` had to be written into the image.** Without it glibc
  has no name-service config and every DNS lookup fails, so the catalog fetch
  fails against a service a port-forward reaches fine.
- **The credentials are staged, not mounted in place.** A secret projects its
  entries as symlinks, so the token file reads as mode 0777 whatever
  `defaultMode` says, and the client rightly refuses to send a token it found
  world-readable — the run then 401s. The entrypoint copies them and fixes
  the mode.

**Why the emulator is still on llvmpipe — root cause, 2026-09-02.** Traced to
the end, and the answer is on the node, not in the image.

`/run/opengl-driver/lib` is a symlink farm into the host's nix store. This
cluster's CDI mounts the driver package (`nvidia-x11-580.142`) but **not the
EGL external-platform packages**, so those symlinks dangle:

| library | target | mounted |
|---|---|---|
| `libEGL_nvidia.so.0` | `nvidia-x11-580.142` | yes |
| `libGLX_nvidia.so.0` | `nvidia-x11-580.142` | yes |
| `libnvidia-egl-gbm.so.1` | `nvidia-egl-external-platforms` → `egl-gbm-1.1.3` | **no** |
| `libnvidia-egl-xlib.so.1` | `nvidia-egl-external-platforms` → `egl-x11` | **no** |

That is exactly the split we see: the compositor uses `libEGL_nvidia` and gets
the card, while Xwayland's glamor needs the GBM external platform, cannot
`dlopen` it, and falls back to software — taking the emulator with it.

Proven in-pod with a ctypes probe. With `libgbm` added to the image (nixpkgs
split it out of mesa, and without it `gbm_create_device` cannot even be
called), the `15_nvidia_gbm.json` this image now carries, and the two missing
store paths bind-mounted by hand:

```
gbm_create_device -> ok   backend: nvidia
eglGetPlatformDisplay -> ok
eglInitialize -> 1 (EGL_SUCCESS), EGL 1.5
```

So the recipe works. Two things still stand in the way of turning it on:
- It also needs `__EGL_VENDOR_LIBRARY_FILENAMES` pinned to NVIDIA, or glvnd
  hands the GBM display to mesa, which rejects an NVIDIA gbm device
  ("gbm device using incorrect/incompatible backend"). Pinning it globally
  then breaks *ares*, which needs the X11 external platform — `egl-x11`,
  dangling for the same reason. A 60s run captures 1.5s of nothing.
- Hand-mounting host store paths into the Job is not a fix worth committing:
  the hashes change with every driver update.

**The real fix is one node3 option**, `hardware.nvidia-container-toolkit.mounts`,
adding the external-platform closure to the CDI spec so every GPU pod sees a
complete driver. That is a dotfiles change and a rebuild of a cluster node, so
it is not done here — it needs a maintenance window, since node3 also runs
Jellyfin.

**Outcome after the node3 CDI fix, 2026-09-04.** The mounts landed and did
exactly what the probe predicted: Xwayland's glamor now initialises on the
card through GBM. What that turned out to buy an X11 emulator is nothing —
`glxinfo` inside the session answers "couldn't find RGB GLX visual or
fbconfig" under every vendor setting tried (default, `__GLX_VENDOR_LIBRARY_NAME
=nvidia`, all five NVIDIA platform JSONs on the config path), and NVIDIA's own
EGL refuses the X11 platform (`eglInitialize failed`). So ares, which draws
through GLX, got no context at all and a 60s run captured one second — a
*regression* from the software path that had been passing by accident of
glamor failing. Measured on node3's 580-branch driver with the 1080 Ti; the
desktop's 610 branch on a 3080 Ti gives ares an NVIDIA context under the same
cage, so this is the driver branch, not the harness.

The image now pins `Xwayland -glamor off` (a `WLR_XWAYLAND` wrapper), which
makes the X11 path deterministic rather than accidental: the compositor
composites on the GPU, the emulator renders on llvmpipe, and both tiers pass.
Accelerating an X11/GLX emulator through Xwayland is closed on this driver;
the accelerated route, if wanted, is a Wayland-native EGL client (cage proves
NVIDIA EGL works on the Wayland platform here) or a newer driver branch. A Job
can point `WLR_XWAYLAND` back at the plain binary to try again.

**The GPU tier is half-done, and worth being precise about.** With a
`nvidia.com/gpu-0` slice the compositor does reach the card (EGL vendor
NVIDIA, GL renderer "NVIDIA GeForce GTX 1080 Ti"), because CDI mounts the
host userspace at `/run/opengl-driver` and the image now searches there ahead
of mesa. But **Xwayland's glamor still fails to initialize on NVIDIA**
("eglInitialize() failed / Disabling GLAMOR"), and the emulator renders
through Xwayland — so the game itself is still on llvmpipe while cage
composites on the GPU. Setting `GBM_BACKENDS_PATH` and
`__GLX_VENDOR_LIBRARY_NAME=nvidia` is the obvious next move and was measured
to make it strictly worse: it removes the software fallback without replacing
it, and a 60s run captures one second of nothing. Getting the emulator onto
the card (native-Wayland SDL, or a working Xwayland/NVIDIA GBM path) is the
open piece of Phase 2 — until then every verdict, on either tier, grades a
software renderer.

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

### Deferred: image size
The image carries nixpkgs' stock `mesa`, which builds all 24 gallium and 12
vulkan drivers so it can serve every GPU. This image only ever loads
llvmpipe/lavapipe — the CPU tier's renderer and the GPU tier's fallback — so
`mesa.override { galliumDrivers = [ "llvmpipe" ]; vulkanDrivers = [ "swrast" ]; }`
would drop most of a 273MB dependency out of a ~934MB image. Not taken yet:
it means building mesa from source (no cache hit), a mesa unique to this
image on every node, and that rebuild again at each nixpkgs bump. Worth doing
when image pull time actually hurts, not before.

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
