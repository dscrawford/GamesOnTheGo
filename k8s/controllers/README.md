# Running the controller suite on the cluster

`tests/e2e` in a pod, so it stops running on the machine somebody is using.
The tests make real pads through `/dev/uinput` and start real padmap daemons
against them; on a desktop that means a fake pad can take a seat in a game
being played, which is exactly what happened once.

## Build and push

```bash
nix build .#controllers-image --max-jobs 2 --cores 4
skopeo copy --dest-cert-dir=/tmp/regcerts --dest-tls-verify=false \
  docker-archive:result docker://192.168.0.2:30500/gotg-controllers:0.1.1
```

The push goes to a node's address; the Job pulls `localhost:30500`, which is
the same NodePort registry under the name the nodes' containerd trusts. The
desktop's `/etc/docker/certs.d` holds a client key only root can read, hence
the cert dir of its own.

The image carries the suite, `src/ui`, `config/` and padmap — a pod has no
checkout — and runs `gotg-test-controllers`, the same entry point a desktop
uses, with `GOTG_DEV_ROOT=/gotg`.

## A run

```bash
sed 's/@TAG@/0.1.1/' k8s/controllers/job.yaml | kubectl apply -f -
kubectl -n default logs -f job/gotg-controllers
```

The pod's exit status is the verdict. `GOTG_E2E_REQUIRE=1` is baked in, so a
pod that cannot reach `/dev/uinput` fails rather than skipping.

One test, by editing `args`:

```bash
sed -e 's/@TAG@/0.1.1/' \
    -e 's/args: \["-q"\]/args: ["-q", "-k", "lights_the_label"]/' \
    k8s/controllers/job.yaml | kubectl apply -f -
```

## What is different in a pod

- **No udev.** SDL asks libudev for the joystick list, there is no `/run/udev`
  to answer, and it reports no controllers at all — eighteen tests failed with
  "SDL never saw the pad". The image sets `SDL_JOYSTICK_DISABLE_UDEV=1` when
  `/run/udev` is missing, which turns on SDL's own `/dev/input` scan.
- **Privileged.** containerd's device cgroup denies `/dev/uinput` to an
  unprivileged container whatever the node's permissions say. Same trade the
  QA job takes, on the same private cluster.
- **The wizard tests do not pass here yet.** Eight of the thirty-four —
  everything that walks padmap's capture wizard through a gate subprocess —
  fail with "the wizard never finished", and padmap's states show `ready`
  without a mapping run: in a pod it seats the fake pad as already configured,
  so the gate never asks. The other twenty-six pass, including the whole
  controller requirement and the press-to-light-a-label set. Chasing that
  difference is the next thing here.
