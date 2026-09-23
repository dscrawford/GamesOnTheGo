#include "bar.h"

#include <math.h>

void gs_bar_init(gs_bar *bar, double slide_seconds) {
    bar->slide_seconds = slide_seconds > 0.0 ? slide_seconds : 0.0;
    bar->changed = 0.0;
    bar->from = 0.0;
    bar->down = false;
}

// How long the rest of this slide takes: a slide's worth of the distance left.
static double duration(const gs_bar *bar) {
    double target = bar->down ? 1.0 : 0.0;
    return bar->slide_seconds * fabs(target - bar->from);
}

static double progress(const gs_bar *bar, double now) {
    double span = duration(bar);
    if (span <= 0.0) return 1.0;
    double t = (now - bar->changed) / span;
    if (t < 0.0) return 0.0;
    return t > 1.0 ? 1.0 : t;
}

double gs_bar_position(const gs_bar *bar, double now) {
    double t = progress(bar, now);
    // Smoothstep: slow out of the start and into the end.
    double eased = t * t * (3.0 - 2.0 * t);
    double target = bar->down ? 1.0 : 0.0;
    return bar->from + (target - bar->from) * eased;
}

void gs_bar_want(gs_bar *bar, bool down, double now) {
    if (down == bar->down) return;
    bar->from = gs_bar_position(bar, now);
    bar->down = down;
    bar->changed = now;
}

bool gs_bar_gone(const gs_bar *bar, double now) {
    return !bar->down && progress(bar, now) >= 1.0;
}

bool gs_bar_moving(const gs_bar *bar, double now) {
    return progress(bar, now) < 1.0;
}
