# The QA runner as a container, for the cluster.
#
# It carries nix, and that is deliberate: which emulator a game runs in, and
# with which settings, is decided by src/client/env and built on demand — the
# same path a desktop takes. Baking environments in instead would fork that
# decision into the image, and a QA farm grading a build the desktop never
# runs is grading the wrong thing. The store is a volume, so a platform
# compiles once for the cluster rather than once per run.
{
  lib,
  dockerTools,
  buildEnv,
  writeShellApplication,
  runCommand,
  gotg,
  qa-tools,
  nix,
  bash,
  coreutils,
  dbus,
  pipewire,
  wireplumber,
  cacert,
  gitMinimal,
  shadow,
  gnutar,
  gzip,
  xz,
  mesa,
  libglvnd,
  vulkan-loader,
  # The environments to carry, as { env-gb = <drv>; ... }. A pod has no
  # writable nix store — the image's is a read-only layer — so anything a run
  # might need has to be here already. The cartridge platforms share one ares,
  # so a handful of them cost about as much as the first.
  environments,
}:
let
  # What `gotg play` looks for: a directory of GC-root-shaped names, each with
  # bin/gotg-play. The entrypoint links these into the state volume's roots/
  # so env_is_built finds them without ever calling nix.
  bakedEnvs = runCommand "gotg-qa-envs" { } ''
    mkdir -p $out
    ${lib.concatStringsSep "\n" (
      lib.mapAttrsToList (name: drv: "ln -s ${drv} $out/${name}") environments
    )}
  '';
  entrypoint = writeShellApplication {
    name = "gotg-qa-entrypoint";
    runtimeInputs = [
      gotg
      qa-tools
      pipewire
      wireplumber
      dbus
      coreutils
    ];
    text = builtins.readFile ./entrypoint.sh;
  };

  # nix refuses to run without these, and a container image has neither unless
  # they are written in.
  accounts = runCommand "gotg-qa-accounts" { } ''
    mkdir -p $out/etc
    echo "root:x:0:0:root:/root:${bash}/bin/bash" > $out/etc/passwd
    echo "root:x:0:" > $out/etc/group
    mkdir -p $out/etc/nix
    {
      echo "experimental-features = nix-command flakes"
      echo "sandbox = false"
    } > $out/etc/nix/nix.conf
  '';
in
dockerTools.buildLayeredImage {
  name = "gotg-qa";
  tag = "0.1.0";
  contents = [
    entrypoint
    gotg
    qa-tools
    nix
    bash
    coreutils
    dbus
    pipewire
    wireplumber
    cacert
    accounts
    # nix fetches the flake with git and unpacks what it downloads.
    gitMinimal
    gnutar
    gzip
    xz
    shadow
    # A software GL/Vulkan stack, which is the whole renderer for the CPU tier
    # and the fallback for the GPU one. A container has no /run/opengl-driver,
    # so without these the compositor comes up on pixman, Xwayland drops to
    # software, and the emulator gets no GL context at all — measured, as a
    # capture one second long.
    mesa
    libglvnd
    vulkan-loader
    bakedEnvs
  ];
  config = {
    Entrypoint = [ (lib.getExe entrypoint) ];
    Env = [
      "SSL_CERT_FILE=${cacert}/etc/ssl/certs/ca-bundle.crt"
      "NIX_SSL_CERT_FILE=${cacert}/etc/ssl/certs/ca-bundle.crt"
      "USER=root"
      "HOME=/root"
      "XDG_RUNTIME_DIR=/tmp/xdg"
      # Where the harness keeps runs, goldens and built environments. A volume
      # in the cluster, so a sweep does not rebuild an emulator per game.
      "XDG_STATE_HOME=/state"
      # wlroots has no monitor and must not go looking for one.
      "WLR_BACKENDS=headless"
      "WLR_LIBINPUT_NO_DEVICES=1"
      # Point the loaders at the software drivers by absolute path. The GPU
      # tier's NVIDIA userspace arrives at runtime through CDI, which sets its
      # own discovery up and takes precedence; these are what is left when it
      # does not, and are why a CPU-tier pod renders at all.
      "LIBGL_DRIVERS_PATH=${mesa}/lib/dri"
      "__EGL_VENDOR_LIBRARY_DIRS=${mesa}/share/glvnd/egl_vendor.d"
      "LD_LIBRARY_PATH=${lib.makeLibraryPath [ libglvnd mesa vulkan-loader ]}"
      "GOTG_QA_BAKED_ENVS=${bakedEnvs}"
    ];
    WorkingDir = "/state";
  };
}
