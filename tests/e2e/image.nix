# The controller suite as a container, for Jobs on the cluster.
#
# These tests make real kernel devices through /dev/uinput and run a real
# danstick daemon against them, which is why they were run on the desktop --
# and why they kept getting in the way of the person using it: a fake pad is
# a real pad, and a daemon in seating mode seats it. One evening one landed as
# player two in a game somebody was playing.
#
# A privileged pod has the same /dev/uinput and none of the collateral. The
# image carries the suite itself (a pod has no checkout), the picker's `src/ui`
# it tests, `config/` it reads, and danstick; `gotg-test-controllers` is the same
# entry point a desktop uses, pointed at /gotg instead of a git tree.
{
  lib,
  dockerTools,
  buildEnv,
  writeShellApplication,
  runCommand,
  controllerTests,
  danstick,
  bash,
  coreutils,
  gnugrep,
  procps,
  src,
}:
let
  # The three directories the suite needs, copied rather than referenced: the
  # image is the checkout as far as the pod is concerned, and
  # `gotg-test-controllers` looks for $GOTG_DEV_ROOT/tests/e2e.
  tree = runCommand "gotg-controllers-tree" { } ''
    mkdir -p $out/gotg
    cp -r ${src}/tests $out/gotg/tests
    cp -r ${src}/src $out/gotg/src
    cp -r ${src}/config $out/gotg/config
    chmod -R u+w $out/gotg
  '';

  entrypoint = writeShellApplication {
    name = "gotg-controllers-entrypoint";
    runtimeInputs = [
      controllerTests
      danstick
      coreutils
      gnugrep
      procps
    ];
    text = ''
      set -euo pipefail
      export GOTG_DEV_ROOT=/gotg
      export GOTG_CONFIG=/gotg/config
      export XDG_RUNTIME_DIR="''${XDG_RUNTIME_DIR:-/tmp/xdg}"
      mkdir -p "$XDG_RUNTIME_DIR"
      chmod 700 "$XDG_RUNTIME_DIR"
      export SDL_VIDEODRIVER=dummy
      # No udev in a pod. SDL's Linux joystick backend asks libudev for the
      # device list, gets an empty one -- there is no /run/udev to answer --
      # and reports no controllers at all: eighteen tests failed with "SDL
      # never saw the pad". This turns on its own /dev/input scan instead, and
      # only where udev is genuinely absent, so a desktop is untouched.
      if [ ! -d /run/udev ]; then
        export SDL_JOYSTICK_DISABLE_UDEV=1
      fi
      export PYGAME_HIDE_SUPPORT_PROMPT=1
      # The whole reason to run here rather than on somebody's desktop: a skip
      # is a failure. A pod that cannot reach /dev/uinput has to say so, not
      # report a green run with nothing in it.
      export GOTG_E2E_REQUIRE=1
      # Each test's cost, printed at the end for run.py --durations.
      export GOTG_E2E_TIMINGS=1
      # The latch danstick's client sets after a successful ensure-daemon. Never
      # inherited into a run: each test starts a daemon of its own.
      unset DANSTICK_SKIP_DAEMON_CHECK || true
      # An Indexed Job split across the nodes (tests/e2e/shards.py): this pod's
      # share, from the index Kubernetes gave it.
      if [ -n "''${GOTG_E2E_SHARDS:-}" ] && [ -n "''${JOB_COMPLETION_INDEX:-}" ]; then
        export GOTG_E2E_SHARD="$JOB_COMPLETION_INDEX/$GOTG_E2E_SHARDS"
        echo "gotg-controllers: share $GOTG_E2E_SHARD on ''${NODE_NAME:-this node}"
      fi
      exec gotg-test-controllers "$@"
    '';
  };
in
dockerTools.buildLayeredImage {
  name = "gotg-controllers";
  tag = "latest";
  contents = buildEnv {
    name = "gotg-controllers-root";
    paths = [
      entrypoint
      controllerTests
      danstick
      bash
      coreutils
      gnugrep
      procps
      dockerTools.caCertificates
      tree
    ];
    pathsToLink = [
      "/bin"
      "/etc"
      "/gotg"
    ];
  };
  config = {
    Entrypoint = [ "${entrypoint}/bin/gotg-controllers-entrypoint" ];
    Cmd = [ "-q" ];
    Env = [
      "HOME=/tmp"
      "PATH=/bin"
    ];
    WorkingDir = "/gotg";
  };
  maxLayers = 40;
}
