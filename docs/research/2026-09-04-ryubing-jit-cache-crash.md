# Ryubing 1.3.3 aborts when a game outgrows its JIT cache: Research Report
*Generated: 2026-09-04 | Sources: 22 | Confidence: High on the mechanism, Medium on upstream status*

## Executive Summary
Tears of the Kingdom 1.4.3 dies about twenty seconds into its opening on
Ryubing 1.3.3 (x86-64 Linux) with a silent `SIGABRT` on a guest thread, and
the last log line is always `JIT Cache Region 0 exhausted, creating new Cache
Region 1 (512 MB Total Allocation)`. Nothing Linux-side is responsible: no
kernel 6.15–6.18 change, sysctl, glibc change, .NET W^X mode or NixOS
packaging detail would refuse a second executable region while allowing the
first. The cause is in Ryubing itself: 1.2.82 replaced the single 2 GB
translated-code reservation upstream Ryujinx always used with 256 MB regions
that grow on demand, and the region-aware half of that change is incomplete —
cache entries are keyed by region-relative offset and `Unmap` frees into the
active region regardless of where the function lived, so a second region turns
every lost translation race into allocator corruption and, shortly after, a
fault in overwritten JIT output. Restoring the original 2 GB reservation (a
one-line source patch in `pkgs/ryubing`) means the growth path never runs; no
game-specific setting is involved, so the fix belongs to the Switch platform
environment rather than a per-game one. Upstream repaired it in Canary
1.3.327 (PR #152 "instanced jit cache", 2026-06-30), but no stable release
carries the fix: stable is still 1.3.3 and Canary is at 1.3.351, confirmed
through Ryubing's Forgejo API, which answers plain HTTP even though its web
pages sit behind a bot wall.

## 1. What the crash is
- The failing session's log ends on the region-growth warning and the process
  dumps core with `SIGABRT` on `HLE.GuestThread`, no .NET message
  (local `coredumpctl`, three cores at 15:02, 15:17 and 15:20 PDT; the first
  two were the controller-applet exception, the third this).
- The code (Ryubing 1.3.3 source in the nix store,
  `src/ARMeilleure/Translation/Cache/JitCache.cs`): `CacheSize = 256 * 1024 *
  1024`; `Allocate` creates a new `ReservedRegion` when the active one is
  full; `_cacheEntries` and `TryFind` are keyed by offset only; `Unmap`
  resolves the region by pointer but calls
  `_cacheAllocators[_activeRegionIndex].Free(funcOffset, …)`.
  `Translator.cs:219` unmaps the losing side of every concurrent translation
  and `ClearJitCache` unmaps everything, so once region 1 is active a region-0
  free lands in region 1's allocator, the next allocation returns live code,
  and a guest thread faults in overwritten output. .NET reports that as an
  uncatchable `AccessViolationException` and aborts.
- Upstream Ryujinx (archived, Codeberg mirror) has `CacheSize = 2047 * 1024 *
  1024` in both `ARMeilleure` and `LightningJit` and no notion of regions
  ([JitCache.cs mirror](https://codeberg.org/smj2k/Ryujinx/raw/branch/master/src/ARMeilleure/Translation/Cache/JitCache.cs)).
- The change: Ryubing 1.2.82, PR #615 — "Instead of pre-allocating 2GB of
  memory for the JIT cache, it is now divided into many regions of 256MB"
  ([release note](https://newreleases.io/project/github/Ryubing/Ryujinx/release/1.2.82)).
- Growth does not always crash — a Windows Canary log reached region 2 before
  dying of something else ([Ryubing/Issues #174](https://github.com/Ryubing/Issues/issues/174)) —
  which fits a race-dependent corruption rather than a deterministic failure.

## 2. What it is not
- Kernel: 6.15–6.18 add no restriction on anonymous `PROT_EXEC` mappings
  ([6.18](https://kernelnewbies.org/Linux_6.18), [6.17](https://kernelnewbies.org/Linux_6.17));
  `vm.memfd_noexec` only affects `memfd_create`, which ARMeilleure does not
  use ([LWN](https://lwn.net/Articles/918106/)); MDWE and SELinux `execmem`
  would have refused region 0 ([LWN](https://lwn.net/Articles/912536/),
  [Drepper](https://www.akkadia.org/drepper/selinux-mem.html)). Host:
  `max_map_count 1048576`, `overcommit 0`, no hardened profile.
- .NET W^X: ARMeilleure maps its own regions with `mmap`/`mprotect`
  (`MemoryManagementUnix.cs`), outside the CLR allocator that
  `DOTNET_EnableWriteXorExecute` governs ([dotnet/runtime#80580](https://github.com/dotnet/runtime/issues/80580)).
- Settings: the memory manager mode, PPTC, vsync, backend and texture
  recompression have documented effects on TOTK but none touch the JIT cache;
  "Use hypervisor" and LightningJit are macOS/ARM64 only
  ([ArmProcessContextFactory.cs](https://codeberg.org/smj2k/Ryujinx/raw/branch/master/src/Ryujinx.HLE/HOS/ArmProcessContextFactory.cs)),
  and the cache size has no configuration key. PPTC off was tried here and
  changed nothing.
- nixpkgs: `pkgs/by-name/ry/ryubing` carries no patches and sets only
  `SDL_VIDEODRIVER=x11`; its history is version bumps
  ([history](https://github.com/NixOS/nixpkgs/commits/master/pkgs/by-name/ry/ryubing)).

## 3. TOTK 1.4.x context
- 1.4.x needs Ryubing ≥ 1.3.3 for SDK20 / audio renderer REV15
  ([Ryubing/Issues #91](https://github.com/Ryubing/Issues/issues/91),
  [#15](https://github.com/Ryubing/Issues/issues/15)); 1.4.3's own notes are
  gameplay fixes ([Perfectly Nintendo](https://www.perfectly-nintendo.com/the-legend-of-zelda-tears-of-the-kingdom-switch-all-the-updates/)).
- Texture recompression may be needed on cards that run out of device memory
  ([Ryubing FAQ](https://docs.ryujinx.app/info/faq-&-troubleshooting/)); not
  this failure.
- The only 1.4.3 issue on Ryubing's tracker is a "bus error" mid-flight on
  Linux Canary, closed without diagnosis ([#479](https://github.com/Ryubing/Issues/issues/479)).

## Key Takeaways
- Fix applied: `pkgs/ryubing/default.nix` overrides nixpkgs' Ryubing with
  `CacheSize = 2047 * 1024 * 1024`; `src/client/env/switch.nix` uses it for
  every Switch game. Reserved, not committed, so the cost is address space.
- No per-game environment: the bug is in the emulator's code cache, not in a
  TOTK setting, so a TOTK-only file would leave the next large game to hit it.
- The PPTC change made while diagnosing was reverted (`enable_ptc = true`).
- Drop the override once nixpkgs' Ryubing is a release that includes PR #152
  (Canary ≥ 1.3.327; the next stable after 1.3.3). Building Canary 1.3.351
  ourselves is possible (its API serves tarballs) but means regenerating the
  NuGet lock, so the one-line patch on stable is the cheaper path today.
- Ryubing's Forgejo REST API (`/api/v1/repos/projects/Ryubing/...`,
  `/api/v1/repos/Ryubing/Canary/releases`) is reachable from scripts; the
  web UI and raw pages are not.

## Sources
0. [Ryubing PR #152 "instanced jit cache"](https://git.ryujinx.app/projects/Ryubing/pulls/152) — the upstream fix, Canary 1.3.327; [stable releases](https://git.ryujinx.app/api/v1/repos/projects/Ryubing/releases), [Canary releases](https://git.ryujinx.app/api/v1/repos/Ryubing/Canary/releases).
1. [Ryubing 1.2.82 release note](https://newreleases.io/project/github/Ryubing/Ryujinx/release/1.2.82) — the 2 GB → 256 MB regions change.
2. [Upstream JitCache.cs (Codeberg mirror)](https://codeberg.org/smj2k/Ryujinx/raw/branch/master/src/ARMeilleure/Translation/Cache/JitCache.cs) — `CacheSize = 2047 MB`, no regions.
3. [Upstream ArmProcessContextFactory.cs](https://codeberg.org/smj2k/Ryujinx/raw/branch/master/src/Ryujinx.HLE/HOS/ArmProcessContextFactory.cs) — LightningJit and hypervisor are ARM64/macOS paths.
4. [Ryubing/Issues #174](https://github.com/Ryubing/Issues/issues/174) — a Windows log with the same region line.
5. [Ryubing/Issues #479](https://github.com/Ryubing/Issues/issues/479) — TOTK 1.4.3 bus error on Linux Canary.
6. [Ryubing/Issues #91](https://github.com/Ryubing/Issues/issues/91), [#15](https://github.com/Ryubing/Issues/issues/15) — TOTK 1.4.x needs 1.3.3's SDK20 support.
7. [Ryubing/Issues #249](https://github.com/Ryubing/Issues/issues/249), [#246](https://github.com/Ryubing/Issues/issues/246), [#406](https://github.com/Ryubing/Issues/issues/406) — macOS-only TOTK launch failures.
8. [Ryubing/Issues #372](https://github.com/Ryubing/Issues/issues/372) — a Linux AccessViolation → SIGABRT of the same shape.
9. [Ryubing FAQ](https://docs.ryujinx.app/info/faq-&-troubleshooting/), [setup guide](https://docs.ryujinx.app/guides/setup-guide/) — settings advice.
10. [kernelnewbies 6.15](https://kernelnewbies.org/Linux_6.15), [6.16](https://kernelnewbies.org/Linux_6.16), [6.17](https://kernelnewbies.org/Linux_6.17), [6.18](https://kernelnewbies.org/Linux_6.18) — no executable-memory changes.
11. [LWN: memfd_noexec](https://lwn.net/Articles/918106/), [LWN: MDWE](https://lwn.net/Articles/912536/), [Drepper on SELinux execmem](https://www.akkadia.org/drepper/selinux-mem.html) — what would have blocked region 0 too.
12. [dotnet/runtime#80580](https://github.com/dotnet/runtime/issues/80580), [discussion #81752](https://github.com/dotnet/runtime/discussions/81752) — the CLR's W^X mapper and its failure messages.
13. [glibc 2.41 announcement](https://lists.gnu.org/archive/html/info-gnu/2025-01/msg00014.html) — executable-stack change, not applicable.
14. [nixpkgs ryubing history](https://github.com/NixOS/nixpkgs/commits/master/pkgs/by-name/ry/ryubing), [PR #238459](https://github.com/NixOS/nixpkgs/pull/238459) — packaging and `max_map_count`.
15. [nx-optimizer #300](https://github.com/MaxLastBreath/nx-optimizer/issues/300), [#145](https://github.com/MaxLastBreath/nx-optimizer/issues/145) — TOTK 1.4.3 / mod-related crashes on Windows.
16. [Perfectly Nintendo: TOTK updates](https://www.perfectly-nintendo.com/the-legend-of-zelda-tears-of-the-kingdom-switch-all-the-updates/) — 1.4.3 contents.
17. Local: Ryubing 1.3.3 source in the nix store (`JitCache.cs`, `CacheMemoryAllocator.cs`, `Translator.cs`, `MemoryManagementUnix.cs`); `coredumpctl`; the session logs under `~/.local/state/gotg/env/env-switch/config/Ryujinx/Logs`.

## Methodology
Three parallel agents (release notes and issues; emulator settings and TOTK
guides; Linux/.NET/nixpkgs causes) using WebSearch and WebFetch, plus direct
queries of Ryubing's GitHub issue tracker and reading of the 1.3.3 source in
the nix store. About 60 queries; 22 sources kept. Inaccessible: Ryubing's
Forgejo (Anubis), the removed GitHub mirrors, GBAtemp and gametechwiki (403).
Sub-questions: is the crash known upstream and fixed; which settings matter;
is anything Linux-side responsible; what does the 1.3.3 code do.
