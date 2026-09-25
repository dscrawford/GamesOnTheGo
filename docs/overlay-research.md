# A simple overlay over games, on the Deck and on any Linux desktop

*Researched 2026-09-24 · ~40 sources · Confidence: high on the mechanisms
(read from compositor source), medium on live behaviour (nothing below was
tested on hardware yet).*

## Summary

Every separate-process overlay that works across desktops does the same three-way
choice we already make: gamescope's external-overlay slot, a wlr-layer-shell
surface on the `overlay` layer, or an X11 override-redirect window with an empty
input shape. Nothing simpler covers more. In-process injection (a Vulkan layer,
MangoHud-style) is the only thing that is truly compositor-independent, and it is
not simple: one hook per graphics API, and an `LD_PRELOAD` for GL.

Our inconsistency on the Deck is most likely **not** the design but one detail of
the gamescope branch: we open an X11 window on the display we inherited from the
game, and gamescope only takes the external overlay from **xwayland 0**, Steam's
display. A game in Game Mode runs on another xwayland, so our window is on a
server gamescope never looks at for overlays. gamescope also accepts a
**layer-shell surface on its own Wayland socket** (`GAMESCOPE_WAYLAND_DISPLAY`) as
the external overlay -- the same code path we use on sway and KDE -- which removes
the need to find display 0 at all.

## 1. Deck Game Mode (gamescope)

- gamescope reads `GAMESCOPE_EXTERNAL_OVERLAY` (CARDINAL) from a window and keeps
  **one** external overlay per display, the most opaque one
  ([steamcompmgr.cpp L4627-4634](https://github.com/ValveSoftware/gamescope/blob/ad2763da1c48860f649abfe842a087188dcb6e20/src/steamcompmgr.cpp#L4627-L4634)).
- It takes that overlay **only from the root context**, xwayland 0, falling back
  to Wayland layer-shell surfaces when there is none
  ([L5191-5217](https://github.com/ValveSoftware/gamescope/blob/ad2763da1c48860f649abfe842a087188dcb6e20/src/steamcompmgr.cpp#L5191-L5217), read and confirmed here).
  `DISPLAY` is server 0 for Steam; games get the extra servers
  (`STEAM_GAME_DISPLAY_n`, [main.cpp L1071-1090](https://github.com/ValveSoftware/gamescope/blob/ad2763da1c48860f649abfe842a087188dcb6e20/src/main.cpp#L1071-L1090)).
  Decky overlays hard-code `DISPLAY=:0` for this reason
  ([steady-deck](https://github.com/spaghettijeff/steady-deck/blob/9ede08585decbb74bc0815cb2c84b88a1ee7ddc5/main.py#L43),
  [OverLaid](https://github.com/TheLogicMaster/OverLaid/blob/9fe79cd38610d28890c76c3e6da5fbd918e406bc/main.py#L85)).
- **Every layer-shell surface on gamescope's socket is an external overlay**:
  `surface_info->win->isExternalOverlay = true`
  ([wlserver.cpp](https://github.com/ValveSoftware/gamescope/blob/ad2763da1c48860f649abfe842a087188dcb6e20/src/wlserver.cpp), `layer_shell_surface_new`, read and confirmed here;
  since commit [53eb4fe](https://github.com/ValveSoftware/gamescope/commit/53eb4fe900), in 3.14.24).
- It is drawn at zpos 2: above the game, below Steam's overlay
  ([steamcompmgr.hpp L38-43](https://github.com/ValveSoftware/gamescope/blob/ad2763da1c48860f649abfe842a087188dcb6e20/src/steamcompmgr.hpp#L38-L43)),
  unscaled, never given input.
- **The one slot is shared with mangoapp**, the performance overlay. mangoapp
  sets the atom to 0 when hidden
  ([MangoHud main.cpp](https://github.com/flightlessmango/MangoHud/blob/73931de948402caf3ea80f26d5bbb6b3f1d2c1d9/src/app/main.cpp#L418-L424)),
  but a 3.16.29 bug left it holding the slot
  ([gamescope#2430](https://github.com/ValveSoftware/gamescope/issues/2430)),
  and one project gave up on the slot for that reason
  ([couchside notes](https://github.com/emerytech/couchside/blob/bed841dcedcdd096d1bd588f1a79e97ae7665a88/docs/memory/project_deck-overlay.md)).
  With the performance overlay on, ours is not drawn, whichever route.
- With VRR active, external-overlay repaints can wait for the game's next frame
  ([L7933-7943](https://github.com/ValveSoftware/gamescope/blob/ad2763da1c48860f649abfe842a087188dcb6e20/src/steamcompmgr.cpp#L7933-L7943)).

## 2. Desktops

| Compositor | Route | Over fullscreen | Source |
|---|---|---|---|
| KWin Wayland (Plasma 5.20+, Deck Desktop since SteamOS 3.8.10) | layer-shell `overlay` | yes, KWin's top layer | [kwin layershellv1window.cpp](https://github.com/KDE/kwin/blob/master/src/layershellv1window.cpp) |
| sway | layer-shell `overlay`, or Xwayland override-redirect | yes (tree: fullscreen < unmanaged < shell_overlay) | [sway root.c](https://github.com/swaywm/sway/blob/master/sway/tree/root.c) |
| Hyprland | layer-shell `overlay` | yes | [discussion #11963](https://github.com/hyprwm/Hyprland/discussions/11963) |
| GNOME / Mutter | **no layer-shell**; Xwayland override-redirect | yes by the code (`META_LAYER_OVERRIDE_REDIRECT`, the highest) | [mutter#973](https://gitlab.gnome.org/GNOME/mutter/-/issues/973), [meta-enums.h](https://github.com/GNOME/mutter/blob/main/src/meta/meta-enums.h) |
| KWin X11 (Deck Desktop before 3.8.10) | override-redirect + XShape | probably | [kwin layers.cpp](https://github.com/KDE/kwin/blob/master/src/layers.cpp) |
| niri | layer-shell (its override-redirect is broken) | not checked | [xwayland-satellite#429](https://github.com/Supreeeme/xwayland-satellite/issues/429) |

Pitfalls: request `overlay`, never `top` (fullscreen-vs-top is undefined,
[Hyprland#15937](https://github.com/hyprwm/Hyprland/pull/15937)); unmap the bar
when hidden, since a mapped one costs the game direct scanout (Mutter picks it as
the top window); on X11 with compositing bypassed an ARGB bar loses its alpha.

## 3. The alternatives, and why not

- **Nested gamescope everywhere** (`gamescope -- game`, one drawing path): cannot
  be used in Game Mode, where it conflicts with the session's gamescope
  ([Steam forum](https://steamcommunity.com/app/1675200/discussions/2/3385030647944762597),
  [SteamTinkerLaunch](https://github.com/sonic2kk/steamtinkerlaunch/wiki/GameScope)),
  and nesting has a cost and its own bugs (touch, window switching, HDR on
  NVIDIA). Not simpler.
- **In-process Vulkan layer / GL preload** (MangoHud's way,
  [implicit layer manifest](https://github.com/flightlessmango/MangoHud/blob/master/src/mangohud.json.in),
  [loader docs](https://github.com/KhronosGroup/Vulkan-Loader/blob/main/docs/LoaderLayerInterface.md)):
  works on every compositor, but needs a layer per API
  ([Mesa's reference layer](https://gitlab.freedesktop.org/mesa/mesa/-/tree/main/src/vulkan/overlay-layer) is ~2,800
  lines; a Rust start: [vulkan_layer](https://github.com/google/vk-layer-for-rust))
  plus a GL hook, and MangoHud itself calls its layer unsupported under gamescope.
  The fallback if the separate window keeps failing, not the base.
- **Driving MangoHud's text** (`custom_text`, `exec`, a watched config): text only,
  100-500 ms updates -- no ring, no slide.

## Recommendation

Keep the design; make it one Wayland path wherever there is a Wayland compositor:

1. `GAMESCOPE_WAYLAND_DISPLAY` set → **layer-shell on that socket** (connect with
   `WAYLAND_DISPLAY` set to it). The code already written for sway and KDE, and no
   need to find xwayland 0. Say in the log whether the slot looks taken (mangoapp
   shown).
2. A Wayland session with `zwlr_layer_shell_v1` → layer-shell `overlay`.
3. `DISPLAY` → X11 override-redirect, empty input shape (GNOME via Xwayland, X11).
4. Otherwise say so and draw nothing. The exit chord never depends on any of it.

Then test the gamescope case where the bar went missing, without the Deck: run
the QA harness's game inside **gamescope's headless backend** and grade the
recording, as it already does for sway and cage. Unverified here: that gamescope's
layer-shell honours our anchor and height (it paints overlays unscaled), and that
the layer-shell route works in SteamOS Game Mode -- nobody reported trying it.

## Gaps

Nothing was tested live. Not found: whether a stock Deck keeps mangoapp in the slot
with the performance overlay off; the gamescope version on current SteamOS stable;
live KWin X11 / GNOME stacking; how Discover-Overlay keeps alpha on uncomposited X11.

## Method

Three parallel research passes (gamescope internals; desktop compositors;
compositor-independent designs), ~100 web searches and fetches, plus gamescope
`wlserver.cpp` and `steamcompmgr.cpp` read directly at commit `ad2763da` to
confirm the two claims the recommendation rests on.
