// The overlay's connection to padmap: one more client on its socket.
//
// Never blocks. A game runs with or without padmap, and an overlay waiting on
// a socket is an exit chord that stops working, so the socket is read only
// when it has something and connected only when it is due -- every couple of
// seconds while it is not there, which picks up a daemon that starts (or
// restarts) mid-game.

#ifndef GOTG_PADLINK_H
#define GOTG_PADLINK_H

#include <stdbool.h>
#include <stddef.h>

#include "pairing.h"

// Long enough for padmap's longest line (an `sdl_mapping` carries a whole
// mapping string per pad); a line longer still is skipped whole.
#define GS_LINK_BUFFER 65536

typedef struct {
    char path[256];
    int fd;
    char buffer[GS_LINK_BUFFER];
    size_t used;
    bool skipping;  // inside a line too long to keep, until its newline
    double next_try;
} gs_link;

// `path` NULL means padmap's own rule: $XDG_RUNTIME_DIR/padmap/padmap.sock --
// and no link at all when there is no XDG_RUNTIME_DIR.
void gs_link_init(gs_link *link, const char *path);

// Connect if not connected and due. Cheap when it is neither.
void gs_link_tick(gs_link *link, double now);

// The descriptor to wait on, or -1 when not connected.
int gs_link_fd(const gs_link *link);

// Read what has arrived and apply every complete line to `pairing`. Returns
// how many events were applied; a closed or broken socket is dropped and
// retried later.
int gs_link_pump(gs_link *link, gs_pairing *pairing, double now);

// Apply every complete line already in the buffer. Split out so a test can
// feed bytes without a socket.
int gs_link_feed(gs_link *link, const char *bytes, size_t length, gs_pairing *pairing, double now);

void gs_link_close(gs_link *link);

#endif
