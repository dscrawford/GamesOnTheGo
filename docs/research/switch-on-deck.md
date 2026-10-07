# Switch emulation on the Steam Deck: Research Report

*Generated: 2026-10-07 | Sources: ~45 (three parallel searches, ~130 fetches) | Confidence: Medium-low — plenty of guidance, almost no measured numbers for Ryujinx on a Deck*

Question asked: what Ryujinx settings should GOTG make the default for Switch
games on a Steam Deck so they run smoothly, and can frame generation or
similar help.

## Executive Summary

The one setting that matters most is already wrong on the Deck: the Deck's
Ryujinx config has `docked_mode: true` (Ryujinx's default), so every Switch
game renders at 1080p for a 1280×800 panel — 2.25× the pixels of handheld
720p, for a machine that gains no power when docked. The `60fps` variants
make it worse by raising the emulated DRAM to 8 GB (`dram_size: 2`), which
since Ryubing 1.2.67 also grows the texture cache, on a 16 GB machine whose
GPU is carved out 1 GB of it (measured on this Deck). Everything else in
the current config already matches what EmuDeck ships and what the 2025
Deck guides recommend: Vulkan, 1× scale, texture recompression off,
HostMappedUnsafe, macro HLE and PTC on, shader cache on. 60 fps is realistic
on a Deck only for titles already near it (Super Mario RPG, perhaps TTYD);
for BotW/TotK the Deck recipe is a 30 cap with UltraCam's DynamicFPS at
720p, and the one 2025 Deck-specific guide's author moved those two to Eden
to hold a stable 30. Frame generation exists for this exact case (lsfg-vk
2.0, Lossless Scaling's engine on Linux, user-reported working with
Ryujinx), but it costs a frame of latency plus forced vsync, needs the base
locked at an exact divisor of the refresh, and needs the person to own
Lossless Scaling — a defensible opt-in variant for 30-locked games, not a
default. No other frame-generation route applies to a native Vulkan
emulator.

## 1. What the Deck is running today (primary data, this Deck)

`~/.local/state/gotg/env/env-switch-world_paper_mario_the_thousand_year_door-60fps/config/Ryujinx/Config.json`, schema version 70:

| Field | Now | Note |
|---|---|---|
| `graphics_backend` | Vulkan | right |
| `res_scale` | 1 | right |
| `docked_mode` | **true** | Ryujinx default; 1080p internal on an 800p panel |
| `dram_size` | **2** (8 GB) | set by `helpers.ryujinxDram 2` in the 60fps variant; EmuDeck ships 0 |
| `enable_texture_recompression` | false | matches docs ("only on OOM") and both Deck guides |
| `memory_manager_mode` | HostMappedUnsafe | "default and fastest" everywhere |
| `enable_macro_hle` / `enable_ptc` / `enable_shader_cache` | true | right |
| `enable_low_power_ptc` | false | see §2 |
| `backend_threading` | Auto | right |
| `vsync_mode` | 0 | Switch 60 Hz mode; EmuDeck also 0 with `enable_vsync` false |
| `max_anisotropy` / `aspect_ratio` | -1 / Fixed16x9 | right |
| `use_hypervisor` | true | Apple Silicon only; no effect on x86 |
| `preferred_gpu` | "" | EmuDeck pins `0x1002_0x163F` (Van Gogh); irrelevant with one GPU |
| `audio_backend` | SDL2 | EmuDeck's newer schema says SDL3; no performance claim found |

Machine: 15.1 GB RAM, GPU VRAM carve-out **1 GiB** (`mem_info_vram_total`),
i.e. the BIOS UMA buffer is at its 1 GB default, not 4 GB.

The plain `env-switch` has never been launched on this Deck, so its
Config.json would be Ryujinx's first-run defaults — docked, 4 GB DRAM.

## 2. Recommended Ryujinx settings for a Deck

| Field | Value | Basis |
|---|---|---|
| `docked_mode` | **false** when the Deck's own panel is the display; true only with an external display connected | Ryubing setup guide (docked = 1080p render); the Deck gains nothing docked ([TechRadar](https://www.techradar.com/news/unlike-the-nintendo-switch-valves-steam-deck-wont-get-boosted-performance-when-docked)) |
| `dram_size` | **0** (4 GB) unless a mod needs more | troubleshooting mirror "don't use 6/8 GB unless needed"; [changelog 1.2.67](https://docs.ryujinx.app/info/changelog/) ties texture cache to DRAM size; EmuDeck ships 0 |
| `res_scale` | 1 | both 2025 Deck guides; the docs' "2× is free" is a desktop claim |
| `enable_texture_recompression` | false; true per game only for the ASTC list (TotK, FE Engage, SMB Wonder, MP Remastered, Bayonetta 3, Astral Chain) if OOM appears | [Ryubing FAQ](https://docs.ryujinx.app/info/faq-&-troubleshooting/); recompression costs CPU, the Deck's scarce resource |
| `memory_manager_mode` | HostMappedUnsafe (HostMapped on crash) | [troubleshooting mirror](https://mintlify.wiki/yakushabb/mirror-ryujinx/user-guide/troubleshooting) |
| `enable_macro_hle`, `enable_ptc`, `enable_shader_cache` | true | docs, Deck guides, EmuDeck |
| `enable_low_power_ptc` | optional: true cuts translation threads by two-thirds (slower first two launches, cooler); false loads faster | [changelog 1.2.25](https://docs.ryujinx.app/info/changelog/); inference, no Deck measurement |
| `backend_threading` | Auto | Deck guides |
| `vsync_mode` | 0 (Switch 60) and let gamescope cap; 120 fps variants (`vsync_mode 2`, custom 120) are pointless on a 90 Hz panel | UltraCam helper; [SDHQ](https://steamdeckhq.com/tips-and-guides/the-sdhq-performance-settings-encyclopedia/) |
| `preferred_gpu` | leave "" | one GPU |

Sources agree the defaults are already right (Ryubing: "Ryujinx works out
of the box and is already on the best settings by default"); the two
2025 Deck guides ([performance](https://steamcommunity.com/sharedfiles/filedetails/?id=3558648713),
[install](https://steamcommunity.com/sharedfiles/filedetails/?id=3554323467))
and [EmuDeck's shipped Config.json](https://raw.githubusercontent.com/dragoonDorise/EmuDeck/main/configs/Ryujinx/Config.json)
change nothing in the graphics block except keeping scale native. The
Deck-specific wins are the two fields above that GOTG itself sets wrong.

## 3. Deck-side settings

| Setting | Recommendation | Basis |
|---|---|---|
| Frame cap / refresh | match the cap to the refresh: 30 @ 90 Hz (OLED) or 30 @ 60; 40 @ 80 Hz for games that hold 40; 60 only for SMRPG-class titles | 2025 Deck guide per game; SDHQ |
| GPU clock | pin manually, 1000–1300 MHz: Ryujinx is CPU-bound and the shared power budget otherwise drifts to the GPU | 2025 Deck guide (1300 for Smash/BotW/TotK); SDHQ |
| TDP | no Ryujinx-specific data found | — |
| UMA frame buffer (BIOS) | leave at 1 GB; the 4 GB advice is for native games and unmeasured for emulators; try only on OOM in TotK-class titles | [pimylifeup](https://pimylifeup.com/steam-deck-increase-vram/), [thegamingsetup](https://thegamingsetup.com/steam-deck-uma-buffer-size-guide) — neither tests an emulator |
| FSR in gamescope | does nothing unless the app outputs below 1280×800; a 720p handheld render already fills the width | [gamescope README](https://github.com/ValveSoftware/gamescope) |

## 4. Per game, on a Deck

| Game | What a Deck reaches | Deck recipe | Source |
|---|---|---|---|
| Zelda TotK | not a stable 30 in Ryujinx without help; 60 fps mods need desktop CPUs | 30 cap, UltraCam DynamicFPS, 720p, shadows 512; the Deck guide's author moved it to Eden for a stable 30 | [Deck guide](https://steamcommunity.com/sharedfiles/filedetails/?id=3558648713); [DSOGaming](https://www.dsogaming.com/?p=171347) |
| Zelda BotW | same as TotK | same; UltraCam supports BotW 1.6 | [nx-optimizer](https://github.com/MaxLastBreath/nx-optimizer) |
| Kirby and the Forgotten Land | "performs just fine", level geometry glitches (emulator unnamed) | 30 cap; 60 fps mod exists, Deck result unknown | [gfinity 2025](https://www.gfinityesports.com/article/steam-deck-switch-emulation-is-good-but-has-a-long-way-to-go) |
| Luigi's Mansion 2 HD | 60 fps mod shown only on a 14900K/4090 | 30 cap; Deck 60: insufficient data | [DSOGaming](https://www.dsogaming.com/?p=181348) |
| Super Mario RPG | "close to 60" on a Deck in Ryujinx (unmeasured) | 60 fps mod plausible | [wccftech](https://wccftech.com/super-mario-rpg-leaks-yuzu-ryujinx/) |
| Paper Mario TTYD | one Deck user: 60 fps + 1080p mods "working great" in game mode; others: inconsistent 60 "in most areas" | 60 fps mod at **720p** (skip the 1920×1080 mod), DRAM 4 GB | [gbatemp](https://gbatemp.net/threads/paper-mario-ttyd-60fps-patch.656183/) (snippets; page 403) |
| Skyward Sword HD | insufficient data (the "full speed on Deck" claim is Cemu/Wii U) | 30 cap | [Steam forum 2022](https://steamcommunity.com/app/1675200/discussions/0/3362523432282691992) |

No source publishes measured Deck fps for any of these in the Ryujinx
lineage versus Eden; the one 2026 comparison that is honest about it says
so outright ([tech-insider](https://tech-insider.org/eden-vs-ryubing-best-switch-emulator-2026/)).

Deck-tuned mods for the Ryujinx lineage live in
[Kenji-NX/switch-pchtxt-mods](https://github.com/Kenji-NX/switch-pchtxt-mods)
(60 FPS, resolution, disable dynamic resolution, remove FXAA; pchtxt under
title-ID folders — Ryujinx's native format; includes TotK, SSHD, LM2HD,
SMRPG, TTYD) and, for Zelda, [NX Optimizer's UltraCam](https://github.com/MaxLastBreath/nx-optimizer)
(DynamicFPS, resolution, shadows, FXAA/FSR toggles), which GOTG already
installs. Chuck's 2023 DynamicFPS is superseded by UltraCam's.

## 5. Frame generation and upscaling

**The only route that applies is lsfg-vk** — Lossless Scaling's frame
generation as a Vulkan layer on Linux:

- Status: v2.0.0 (2026-09-05): FP16 path "2–3× faster" on hardware like
  RDNA2, Vulkan 1.2 minimum, ~70% less memory, profiles matched by binary
  name or Steam AppID, `lsfg-vk-cli validate|healthcheck|benchmark`. Moved
  off GitHub to its own Forgejo (git.lsfg-vk.dev, Codeberg mirror) and
  relicensed **CC BY-NC-ND 4.0** on 2026-08-27 ([release](https://lsfg-vk.dev/blog/release-v2.0.0/), [changes](https://lsfg-vk.dev/blog/important-changes-to-lsfg-vk/)).
- Requires owning Lossless Scaling on Steam, switched to its `lsfg-vk` beta
  branch, which ships the DLL the layer loads ([Decky README](https://github.com/xXJSONDeruloXx/decky-lsfg-vk)).
- Configuration (v2 names; the v1 `LSFG_*`/`ENABLE_LSFG` recipes do
  nothing against v2): TOML `[[profile]]` with `active_in`, `multiplier`
  (2; 3 = two generated frames), `flow_scale` (lower = cheaper), `performance_mode`,
  `override_present_mode` (forces FIFO), `preserve_swapchain_image_count`
  (fixes crashes in apps that assume their swapchain length); or env
  `LSFGVK_PROFILE=<name>`, or `LSFGVK_ENV=1` with `LSFGVK_MULTIPLIER`,
  `LSFGVK_FLOW_SCALE`, `LSFGVK_PERFORMANCE_MODE` ([options](https://lsfg-vk.dev/docs/configuration/configuration-options/), [env](https://lsfg-vk.dev/docs/configuration/environment-variables/)). GOTG launches outside Steam's AppID, so the env form is the fit.
- Pacing: one mode; generated frames are presented as ready, "requires
  Vsync", works "only if the target refresh rate is the exact monitor
  refresh rate", "introduces latency" ([pacing](https://lsfg-vk.dev/docs/configuration/pacing-modes/)).
  So: LCD 60 Hz → base 30 × 2; OLED 90 Hz → 45 × 2 or 30 × 3; the 40 Hz
  mode has nothing to double into.
- On gamescope/Deck: `ENABLE_GAMESCOPE_WSI=0`, no MangoHud/vkBasalt layers,
  gamescope's frame limiter **off** (or FG silently does nothing) with the
  cap applied inside the app — for Ryujinx, its vsync mode ([troubleshooting](https://lsfg-vk.dev/docs/troubleshooting/basic-troubleshooting-steps/), [elotrolado thread](https://www.elotrolado.net/hilo_ho-lossless-scaling-frame-generation-para-linux-steamos_2521630_s50)).
- With Ryujinx: works, by user report — "ryujinx funciona igual que algunas
  versiones de yuzu, pero en algunas versiones de eden falla" (same thread).
  No measurement of fps, latency or battery for any emulator on a Deck
  exists.
- Deck verdicts: SteamDeckHQ's hands-on avoids it "for most games since I
  feel the input lag"; 3× produced artifacts; 2.0 "no huge differences"
  ([SDHQ](https://steamdeckhq.com/news/lossless-scaling-2-0-doesnt-improve-frame-gen/)). The enthusiastic coverage tested higher-TDP handhelds ([TechRadar](https://www.techradar.com/computing/gaming-pcs/the-lossless-scaling-plugin-is-the-best-thing-that-could-happen-for-steamos-handhelds-and-performance-results-prove-it)).
- Decky plugin: "Decky LSFG-VK" on the store since 2025-11-28, keyed on
  Steam AppID, still on 1.x as of 2026-09-06; an experimental fork adds
  adaptive FG the upstream docs call "planned" ([SDHQ store](https://steamdeckhq.com/news/lossless-scaling-plugin-is-on-decky-store/)).

Routes that do **not** apply: Decky Framegen / OptiScaler FSR3 FG (DLSS-FG
Windows titles only; on the Deck "felt like 15 fps" — [SDHQ](https://steamdeckhq.com/news/a-plugin-to-install-the-fsr-3-frame-gen-mod-on-steam-deck-is-coming/)),
AMD AFMF (Windows driver, no RADV), gamescope (spatial upscaling only), and
no Switch emulator has frame interpolation (Ryujinx's Turbo/unbounded vsync
speed the emulated clock; they do not interpolate).

| Route | Ryujinx on Deck? | Cost | Needs |
|---|---|---|---|
| lsfg-vk 2.0, env/TOML | yes (user report) | GPU share of the APU; ≥1 frame + FIFO latency; artifacts at 3× | own Lossless Scaling; Vulkan; base = refresh/N; gamescope limiter off; `ENABLE_GAMESCOPE_WSI=0` |
| Decky LSFG-VK | same engine, Steam-AppID keyed | same | Decky Loader; a Steam shortcut |
| Decky Framegen / OptiScaler | no | — | — |
| AMD AFMF | no | — | — |
| gamescope FSR/NIS/SGSR | upscaling only | ~free | app output < 1280×800 |
| Ryujinx `res_scale` 2 | yes | ~4× fragment load | headroom; the opposite of what FG needs |

## 6. The emulator itself, 2026

| Emulator | Status | Linux build | Nix | Note |
|---|---|---|---|---|
| Ryubing 1.3.3 (GOTG's) | maintenance-only since the 16 Feb 2026 DMCA ("no major features") | tarball; Forgejo `git.ryujinx.app` refused connections during this research; EmuDeck fetches Codeberg attachment URLs | `pkgs.ryubing` 1.3.3 | [retrohandhelds](https://retrohandhelds.gg/citron-popular-switch-emulator-is-gone-but-its-not-nintendos-fault/) |
| Kenji-NX | active (Apr 2026), the maintained Ryujinx branch; keeps `switch-pchtxt-mods` | release host unconfirmed | none | [GitHub org](https://github.com/Kenji-NX) |
| Eden 0.2.x | active, "the default choice by 2026"; the Deck guide moved BotW/TotK to it | AppImage only, no Flatpak | `pkgs.eden` 0.2.1 (SDL3 since May 2026) | [downloads](https://eden-emu.dev/downloads) |
| Citron | gone since ~14 Feb 2026 (internal dispute, not Nintendo) | stale | — | retrohandhelds |
| Sudachi / Torzu | suspended / inactive | ? | — | — |

Measured Deck head-to-heads: none published. Switching emulators is a
separate decision with its own controller work (Ryujinx is the consumer
that cannot tell danstick's 360 clones apart; whether Eden can is untested).

## Key Takeaways

1. **Fix the two fields GOTG sets wrong on a Deck**: `docked_mode` false
   (unless an external display is connected) and `dram_size` 0. These are
   the only Deck-specific changes with a basis; the rest of the config is
   already what everyone recommends.
2. **On a Deck, the `60fps` variants should become "smooth 720p" variants**:
   keep UltraCam's DynamicFPS (so a miss drops frames rather than slowing
   the game), target 1280×720, shadows 512, drop the 1080p mods, DRAM 4 GB;
   `120fps` variants have no meaning on a 90 Hz panel. SMRPG and TTYD are
   the titles where 60 is credible.
3. **Pair with gamescope**: cap 30 @ 90 Hz (OLED) / 30 @ 60 (LCD), 60 for
   the two light titles; pin the GPU clock (1000–1300). Leave the BIOS UMA
   at 1 GB.
4. **Frame generation: opt-in, not default.** lsfg-vk 2.0 is the only route,
   works with Ryujinx per users, and suits exactly the 30-locked Zelda case
   — at a frame of latency, forced vsync, and the purchase of Lossless
   Scaling. Worth a `framegen` variant that exports `LSFGVK_ENV=1
   LSFGVK_MULTIPLIER=2 ENABLE_GAMESCOPE_WSI=0` when the layer is installed,
   never on by default. Measure it with the QA harness before trusting it.
5. **Watch the emulator**: Ryubing is in maintenance after the Feb 2026
   DMCA; nixpkgs carries both it and Eden. If Zelda on the Deck is the goal,
   a measured Eden trial is the next experiment.

### How GOTG carries the machine default (implemented 2026-10-07)

Every environment's launcher embeds `src/client/env/machine.sh` and calls
`gotg_machine_detect` before `preLaunch`: `GOTG_MACHINE` is `deck` when the
DMI product name is `Jupiter` or `Galileo` (EmuDeck's rule), and
`GOTG_EXTERNAL_DISPLAY` is 1 when a DRM connector other than the panel's
(eDP/DSI/LVDS) says `connected` — a Deck's dock shows as DP. Both can be set
beforehand. `switch.nix` writes `docked_mode` from the display and
`dram_size = 0` on a Deck on every launch; `ryujinxDram` does nothing on a
Deck; `ryujinxModOnly { onDeck = false; }` removes the mod there (TTYD's
1080p); `totkUltraCam`/`botwUltraCam` choose a Deck profile at launch (cap
≤ 60, render ≤ 1080p docked / 720p on the panel, shadows 512, 4 GiB). The
120fps pchtxt variants (SMRPG, SSHD) are left alone: those patches have no
dynamic FPS, and a 60 Hz vsync under a 120 fps patch could halve game speed.

## Sources

1. [Ryubing FAQ](https://docs.ryujinx.app/info/faq-&-troubleshooting/) — defaults are best; texture recompression list
2. [Ryubing setup guide](https://docs.ryujinx.app/guides/setup-guide/) — docked = 1080p; shader cache; PTC
3. [Ryubing changelog](https://docs.ryujinx.app/info/changelog/) — 1.2.25 low-power PTC; 1.2.67 texture cache scales with DRAM
4. [Steam Deck Ryujinx performance guide (2025)](https://steamcommunity.com/sharedfiles/filedetails/?id=3558648713) — per-game caps and GPU clocks; moved Zelda to Eden
5. [Steam Deck Ryujinx install guide (2025)](https://steamcommunity.com/sharedfiles/filedetails/?id=3554323467) — same graphics block, Ryujinx 1.3.2
6. [EmuDeck Config.json](https://raw.githubusercontent.com/dragoonDorise/EmuDeck/main/configs/Ryujinx/Config.json) — shipped Deck defaults
7. [EmuDeck Ryujinx installer](https://raw.githubusercontent.com/dragoonDorise/EmuDeck/main/functions/EmuScripts/emuDeckRyujinx.sh) — Codeberg tarballs; Jupiter/Galileo detection
8. [Ryujinx doc mirror: graphics](https://mintlify.wiki/yakushabb/mirror-ryujinx/user-guide/graphics-settings), [troubleshooting](https://mintlify.wiki/yakushabb/mirror-ryujinx/user-guide/troubleshooting), [configuration](https://mintlify.wiki/yakushabb/mirror-ryujinx/user-guide/configuration), [PTC](https://mintlify.wiki/yakushabb/mirror-ryujinx/features/ptc), [memory](https://mintlify.wiki/yakushabb/mirror-ryujinx/architecture/memory-management)
9. [SDHQ performance encyclopedia](https://steamdeckhq.com/tips-and-guides/the-sdhq-performance-settings-encyclopedia/) — refresh matching, GPU clock pinning
10. [TechRadar: no docked boost](https://www.techradar.com/news/unlike-the-nintendo-switch-valves-steam-deck-wont-get-boosted-performance-when-docked)
11. [pimylifeup UMA](https://pimylifeup.com/steam-deck-increase-vram/), [thegamingsetup UMA](https://thegamingsetup.com/steam-deck-uma-buffer-size-guide) — unmeasured for emulators
12. [lsfg-vk v2.0.0](https://lsfg-vk.dev/blog/release-v2.0.0/), [important changes](https://lsfg-vk.dev/blog/important-changes-to-lsfg-vk/), [configuration options](https://lsfg-vk.dev/docs/configuration/configuration-options/), [environment variables](https://lsfg-vk.dev/docs/configuration/environment-variables/), [pacing modes](https://lsfg-vk.dev/docs/configuration/pacing-modes/), [troubleshooting](https://lsfg-vk.dev/docs/troubleshooting/basic-troubleshooting-steps/)
13. [decky-lsfg-vk](https://github.com/xXJSONDeruloXx/decky-lsfg-vk), [SDHQ: plugin on store](https://steamdeckhq.com/news/lossless-scaling-plugin-is-on-decky-store/), [SDHQ: 2.0 verdict](https://steamdeckhq.com/news/lossless-scaling-2-0-doesnt-improve-frame-gen/), [SDHQ: lsfg-vk 2.0.0](https://steamdeckhq.com/news/lsfg-vk-lossless-scaling-huge-2-0-0-release/)
14. [elotrolado lsfg-vk thread](https://www.elotrolado.net/hilo_ho-lossless-scaling-frame-generation-para-linux-steamos_2521630_s50) — Ryujinx works; gamescope limiter off
15. [SDHQ: Decky Framegen](https://steamdeckhq.com/news/a-plugin-to-install-the-fsr-3-frame-gen-mod-on-steam-deck-is-coming/), [Yahoo/Digital Trends](https://tech.yahoo.com/computing/articles/ve-dying-steam-deck-frame-150007393.html), [TechRadar AFMF](https://www.techradar.com/computing/gpu/amds-new-graphics-driver-offers-a-free-frame-rate-boost-for-all-pc-games-with-a-couple-of-notable-catches), [gamescope README](https://github.com/ValveSoftware/gamescope)
16. [retrohandhelds: Citron gone, Ryubing maintenance](https://retrohandhelds.gg/citron-popular-switch-emulator-is-gone-but-its-not-nintendos-fault/), [gamingpromax DMCA](https://gamingpromax.com/nintendo-dmca-ryubing-citron-switch-emulators-shutdown/), [Eden downloads](https://eden-emu.dev/downloads), [nixpkgs ryubing](https://mynixos.com/nixpkgs/package/ryubing), [nixpkgs eden](https://mynixos.com/nixpkgs/package/eden), [Kenji-NX](https://github.com/Kenji-NX), [tech-insider comparison](https://tech-insider.org/eden-vs-ryubing-best-switch-emulator-2026/)
17. [Kenji-NX/switch-pchtxt-mods](https://github.com/Kenji-NX/switch-pchtxt-mods), [nx-optimizer](https://github.com/MaxLastBreath/nx-optimizer), [hoverbike1 TOTK mods (archived)](https://github.com/hoverbike1/TOTK-Mods-collection), [wccftech Chuck's DFPS](https://wccftech.com/the-legend-of-zelda-tears-of-the-kingdom-dynamic-fps-mod/)
18. Per game: [gfinity 2025](https://www.gfinityesports.com/article/steam-deck-switch-emulation-is-good-but-has-a-long-way-to-go), [DSOGaming TotK](https://www.dsogaming.com/?p=171347), [DSOGaming LM2HD](https://www.dsogaming.com/?p=181348), [wccftech SMRPG](https://wccftech.com/super-mario-rpg-leaks-yuzu-ryujinx/), [gbatemp TTYD](https://gbatemp.net/threads/paper-mario-ttyd-60fps-patch.656183/), [Steam forum SSHD](https://steamcommunity.com/app/1675200/discussions/0/3362523432282691992)

## Methodology

Three parallel research agents (WebSearch/WebFetch), ~32 queries and
~130 fetches, 2025–2026 sources preferred and older ones marked; plus
primary data read off this Deck (its Ryujinx Config.json, memory and VRAM
carve-out) and GOTG's own environment sources. Sub-questions: Ryujinx
Config.json settings for the Deck; Deck-side settings; texture
recompression and memory manager; per-game Deck results; frame generation
and upscaling routes on SteamOS; the 2026 emulator landscape, EmuDeck's
shipped defaults, and per-game mods. Unreachable during the research:
`git.ryujinx.app` (connection refused), gametechwiki and notebookcheck
(403), gbatemp (403, snippets only). Nothing measured for Ryujinx on a Deck
was found for any single setting; treat the FPS claims above as reports,
not benchmarks.
