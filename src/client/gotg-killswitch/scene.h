// What one frame of the bar looks like, as a mesh -- no SDL, so a test can
// ask where the ring is and whether the bar is on screen at all.
//
// The bar comes down from the top edge for two reasons and shows one at a
// time:
//
//   - somebody is holding the exit chord: a red ring closes slowly across the
//     three seconds, an X firming up inside it, and the game stops when it
//     closes. This outranks everything; it is the one thing on screen that
//     is about to end the session.
//   - somebody is joining: one ring per pad holding a button, filling in its
//     seat's colour, with a spinner turning inside it so a hold that has just
//     begun already reads as "working on it". A seat just taken shows as a
//     solid disc with a tick for a moment before the bar goes back up.

#ifndef GOTG_SCENE_H
#define GOTG_SCENE_H

#include <stddef.h>
#include <stdint.h>

#include "shapes.h"

typedef struct {
    int width;                 // the surface, in pixels
    float bar_height;          // the bar, in pixels: see gs_bar_height
    double position;           // the bar: 0 out of sight, 1 all the way down
    const float *fractions;    // pads holding to join, oldest first: how far
    const int32_t *players;    // ...and the seat each is filling towards
    size_t hold_count;
    const int32_t *joined;     // seats just taken, oldest first
    size_t joined_count;
    double exit_progress;      // 0..1 through the exit hold; 0 when not held
    double clock;              // seconds, for the spinner's turn
} gs_scene;

// The bar's height on a screen this tall: big enough to read from a sofa,
// small enough to leave the game alone. Taken by the scene as a number rather
// than worked out from its surface, because on a layer-shell compositor the
// surface *is* the bar and on X11 it is the whole screen.
float gs_bar_height(int screen_height);

// The colour of a seat, the same four the picker draws them in.
gs_colour gs_player_colour(int player);

// The frame, appended to `mesh` (which is cleared first).
void gs_scene_build(const gs_scene *scene, gs_mesh *mesh);

// Where the n-th of `count` items sits across a bar this wide: centred, a
// fixed step apart.
float gs_item_x(int width, float bar_height, size_t index, size_t count);

#endif
