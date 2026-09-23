// The top bar's motion: down when there is something to show, up when not.
//
// Its own file for the reason the kill switch's decision is: "slides down in a
// quarter of a second, and turning round half way does not jump" is a thing a
// test can hold with a made-up clock, and not with a compositor.
//
// A reversal starts from wherever the bar is, and takes the time the rest of
// that distance is worth -- a bar called back up an inch into its descent is
// back in an instant, not a full slide later.

#ifndef GOTG_BAR_H
#define GOTG_BAR_H

#include <stdbool.h>

typedef struct {
    double slide_seconds;  // a full slide, top to bottom
    double changed;        // when the direction last changed
    double from;           // where it was then
    bool down;             // where it is going
} gs_bar;

void gs_bar_init(gs_bar *bar, double slide_seconds);

// Where the bar should be going. Changing direction mid-slide keeps its place.
void gs_bar_want(gs_bar *bar, bool down, double now);

// How far down it is: 0 out of sight, 1 all the way down. Eased, so it slows
// into both ends rather than stopping dead.
double gs_bar_position(const gs_bar *bar, double now);

// Up and finished moving: the window can go.
bool gs_bar_gone(const gs_bar *bar, double now);

// Still moving, so the loop draws at a frame rate rather than idling.
bool gs_bar_moving(const gs_bar *bar, double now);

#endif
