// The painter: this same program, run as `gotg-killswitch --paint`, drawing
// the frames it is sent until its pipe closes. See frame.h for why it is a
// process of its own.
//
// The kill switch side never waits on it. Frames go down a non-blocking pipe
// and a full one drops them; a painter that dies is started again after a
// pause that grows each time; one that is told to go is reaped on later
// ticks and killed if it has not gone by then.

#ifndef GOTG_PAINTER_H
#define GOTG_PAINTER_H

#include <stdbool.h>
#include <sys/types.h>

#include "frame.h"

typedef struct {
    pid_t pid;           // 0 when there is no painter
    int fd;              // the write end of its pipe, -1 when told to go or none
    double retry_at;     // after a failed start, not before this
    double reap_by;      // told to go: killed if still here at this time
    unsigned failures;   // painters in a row that went by themselves
    gs_frame last_sent;  // so an unchanged picture is not sent again
    bool sent_any;
} gs_painter;

void gs_painter_init(gs_painter *painter);

// Start one if there is none and one is due. `self` is this program's path.
void gs_painter_ensure(gs_painter *painter, const char *self, double now);

// Send a frame unless it is the one last sent. Never waits.
void gs_painter_send(gs_painter *painter, const gs_frame *frame, double now);

// Tell it to go -- close its pipe -- without waiting for it. gs_painter_tick
// reaps it on a later pass.
void gs_painter_release(gs_painter *painter, double now);

// Reap a painter that was told to go, killing it past its time. Cheap.
void gs_painter_tick(gs_painter *painter, double now);

// Tell it to go and wait for it: for the way out of the program, where there
// is no later pass to reap on.
void gs_painter_close(gs_painter *painter);

// The painter's own side: open a window, draw every new frame that arrives on
// stdin, exit when stdin closes. The process's exit status.
int gs_paint_main(void);

#endif
