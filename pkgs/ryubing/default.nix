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
{ ryubing }:
ryubing.overrideAttrs (old: {
  pname = "ryubing-jit2g";
  postPatch = (old.postPatch or "") + ''
    substituteInPlace src/ARMeilleure/Translation/Cache/JitCache.cs \
      --replace-fail "CacheSize = 256 * 1024 * 1024" "CacheSize = 2047 * 1024 * 1024"
  '';
  # The suite takes as long as the build and asserts nothing about this.
  doCheck = false;
})
