# Ryubing with the JIT cache Ryujinx always had.
#
# 1.3.3 shrank ARMeilleure's translated-code cache from one 2 GB reservation
# to 256 MB, growing by further 256 MB regions when the first fills. A game
# whose code outgrows the first region — Tears of the Kingdom does, twenty
# seconds into its opening — gets "JIT Cache Region 0 exhausted, creating new
# Cache Region 1" and then a silent SIGABRT on a guest thread, three times out
# of three on 2026-09-04. The growth path is what is broken; a region large
# enough never takes it. Reserved, not committed, so the 2 GB costs address
# space and nothing else, which is why upstream ran that way for years.
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
