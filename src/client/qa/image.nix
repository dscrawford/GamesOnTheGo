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
  libgbm,
  libglvnd,
  vulkan-loader,
  xwayland,
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
  # Xwayland with glamor off. Measured on node3 once the CDI mount carried
  # NVIDIA's EGL platforms: glamor comes up on the card through GBM — and
  # then offers X11 clients no GLX visual at all ("couldn't find RGB GLX
  # visual or fbconfig" from glxinfo, every vendor setting tried), so an
  # emulator that draws through GLX gets no context and a 60s run captures
  # one second of nothing. With glamor off, Xwayland's GLX is Mesa's over
  # llvmpipe and every visual is there. The compositor beside it still runs
  # on the GPU; what is pinned here is that the X11 emulators render at all.
  # A Job can point WLR_XWAYLAND back at the plain binary to try again on a
  # driver that does better.
  xwaylandSoftware = writeShellApplication {
    name = "xwayland-glamor-off";
    text = ''exec ${xwayland}/bin/Xwayland -glamor off "$@"'';
  };
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
    # Without this glibc has no name-service configuration at all and answers
    # every DNS lookup with "not found" — the catalog fetch fails against a
    # cluster service that a port-forward reaches fine, which is how this line
    # was earned.
    echo "hosts: files dns" > $out/etc/nsswitch.conf

    # Teach EGL about NVIDIA's GBM platform.
    #
    # Xwayland is where the emulator renders, and its glamor reaches the card
    # through GBM. The driver ships the implementation
    # (libnvidia-egl-gbm.so.1, present in the CDI mount) but this cluster's
    # CDI does not mount the json that advertises it — /run/opengl-driver has
    # no share/egl at all — so EGL never loads the platform, eglInitialize
    # fails, and Xwayland falls back to software while the compositor beside
    # it runs on the GPU. The file is a pointer, not a copy: it names the
    # driver's own library, which is resolved at runtime from the mount.
    mkdir -p $out/etc/egl/egl_external_platform.d
    echo '{"file_format_version":"1.0.0","ICD":{"library_path":"libnvidia-egl-gbm.so.1"}}' \
      > $out/etc/egl/egl_external_platform.d/15_nvidia_gbm.json
    mkdir -p $out/etc/nix
    {
      echo "experimental-features = nix-command flakes"
      echo "sandbox = false"
    } > $out/etc/nix/nix.conf
  '';
in
dockerTools.buildLayeredImage {
  name = "gotg-qa";
  tag = "0.4.0";
  contents = [
    entrypoint
    xwaylandSoftware
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
    # Split out of mesa in nixpkgs, and needed by name: Xwayland creates its
    # GBM device through libgbm.so.1, and without it glamor cannot start at
    # all — "failed to setup GBM backend", with the emulator on llvmpipe
    # while the compositor runs on the card.
    libgbm
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
      # See xwaylandSoftware above.
      "WLR_XWAYLAND=${lib.getExe xwaylandSoftware}"
      # Both renderers, in the order they should win. /run/opengl-driver is
      # what this cluster's CDI injects into a pod with a GPU slice — the
      # host's NVIDIA userspace, matched to the running kernel driver — and it
      # must come first. Pointing these only at mesa (the first version of this
      # file) hid the NVIDIA vendor library, so glvnd handed the card to mesa,
      # mesa answered "driver (null)", EGL failed and the compositor fell back
      # to software: a GPU-tier run that passed while grading llvmpipe. The
      # mesa entries behind them are the CPU tier's whole renderer.
      "LIBGL_DRIVERS_PATH=/run/opengl-driver/lib/dri:${mesa}/lib/dri"
      "__EGL_VENDOR_LIBRARY_DIRS=/run/opengl-driver/share/glvnd/egl_vendor.d:${mesa}/share/glvnd/egl_vendor.d"
      "VK_DRIVER_FILES=/run/opengl-driver/share/vulkan/icd.d:${mesa}/share/vulkan/icd.d"
      "LD_LIBRARY_PATH=/run/opengl-driver/lib:${lib.makeLibraryPath [ libglvnd mesa libgbm vulkan-loader ]}"
      # The GBM backend and the platform config for anything that does reach
      # the card through EGL — the compositor does. Xwayland is kept off it
      # (WLR_XWAYLAND above), and __GLX_VENDOR_LIBRARY_NAME=nvidia is absent
      # on purpose: measured with the platforms mounted, it changes nothing
      # about the missing GLX visuals.
      "GBM_BACKENDS_PATH=/run/opengl-driver/lib/gbm"
      "__EGL_EXTERNAL_PLATFORM_CONFIG_DIRS=/etc/egl/egl_external_platform.d"
      "GOTG_QA_BAKED_ENVS=${bakedEnvs}"
    ];
    WorkingDir = "/state";
  };
}
