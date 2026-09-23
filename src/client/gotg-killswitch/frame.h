// What the kill switch tells its painter to draw: one frame of the bar, small
// enough to cross a pipe in a single atomic write.
//
// The drawing is a separate process. The kill switch's loop is the one thing
// that must keep running while a game is hung -- it is what stops the game --
// and drawing means talking to a compositor: a round trip, a present that
// waits for the panel, a connect. Any of those can stall, and a stall in the
// same loop was a kill chord that did nothing. So the kill switch keeps the
// chord, padmap's socket and every decision, and hands the painter only this.
// A painter that hangs misses frames; the kill switch does not notice.

#ifndef GOTG_FRAME_H
#define GOTG_FRAME_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "pairing.h"

#define GS_FRAME_MAGIC 0x56534f47u  // "GOSV", so a torn or foreign read is refused

typedef struct {
    uint32_t magic;
    float position;       // the bar: 0 out of sight, 1 all the way down
    float exit_progress;  // 0..1 through the exit hold
    float clock;          // seconds, for the spinner
    uint32_t hold_count;
    uint32_t joined_count;
    float hold_fraction[GS_HOLDS_MAX];
    int32_t hold_player[GS_HOLDS_MAX];
    int32_t joined[GS_JOINED_MAX];
} gs_frame;

// Filled from what the kill switch knows, clamped to what a frame can hold.
void gs_frame_pack(gs_frame *frame, double position, double exit_progress, double clock, const gs_hold *holds,
                   size_t hold_count, const int *joined, size_t joined_count);

// Whether bytes read off the pipe are a frame this painter can draw. Refuses
// the wrong magic and counts past the arrays, so nothing downstream indexes
// past them whatever arrived.
bool gs_frame_valid(const gs_frame *frame);

#endif
