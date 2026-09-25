# Running the controller suite on the cluster

`tests/e2e` in a pod, so it stops running on the machine somebody is using.
The tests make real pads through `/dev/uinput` and start real padmap daemons
against them; on a desktop that means a fake pad can take a seat in a game
being played, which is exactly what happened once.

## A run

```bash
nix run .#controllers-cluster                        # the whole suite, one pod per node
nix run .#controllers-cluster -- -k lights_the_label # pytest's arguments, in every pod
nix run .#controllers-cluster -- --shards 1 -k x     # one pod
nix run .#controllers-cluster -- --durations         # and rewrite tests/e2e/durations.json
```

`run.py` builds the image behind one out-link (`~/.cache/gotg/controllers-image`,
so the last image is the only one held from garbage collection), pushes it
only if the registry lacks the tag, starts the Job, follows every pod's log
into `~/.cache/gotg/controllers-runs/<tag>-<time>/share-N.log`, and prints
each share's verdict, its failures and the slowest tests. The exit status is
the verdict. `GOTG_E2E_REQUIRE=1` is baked in, so a pod that cannot reach
`/dev/uinput` fails rather than skipping.

**Split by node.** The Job is Indexed, one pod per node
(`podAntiAffinity` on `gotg.dcrawford/uinput`), and each pod runs the share
`tests/e2e/shards.py` gives it: balanced on `durations.json`, longest first.
The tests cannot share a node -- every fake pad is a real device there, and a
test's daemon would seat another test's pads -- which is also why k8s/qa
carries the same label: its virtual pad would join these tests. A share with
no free node waits, and the run says so. Refresh the timings with
`--durations` after adding or slowing tests; a test with no timing counts as
the median.

**Tag by content.** The tag is the image's store hash. Reusing `0.1.4` once
cost an evening: a node still had that tag from an earlier session and ran a
test file that had since been renamed. With content tags `IfNotPresent` is
safe, and the image is built from only what the suite reads (`tests/e2e`,
`src/ui`, `src/client/data`, `config`), so an edit anywhere else is the same
image, already pushed, already on every node.

The push goes to a node's address; the Job pulls `localhost:30500`, which is
the same NodePort registry under the name the nodes' containerd trusts. The
desktop's `/etc/docker/certs.d` holds a client key only root can read, hence
the cert dir of its own.

The image carries the suite, `src/ui`, `config/` and padmap — a pod has no
checkout — and runs `gotg-test-controllers`, the same entry point a desktop
uses, with `GOTG_DEV_ROOT=/gotg`.

## What is different in a pod

- **No udev.** SDL asks libudev for the joystick list, there is no `/run/udev`
  to answer, and it reports no controllers at all — eighteen tests failed with
  "SDL never saw the pad". The image sets `SDL_JOYSTICK_DISABLE_UDEV=1` when
  `/run/udev` is missing, which turns on SDL's own `/dev/input` scan.
- **Privileged.** containerd's device cgroup denies `/dev/uinput` to an
  unprivileged container whatever the node's permissions say. Same trade the
  QA job takes, on the same private cluster.
- **The wizard tests do not pass here yet.** Five of the fifty —
  everything that walks padmap's capture wizard through a gate subprocess —
  fail with "the wizard never finished", and padmap's states show `ready`
  without a mapping run: in a pod it seats the fake pad as already configured,
  so the gate never asks. The other forty-four pass, including the whole
  controller requirement and the press-to-light-a-label set. Chasing that
  difference is the next thing here.
