#include "frame.h"

#include <string.h>

void gs_frame_pack(gs_frame *frame, double position, double exit_progress, double clock, const gs_hold *holds,
                   size_t hold_count, const int *joined, size_t joined_count) {
    memset(frame, 0, sizeof *frame);
    frame->magic = GS_FRAME_MAGIC;
    frame->position = (float)position;
    frame->exit_progress = (float)exit_progress;
    frame->clock = (float)clock;
    if (hold_count > GS_HOLDS_MAX) hold_count = GS_HOLDS_MAX;
    if (joined_count > GS_JOINED_MAX) joined_count = GS_JOINED_MAX;
    frame->hold_count = (uint32_t)hold_count;
    frame->joined_count = (uint32_t)joined_count;
    for (size_t i = 0; i < hold_count; i++) {
        frame->hold_fraction[i] = (float)holds[i].fraction;
        frame->hold_player[i] = holds[i].player;
    }
    for (size_t i = 0; i < joined_count; i++) frame->joined[i] = joined[i];
}

bool gs_frame_valid(const gs_frame *frame) {
    return frame->magic == GS_FRAME_MAGIC && frame->hold_count <= GS_HOLDS_MAX &&
           frame->joined_count <= GS_JOINED_MAX;
}
