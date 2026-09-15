# Ryubing with the JIT cache Ryujinx always had.
#
# Ryubing 1.2.82 (PR #615) replaced ARMeilleure's single 2 GB reservation for
# translated code with 256 MB regions, growing when the first fills. The
# region-aware half is incomplete: cache entries are keyed by region-relative
# offset, and Unmap frees into the *active* region's allocator whichever
# region the function was in — so once a second region exists, freeing a
# region-0 function (every lost translation race does) hands region 1 a bogus
# free block, the next Allocate returns live code, and a guest thread faults
# in overwritten JIT output. .NET turns that into an uncatchable
# AccessViolationException and abort(): "JIT Cache Region 0 exhausted,
# creating new Cache Region 1", then a silent SIGABRT. Tears of the Kingdom
# outgrows 256 MB twenty seconds into its opening; three out of three on
# 2026-09-04. Nothing Linux-side is involved (kernel, glibc, W^X, sysctls all
# checked). With the original size the growth path never runs. Reserved, not
# committed, so the 2 GB is address space, which is why upstream ran that way
# for years.
#
# Fixed upstream in Canary 1.3.327 (PR #152 "instanced jit cache",
# 2026-06-30: one allocator across regions, "no longer cause crashes due to
# entry offset collisions"); no stable release carries it as of 2026-09-04
# (stable is 1.3.3, Canary 1.3.351). Drop this override once nixpkgs' ryubing
# is past that.
#
# And the SDL it ships cannot see the controller people have.
#
# Ryujinx bundles its own libSDL2.so — SDL 2.30.0, from the Ryujinx.SDL2-CS
# package — and .NET loads it in preference to anything on the system, because
# the app's deps.json names it and NATIVE_DLL_SEARCH_DIRECTORIES is searched
# before the loader's path. The current Steam Controller is a hidapi device
# with no evdev node at all, and 2.30.0 has never heard of it: its gamepad
# database knows Valve products 1102, 1142, 1201, 1202 and 11fc, and the puck
# is 1304. So the emulator saw no pad, the game stopped on its controller
# screen, and the only way through was to run Steam — whose virtual gamepad
# (28de:11ff) is a device SDL 2.30.0 does know.
#
# nixpkgs' SDL2 is sdl2-compat, the SDL2 API over SDL3, and SDL3 drives the
# puck directly. Measured with a probe against each library in turn, hint on:
# 2.30.0 enumerated nothing, sdl2-compat enumerated the puck as a gamepad. So
# the bundled file becomes a link to that one, and the emulator sees what the
# launcher sees — which is the whole point, since gotg's own pad tools are
# SDL3 and were reading a device the emulator could not.
{ ryubing, SDL2 }:
ryubing.overrideAttrs (old: {
  pname = "ryubing-jit2g";
  postPatch = (old.postPatch or "") + ''
    substituteInPlace src/ARMeilleure/Translation/Cache/JitCache.cs \
      --replace-fail "CacheSize = 256 * 1024 * 1024" "CacheSize = 2047 * 1024 * 1024"
  '';
  postInstall = (old.postInstall or "") + ''
    native=("$out"/lib/*/runtimes/linux-x64/native/libSDL2.so)
    [ -e "''${native[0]}" ] || {
      echo "no bundled libSDL2.so to replace — has the layout changed?" >&2
      exit 1
    }
    ln -sf ${SDL2}/lib/libSDL2-2.0.so.0 "''${native[0]}"
  '';
  # The suite takes as long as the build and asserts nothing about this.
  doCheck = false;
})
