#include "overlay.h"

#include <math.h>

// -std=c17 is ISO C, where M_PI is an extension rather than a promise.
#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

// Clockwise from twelve o'clock, because that is how a countdown reads. In
// screen coordinates y grows downwards, so twelve o'clock is -y and clockwise
// is +x first — the opposite of the unit circle, and the reason this is worth
// a function rather than two lines inlined at the call site.
static ks_point at(float cx, float cy, float radius, float turn) {
    float angle = turn * 2.0f * (float)M_PI;
    return (ks_point){cx + radius * sinf(angle), cy - radius * cosf(angle)};
}

size_t ks_ring(float cx, float cy, float inner, float outer, float progress, ks_point *out, size_t max) {
    if (progress <= 0.0f || max < 4) return 0;
    if (progress > 1.0f) progress = 1.0f;

    // One step every few degrees: fine enough that the edge reads as a curve
    // at the size this is drawn, coarse enough to stay a handful of triangles.
    const size_t steps = 64;
    size_t written = 0;
    for (size_t step = 0; step <= steps; step++) {
        float turn = progress * (float)step / (float)steps;
        if (written + 2 > max) break;
        out[written++] = at(cx, cy, inner, turn);
        out[written++] = at(cx, cy, outer, turn);
    }
    return written;
}

void ks_stroke(float x1, float y1, float x2, float y2, float width, ks_point *out) {
    float dx = x2 - x1, dy = y2 - y1;
    float length = sqrtf(dx * dx + dy * dy);
    if (length <= 0.0f) length = 1.0f;

    // Half a stroke's width, perpendicular to the line.
    float px = -dy / length * width / 2.0f;
    float py = dx / length * width / 2.0f;

    out[0] = (ks_point){x1 + px, y1 + py};
    out[1] = (ks_point){x2 + px, y2 + py};
    out[2] = (ks_point){x2 - px, y2 - py};
    out[3] = (ks_point){x1 - px, y1 - py};
}
