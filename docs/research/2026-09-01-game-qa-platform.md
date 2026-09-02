# Automated game QA on the cluster: Research Report

*Generated: 2026-09-01 | Sources: ~45 | Confidence: High on architecture choice, Medium on per-tool caveats*

Question: how to spin up GOTG games headlessly on the k8s cluster (CPU nodes +
node3's GTX 1080 Ti) and automatically check that controllers work, audio is
fine, and graphics render normally. VMs were on the table because "kubernetes
is not great with displays."

## Executive Summary

The display problem is solved without VMs: a headless Wayland compositor (or
Xvfb), a PipeWire/PulseAudio null sink, and a uinput virtual gamepad turn any
container into a complete fake gaming rig — this is exactly how Wolf
(games-on-whales) streams monitor-less desktops from Docker today. No mature
"game QA farm on k8s" product exists; the space is assembled from parts, and
every part is proven. VMs are the *worse* route for this workload: the NixOS
test framework has no audio API and no mouse, consumer GPUs cannot be shared
into VMs (no vGPU/SR-IOV; whole-GPU vfio passthrough would take the 1080 Ti
away from its current pod workloads), and llvmpipe software rendering cannot
sustain N64-era emulation. Containers on the existing cluster — with the
already-configured `nvidia.com/gpu-0` time-slicing for the GPU tier and plain
CPU pods with llvmpipe for the 8/16-bit tier — are the efficient path.

## 1. Running games headlessly in containers (the "display problem")

- **Wolf** (games-on-whales, 2.1k★, active) demonstrates the full pattern:
  on-demand virtual desktops "without the need for a monitor or a dummy plug"
  via `gst-wayland-display` (a Smithay micro-compositor exposed as a GStreamer
  element), inputtino uinput gamepads, and a PulseAudio sidecar
  ([wolf](https://github.com/games-on-whales/wolf),
  [gst-wayland-display](https://github.com/games-on-whales/gst-wayland-display)).
  But Wolf drives the *Docker socket* directly; native k8s support is an open
  "help wanted" issue
  ([#82](https://github.com/games-on-whales/wolf/issues/82)), and its k8s
  operator **fenrir** says verbatim "NOT IN A USEABLE STATE!"
  ([fenrir](https://github.com/games-on-whales/fenrir)). Use its architecture,
  not its runtime.
- **Headless compositors** that do run in plain pods:
  - wlroots headless: `WLR_BACKENDS=headless WLR_LIBINPUT_NO_DEVICES=1` with
    **cage** (single-app kiosk, exits with the app — ideal per-game-pod
    lifecycle) or sway; proven in containers/CI
    ([wlroots env vars](https://github.com/swaywm/wlroots/blob/master/docs/env_vars.md),
    [swayvnc](https://github.com/bbusse/swayvnc)).
  - gamescope `--backend headless` exists but has open NVIDIA
    renders-nothing reports
    ([gamescope #1984](https://github.com/ValveSoftware/gamescope/issues/1984)) — avoid on the 1080 Ti.
  - Xvfb (+ VirtualGL for GPU GLX): the legacy path; still what
    selkies-glx-desktop uses; works everywhere including NVIDIA.
- **Selkies** (2.1k★) is the most mature k8s-native *interactive* option —
  unprivileged pods, WebRTC/WebSocket browser streaming, browser-gamepad
  injection — the right add-on when a human needs to watch a failing session
  ([selkies](https://github.com/selkies-project/selkies)).
- **Dead ends** (verified): k8s-at-home GOW chart (deprecated), linuxserver
  steamos/emulatorjs images (deprecated), Netris (experimental, no k8s),
  fenrir (unusable).

## 2. GPU access

- node3 already exposes the 1080 Ti via CDI + generic-cdi-plugin with
  time-sliced resources (`nvidia.com/gpu-0` ×2, `nvidia.com/gpu-all` ×1) —
  the standard oversubscription mechanism (replicas in config; no memory
  isolation, round-robin)
  ([NVIDIA GPU sharing docs](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/24.6.2/gpu-sharing.html)).
  QA pods just request a slice; replicas can be raised for more parallelism.
- NVENC on the 1080 Ti makes in-pod capture/encode cheap.
- For VMs, by contrast: GeForce allows passthrough but **one whole GPU per VM,
  no SR-IOV/vGPU on consumer cards**
  ([TechPowerUp](https://www.techpowerup.com/280454/nvidia-enables-gpu-passthrough-for-virtual-machines-on-consumer-grade-geforce-gpus)),
  and vfio-binding the card removes it from the host/containers entirely.

## 3. The three QA checks (all assembled from proven parts)

**Controllers.** `/dev/uinput` virtual pads are indistinguishable from real
hardware to SDL apps. **inputtino** (games-on-whales; the input layer of Wolf
and Sunshine; Python bindings; emulates Xbox/PS5/Switch pads with rumble
callbacks) is purpose-built
([inputtino](https://github.com/games-on-whales/inputtino)). Critical gotcha:
SDL's udev-based hotplug is unreliable in containers (inotify fallback only
triggers under Flatpak/pressure-vessel markers,
[SDL #3889](https://github.com/libsdl-org/SDL/issues/3889)) — but devices
present at SDL init are found by directly scanning `/dev/input`, so **create
the pad before launching the emulator** and hotplug never matters. Rumble
callbacks double as a reverse-channel assertion (the game talks back to the
pad). The controller check itself is behavioral: inject a deterministic input
script and assert the video is *not* frozen during the input window
(freezedetect doubles as the controller assertion).

**Audio.** `pactl load-module module-null-sink sink_name=qa` (identical under
pipewire-pulse), launch with `PULSE_SINK=qa`, record the monitor:
`ffmpeg -f pulse -i qa.monitor -t 60 qa.wav`
([Arch PulseAudio examples](https://wiki.archlinux.org/title/PulseAudio/Examples)).
Analysis: `silencedetect=n=-50dB:d=2` for no-audio; windowed-RMS scan for
mid-signal dropouts
([Audio-Dropout-Detector](https://github.com/johnblatchford/Audio-Dropout-Detector));
sample-delta click counting as a crackle heuristic (would have caught the ares
zero-latency crackle fixed in ddbba26). `pw-dump | jq` asserting the
emulator's node exists and is streaming is a check by itself. No off-the-shelf
"emulator audio CI" project exists — this is assembled, not adopted.

**Graphics.** Record the virtual display during the run, then:
`blackdetect=d=2` (black screen), `freezedetect=n=-60dB:d=2` (frozen output —
only asserted during the scripted-input window, since title screens
legitimately freeze)
([ffmpeg filters](https://ayosec.github.io/ffmpeg-filters-docs/8.0/Filters/Video/freezedetect.html);
[frame-checker](https://github.com/kuvk/frame-checker) wraps exactly these).
Plus one frame at a fixed timestamp perceptual-hashed against a per-game
golden (Hamming ≤ ~8) — the Dolphin **FifoCI** pattern (record once, assert
forever, [fifoci](https://github.com/dolphin-emu/fifoci)). For games without
goldens: a single-frame VLM pass/fail ("is this rendering normally or
black/garbled?") — published pipelines exist for game-bug frame retrieval at
~$100/whole-evaluation scale
([arXiv 2508.04895](https://arxiv.org/html/2508.04895v1)).

**Determinism caveat.** ares has no public headless input-replay/BSV
equivalent (RetroArch does; ares does not) — input scripts are timing-based
and mildly flaky, so assertions must stay coarse (black/freeze/silence/pHash
threshold), not exact-frame.

## 4. The VM route, assessed honestly

- **NixOS test framework** is the one genuinely attractive VM tool: pure-Nix,
  first-class scripted API — `screenshot()`, tesseract OCR
  (`wait_for_text`), `send_key`, `wait_for_window` — with a real precedent of
  testing a Wayland compositor under llvmpipe
  ([kokada's Hyprland nixosTest](https://kokada.dev/blog/writing-nixos-tests-for-fun-and-profit/),
  [manual](https://github.com/NixOS/nixpkgs/blob/master/nixos/doc/manual/development/writing-nixos-tests.section.md)).
  But (verified in driver source): **no audio API, no mouse**; no GPU in
  guests (llvmpipe can't run N64+ full-speed — accurate N64 RDP needed Vulkan
  paraLLEl-RDP to reach full speed at all,
  [libretro](https://www.libretro.com/index.php/reviving-and-rewriting-parallel-rdp-fast-and-accurate-low-level-n64-rdp-emulation/)).
  QEMU `-audiodev wav` injection is a plausible but unproven workaround.
  Right-sized use: **client integration tests** (install flow, launcher
  generation, frontend boots, menu text appears), not gameplay QA.
- **Where it runs**: a nixosTest is just a Nix build running QEMU — a k8s Job
  with `/dev/kvm` exposed (kvm device plugin /
  [kubevirt #7504](https://github.com/kubevirt/kubevirt/issues/7504)) runs it
  fine; no KubeVirt, no nested-virt problem.
- **KubeVirt**: heavy operator stack, vfio-dedicated GPU, no documented
  virgl/venus acceleration — overkill for three nodes.
- **microvm.nix**: graphics marked experimental, and no test-driver API at
  all — wrong shape for scripted QA.

## Key Takeaways

1. Containers, not VMs, for gameplay QA — every QA-relevant capability
   (display, audio, pads, GPU sharing) is strictly better in pods on this
   hardware.
2. Nothing off-the-shelf does this; the assembled stack is: cage headless +
   PipeWire null sink + inputtino pad + ffmpeg capture/analysis, per pod.
3. GPU tier (n64/gamecube/wii/switch) runs on node3's existing time-slice
   resources; 8/16-bit tier runs on CPU nodes under llvmpipe — the
   mostly-CPU cluster is fully useful.
4. Create the virtual pad *before* emulator launch; that one ordering rule
   eliminates the entire container-hotplug problem class.
5. nixosTest still earns a place — as the client-level integration test rig
   (OCR-driven "does the frontend boot"), runnable as cluster Jobs with
   /dev/kvm only. Don't extend dotfiles with libvirt/microvm for QA.
6. Streaming (Selkies/Sunshine/wayvnc) is a debugging add-on, not the
   foundation — QA needs capture, not low-latency delivery.

## Sources

Full per-claim URLs are inline above; the three underlying research sweeps
covered: container/k8s game streaming (Wolf, Selkies, GOW, gamescope,
wlroots, linuxserver images, NVIDIA/Intel GPU sharing), VM approaches
(nixosTest driver source, microvm.nix, KubeVirt, QEMU virtio-gpu/venus/
gfxstream, consumer vGPU limits), and QA techniques (inputtino/uinput/SDL
internals, PulseAudio/PipeWire capture + analysis, ffmpeg QC filters, FifoCI,
RetroArch BSV, VLM-based game QA papers).

## Methodology

Three parallel research agents (containers/k8s, VMs/Nix, QA techniques), each
6-12 searches + 4-6 deep reads, cross-checked against GitHub repo state via
`gh api`/`gh search` (Wolf/fenrir/selkies READMEs and activity), plus local
cluster inspection (node3 CDI config, time-slice resources, runtime classes).
Explicitly unverified items are marked inline in the agent findings; the ones
that matter for the plan: QEMU wav-capture inside nixosTest (unproven), Wolf
releases newer than v2024.07, vgamepad's Linux backend maturity (use
inputtino instead).
