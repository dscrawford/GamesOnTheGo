# GL on a machine that is not NixOS.
#
# On NixOS, /run/opengl-driver carries the GPU userspace and nix-built
# programs just work. On a foreign distro -- SteamOS -- that path does not
# exist and the host's mesa is unloadable from our glibc, so GL, EGL, Vulkan
# and GBM all come up empty: "No RDP rendering support" from ares, "Window
# framebuffer support not available" from the picker, "Failed to create
# allocator" from the split-screen sway, and a QA compositor that falls back
# to drawing in software and records a second of nothing. Ship nixpkgs' own
# mesa and point every loader at it: the nixGL trick, written once.
#
# `exports` is the pointing, unconditionally, for a caller that has already
# decided. `guarded` decides: only when the host provides nothing, or when
# GOTG_FOREIGN_GL=1 asks for it on a machine that has the host path too --
# which is how `gotg qa --machine deck` finds a Deck-only failure without a
# Deck.
{ mesa }:
rec {
  exports = ''
    export LIBGL_DRIVERS_PATH=${mesa}/lib/dri
    export GBM_BACKENDS_PATH=${mesa}/lib/gbm
    export __EGL_VENDOR_LIBRARY_FILENAMES=${mesa}/share/glvnd/egl_vendor.d/50_mesa.json
    export VK_DRIVER_FILES=${mesa}/share/vulkan/icd.d/radeon_icd.x86_64.json:${mesa}/share/vulkan/icd.d/intel_icd.x86_64.json
    export LD_LIBRARY_PATH=${mesa}/lib''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
  '';
  guarded = ''
    if [ "''${GOTG_FOREIGN_GL:-0}" = 1 ] || [ ! -e /run/opengl-driver ]; then
    ${exports}
    fi
  '';
}
