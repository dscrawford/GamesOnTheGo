# Running QA on the cluster

`gotg qa` in a pod. The harness, the emulator environments and a software GL
stack are all in the image, so a run needs no nix and no network beyond the
service it fetches the game from.

## Build and push

```bash
nix build .#qa-image
skopeo copy docker-archive:result docker://localhost:30500/gotg-qa:0.3.0
```

The image carries the cartridge platforms (gb, gbc, gba, nes, snes, genesis,
n64), which share one ares between them. A platform that is not in it cannot
be graded in a pod — add it to `environments` in `flake.nix` and rebuild,
rather than expecting the pod to build it.

## Once

```bash
kubectl apply -f k8s/qa/state.yaml
kubectl -n default create secret generic gotg-qa-config \
  --from-file=config.json="$HOME/.config/gotg/config.json" \
  --dry-run=client -o yaml | kubectl apply -f -
```

## A run

```bash
sed -e 's/@NAME@/usa-diddy-kong-racing/' \
    -e 's/@GAME@/usa.diddy_kong_racing_rev1/' \
    k8s/qa/job.yaml | kubectl apply -f -

kubectl -n default logs -f job/gotg-qa-usa-diddy-kong-racing
```

The pod's exit status is the verdict: zero when every axis passed. The
captures, the frames and `verdict.json` stay on the state volume under
`/state/gotg/qa/runs/<timestamp>/`.

## Goldens

The graphics axis only has an opinion where a golden frame exists, and a
golden is only compared against a run with the same `--duration` and
`--boot-wait` — the frame a game shows at second N is a function of when the
pad started pressing. Bless one by adding `--bless` to the Job's `args`; it
lands on the state volume and every later run with those timings is graded
against it.

## Tiers

The manifest as written is the GPU tier: `runtimeClassName: nvidia`, pinned to
node3, holding one of the card's two time-slices. For the 8/16-bit platforms
drop `runtimeClassName`, the `nodeSelector` and the `nvidia.com/gpu-0` limit —
the image's llvmpipe renders them, and they schedule anywhere.

## What the GPU tier does and does not accelerate

Asking for a slice gets the *compositor* onto the card — CDI mounts the host
NVIDIA userspace at `/run/opengl-driver`, and the image searches it ahead of
mesa. The *emulator* renders through Xwayland, whose glamor does not currently
initialize on NVIDIA here, so the game itself still runs on llvmpipe. Verdicts
are sound; performance-sensitive ones are not yet meaningful. See
`docs/qa-platform-plan.md` for what was measured and what to try next.

## What needs privilege, and why

The virtual pad is a `/dev/uinput` device, and containerd's device cgroup
refuses it to an unprivileged container whatever the node's file permissions
say. `privileged: true` is there for that one device. Everything else in the
run — the compositor, the recorders, the null audio sink — is ordinary
userspace.

## Games that cannot be graded here

The HarbourMasters ports (Ship of Harkinian, 2 Ship 2 Harkinian) extract their
assets on first run behind a dialog no unattended run can answer. QA copies an
already-extracted archive into the run when the machine has one, so on a
cluster they need that archive seeded onto the state volume first — a pod has
no way to make it. `gotg qa` says so and stops rather than hanging.
