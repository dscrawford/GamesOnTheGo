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
# decided -- the QA tools, which carry their mesa on purpose.
#
# `guarded` decides: only when the host provides nothing, or when
# GOTG_FOREIGN_GL=1 asks for it on a machine that has the host path too --
# which is how `gotg qa --machine deck` finds a Deck-only failure without a
# Deck. And it does not carry the mesa: it names it. A dependency put a
# gigabyte (mesa and its LLVM) into every environment's closure, the
# overlay's and the picker's, on NixOS too, where it is never loaded. `path`
# is the mesa's store path with its context dropped, written down by each of
# them (share/gotg/foreign-gl), and the client fetches exactly that one
# before a launch on the machines that load it (lib/foreign-gl.sh). Exactly
# that one: a mesa from a newer nixpkgs wants a newer glibc than the program
# it is loaded into. Not there -- run out of the store by hand, or a fetch that
# failed -- it says so, rather than leaving a black window to explain itself.
{ mesa }:
let
  path = builtins.unsafeDiscardStringContext "${mesa}";
  pointAt = gl: ''
    export LIBGL_DRIVERS_PATH=${gl}/lib/dri
    export GBM_BACKENDS_PATH=${gl}/lib/gbm
    export __EGL_VENDOR_LIBRARY_FILENAMES=${gl}/share/glvnd/egl_vendor.d/50_mesa.json
    export VK_DRIVER_FILES=${gl}/share/vulkan/icd.d/radeon_icd.x86_64.json:${gl}/share/vulkan/icd.d/intel_icd.x86_64.json
    export LD_LIBRARY_PATH=${gl}/lib''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
  '';
in
{
  inherit path;
  exports = pointAt mesa;
  guarded = ''
    if [ "''${GOTG_FOREIGN_GL:-0}" = 1 ] || [ ! -e /run/opengl-driver ]; then
      if [ -e ${path}/lib/dri ]; then
    ${pointAt path}
      else
        echo "gotg: this machine has no /run/opengl-driver, and the GL GOTG brings for one is not here (${path}); gotg play and gotg sync fetch it" >&2
      fi
    fi
  '';
}
