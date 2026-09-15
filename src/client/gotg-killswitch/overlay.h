// What the player sees while the kill switch is held.
//
// Three seconds is a long time to hold something with no sign that anything is
// happening — long enough to let go and conclude the feature does not work. So
// the hold draws itself: a red ring that closes as the three seconds run out,
// around an X that grows solid as it does. Letting go takes it away.
//
// The geometry is separated from the drawing for the usual reason: an arc that
// sweeps the wrong way or a ring that is full at half a hold are bugs a test
// can catch, and SDL is not needed to catch them.

#ifndef GOTG_OVERLAY_H
#define GOTG_OVERLAY_H

#include <stdbool.h>
#include <stddef.h>

typedef struct {
    float x, y;
} ks_point;

// The ring as a triangle strip: one inner and one outer point per step,
// sweeping clockwise from twelve o'clock through `progress` of a full circle.
// Returns how many points were written, which is 0 for a progress of 0 and
// never more than `max`.
size_t ks_ring(float cx, float cy, float inner, float outer, float progress, ks_point *out, size_t max);

// One stroke of the X, as the four corners of a thick line from (x1,y1) to
// (x2,y2). Always writes four points.
void ks_stroke(float x1, float y1, float x2, float y2, float width, ks_point *out);

// The window, or NULL where there is no display to put one on. Everything
// below tolerates NULL, so a machine with no video is a kill switch that
// simply does not draw.
typedef struct overlay overlay;

overlay *overlay_open(void);
void overlay_draw(overlay *window, float progress);
void overlay_close(overlay *window);

#endif
