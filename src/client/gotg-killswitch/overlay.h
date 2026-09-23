// The window the bar is drawn in, over whatever game is running.
//
// "Over the game" is a different request to every display server this runs
// under, so there are three ways in and the first that fits is taken:
//
//   gamescope (a Deck in Game Mode)
//       An X11 window with GAMESCOPE_EXTERNAL_OVERLAY set: gamescope's one
//       external-overlay slot, which mangoapp uses too. It sits above the game
//       and below Steam's own overlay (steamcompmgr.hpp: zpos 2 against 3).
//   a wlroots compositor with layer-shell (sway, Hyprland, KDE)
//       A layer-shell surface on the overlay layer, which sway stacks above
//       fullscreen windows (sway/tree/root.c: shell_overlay after fullscreen).
//       No input and no keyboard, so a click lands on the game.
//   anything else with X11 (cage, which QA runs games in, has no layer-shell)
//       An override-redirect window with an empty input shape: wlroots draws
//       unmanaged X11 windows above fullscreen ones, and nothing clicks it.
//
// The window exists only while the bar is on screen. Opened when it starts
// down, closed when it is back up: a hidden overlay that stays mapped costs a
// fullscreen game its direct scanout on wlroots, and holds gamescope's slot
// from mangoapp.

#ifndef GOTG_OVERLAY_H
#define GOTG_OVERLAY_H

#include <stdbool.h>

#include "shapes.h"

typedef struct overlay overlay;

// The window, or NULL where there is nowhere to put one -- in which case the
// painter exits and the kill switch carries on without a bar.
overlay *overlay_open(void);

// Which way in was taken, for the log: "gamescope", "layer-shell", "x11".
const char *overlay_kind(const overlay *window);

// Which SDL renderer draws it, for the same log: "software" is the fallback
// where no GPU userspace could be loaded.
const char *overlay_renderer(const overlay *window);

// Whether presenting waits for the panel. Where it does not, the painter
// paces itself, or it would draw as fast as the driver lets it.
bool overlay_vsync(const overlay *window);

// The surface, in pixels, and the bar's height on it.
void overlay_size(const overlay *window, int *width, float *bar_height);

// One frame: cleared to nothing, the mesh drawn, presented.
void overlay_draw(overlay *window, const gs_mesh *mesh);

void overlay_close(overlay *window);

#endif
