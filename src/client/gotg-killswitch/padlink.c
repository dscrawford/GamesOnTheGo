// SO_PEERCRED and struct ucred.
#define _GNU_SOURCE

#include "padlink.h"

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include "events.h"

// How long to wait before asking again for a socket that was not there.
#define RETRY_SECONDS 2.0

void gs_link_init(gs_link *link, const char *path) {
    memset(link, 0, sizeof *link);
    link->fd = -1;
    if (path && *path) {
        snprintf(link->path, sizeof link->path, "%s", path);
        return;
    }
    // No per-user runtime directory, no link. padmap falls back to /tmp
    // then, where any local user can put a socket first; the joining rings
    // are not worth listening to a stranger for. Empty path: never connects.
    const char *runtime = getenv("XDG_RUNTIME_DIR");
    if (runtime && *runtime) snprintf(link->path, sizeof link->path, "%s/padmap/padmap.sock", runtime);
}

void gs_link_close(gs_link *link) {
    if (link->fd >= 0) close(link->fd);
    link->fd = -1;
    link->used = 0;
    link->skipping = false;
}

void gs_link_tick(gs_link *link, double now) {
    if (link->fd >= 0 || now < link->next_try || !link->path[0]) return;
    link->next_try = now + RETRY_SECONDS;

    struct sockaddr_un address;
    memset(&address, 0, sizeof address);
    address.sun_family = AF_UNIX;
    if (strlen(link->path) >= sizeof address.sun_path) return;
    memcpy(address.sun_path, link->path, strlen(link->path) + 1);

    // Non-blocking from the start. A unix socket does not always answer at
    // once: a listener whose backlog is full -- a daemon that has stopped
    // accepting -- holds a blocking connect until it accepts, and this runs
    // in the loop that watches the kill chord. A connect that cannot finish
    // now is tried again on a later tick.
    int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
    if (fd < 0) return;
    if (connect(fd, (struct sockaddr *)&address, sizeof address) != 0) {
        close(fd);
        return;
    }
    // padmap is this user's own daemon; a socket anyone else answers is not
    // padmap, whatever it is called.
    struct ucred peer;
    socklen_t length = sizeof peer;
    if (getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &peer, &length) != 0 || peer.uid != getuid()) {
        close(fd);
        return;
    }
    link->fd = fd;  // used and skipping were reset when the last one closed
}

int gs_link_fd(const gs_link *link) {
    return link->fd;
}

static int drain(gs_link *link, gs_pairing *pairing, double now) {
    int applied = 0;
    size_t start = 0;
    for (size_t i = 0; i < link->used; i++) {
        if (link->buffer[i] != '\n') continue;
        if (link->skipping) {
            link->skipping = false;
        } else if (i > start) {
            gs_event event;
            if (gs_event_parse(link->buffer + start, i - start, &event)) {
                gs_event_apply(&event, pairing, now);
                applied++;
            }
        }
        start = i + 1;
    }
    // Keep the partial line for the next read.
    memmove(link->buffer, link->buffer + start, link->used - start);
    link->used -= start;
    if (link->used == sizeof link->buffer) {
        // A whole buffer and no newline: this line is too long to keep. Skip
        // to its end rather than lose the framing of every line after it.
        link->used = 0;
        link->skipping = true;
    }
    return applied;
}

int gs_link_feed(gs_link *link, const char *bytes, size_t length, gs_pairing *pairing, double now) {
    int applied = 0;
    while (length > 0) {
        size_t room = sizeof link->buffer - link->used;
        size_t take = length < room ? length : room;
        memcpy(link->buffer + link->used, bytes, take);
        link->used += take;
        bytes += take;
        length -= take;
        applied += drain(link, pairing, now);
    }
    return applied;
}

// Reads per pump. Bounded, because the kill chord is looked at between pumps:
// a peer that never stops talking would otherwise hold this loop for good.
#define READS_PER_PUMP 16

int gs_link_pump(gs_link *link, gs_pairing *pairing, double now) {
    if (link->fd < 0) return 0;
    int applied = 0;
    char chunk[8192];
    for (int reads = 0; reads < READS_PER_PUMP; reads++) {
        ssize_t got = read(link->fd, chunk, sizeof chunk);
        if (got > 0) {
            applied += gs_link_feed(link, chunk, (size_t)got, pairing, now);
            continue;
        }
        if (got < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) break;
        if (got < 0 && errno == EINTR) continue;
        // Closed, or broken: gone until the next tick finds it again.
        gs_link_close(link);
        break;
    }
    return applied;
}
