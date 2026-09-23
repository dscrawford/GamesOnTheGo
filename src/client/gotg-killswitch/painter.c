#define _POSIX_C_SOURCE 200809L

#include "painter.h"

#include <SDL3/SDL.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

#include "overlay.h"
#include "scene.h"

extern char **environ;

// After a painter that would not start or went by itself: two seconds, then
// four, up to half a minute. A machine with no display would otherwise start
// one every couple of seconds for as long as somebody was joining.
#define RETRY_FIRST 2.0
#define RETRY_MOST 30.0

// How long a painter gets to go after its pipe closes, before it is killed.
#define GRACE_SECONDS 0.5

void gs_painter_init(gs_painter *painter) {
    memset(painter, 0, sizeof *painter);
    painter->fd = -1;
}

// Gone without being asked: wait before trying again, longer each time.
static void lost(gs_painter *painter, double now) {
    if (painter->fd >= 0) close(painter->fd);
    painter->fd = -1;
    if (painter->pid > 0) {
        painter->reap_by = now;  // tick reaps it, or kills what is left of it
    }
    double wait = RETRY_FIRST;
    for (unsigned i = 0; i < painter->failures && wait < RETRY_MOST; i++) wait *= 2.0;
    painter->retry_at = now + (wait < RETRY_MOST ? wait : RETRY_MOST);
    painter->failures++;
}

void gs_painter_ensure(gs_painter *painter, const char *self, double now) {
    // One painter at a time: a new one waits until the last has been reaped.
    if (painter->pid > 0 || now < painter->retry_at) return;

    int ends[2];
    if (pipe(ends) != 0) {
        lost(painter, now);
        return;
    }
    // The write end is ours alone and never blocks; the read end becomes the
    // painter's stdin and nothing else of ours.
    fcntl(ends[1], F_SETFD, FD_CLOEXEC);
    fcntl(ends[1], F_SETFL, fcntl(ends[1], F_GETFL) | O_NONBLOCK);

    posix_spawn_file_actions_t actions;
    posix_spawn_file_actions_init(&actions);
    posix_spawn_file_actions_adddup2(&actions, ends[0], STDIN_FILENO);
    posix_spawn_file_actions_addclose(&actions, ends[0]);
    char *argv[] = {(char *)"gotg-killswitch", (char *)"--paint", NULL};
    pid_t pid = 0;
    int failed = posix_spawn(&pid, self, &actions, NULL, argv, environ);
    posix_spawn_file_actions_destroy(&actions);
    close(ends[0]);
    if (failed) {
        close(ends[1]);
        lost(painter, now);
        return;
    }
    painter->pid = pid;
    painter->fd = ends[1];
    painter->sent_any = false;
}

void gs_painter_send(gs_painter *painter, const gs_frame *frame, double now) {
    if (painter->fd < 0) return;
    // The same picture again is nothing to send: a joined seat's badge sits
    // still for a second and a half, and the painter only draws what is new.
    if (painter->sent_any && memcmp(&painter->last_sent, frame, sizeof *frame) == 0) return;
    // Smaller than PIPE_BUF, so it goes whole or not at all. A painter that
    // has exited shows up here as EPIPE (SIGPIPE is ignored).
    ssize_t wrote = write(painter->fd, frame, sizeof *frame);
    if (wrote == (ssize_t)sizeof *frame) {
        painter->last_sent = *frame;
        painter->sent_any = true;
        return;
    }
    if (wrote < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) return;  // behind: this one is dropped
    lost(painter, now);
}

void gs_painter_release(gs_painter *painter, double now) {
    if (painter->fd >= 0) close(painter->fd);
    painter->fd = -1;
    if (painter->pid > 0 && painter->reap_by == 0.0) painter->reap_by = now + GRACE_SECONDS;
    // It went because it was asked to: whatever failed before is behind us.
    painter->failures = 0;
}

void gs_painter_tick(gs_painter *painter, double now) {
    if (painter->pid <= 0 || painter->fd >= 0) return;
    if (waitpid(painter->pid, NULL, WNOHANG) == painter->pid) {
        painter->pid = 0;
        painter->reap_by = 0.0;
        return;
    }
    if (now >= painter->reap_by) {
        // Stuck in a compositor call, most likely. Whatever it was waiting on
        // is not worth keeping a process for.
        kill(painter->pid, SIGKILL);
        waitpid(painter->pid, NULL, 0);
        painter->pid = 0;
        painter->reap_by = 0.0;
    }
}

void gs_painter_close(gs_painter *painter) {
    gs_painter_release(painter, 0.0);
    for (int waited = 0; painter->pid > 0 && waited < 500; waited += 10) {
        if (waitpid(painter->pid, NULL, WNOHANG) == painter->pid) {
            painter->pid = 0;
            return;
        }
        SDL_Delay(10);
    }
    if (painter->pid > 0) {
        kill(painter->pid, SIGKILL);
        waitpid(painter->pid, NULL, 0);
        painter->pid = 0;
    }
}

// --- the painter's own side ---------------------------------------------------

static gs_vertex mesh_vertices[GS_MESH_VERTICES];
static int mesh_indices[GS_MESH_INDICES];

// Read what has arrived and keep the newest whole, valid frame. False when the
// pipe has closed: the bar is up and the painter's work is done.
static bool newest(unsigned char *pending, size_t *used, size_t size, gs_frame *latest, bool *fresh) {
    for (;;) {
        ssize_t got = read(STDIN_FILENO, pending + *used, size - *used);
        if (got == 0) return false;
        if (got < 0) {
            if (errno == EINTR) continue;
            return errno == EAGAIN || errno == EWOULDBLOCK;
        }
        *used += (size_t)got;
        // Frames arrive whole but a read can end mid-frame when several are
        // waiting; the part-frame stays for the next read. Only the newest
        // whole one matters -- an older one is a picture of the past.
        size_t whole = *used / sizeof(gs_frame);
        if (whole == 0) continue;
        gs_frame candidate;
        memcpy(&candidate, pending + (whole - 1) * sizeof(gs_frame), sizeof candidate);
        if (gs_frame_valid(&candidate)) {
            *latest = candidate;
            *fresh = true;
        }
        memmove(pending, pending + whole * sizeof(gs_frame), *used - whole * sizeof(gs_frame));
        *used -= whole * sizeof(gs_frame);
    }
}

int gs_paint_main(void) {
    signal(SIGPIPE, SIG_IGN);
    fcntl(STDIN_FILENO, F_SETFL, fcntl(STDIN_FILENO, F_GETFL) | O_NONBLOCK);

    overlay *window = overlay_open();
    if (!window) return 1;
    int width;
    float bar_height;
    overlay_size(window, &width, &bar_height);
    // What QA reads to find the bar in a recording: the size it actually drew.
    fprintf(stderr, "gotg-killswitch: overlay up (%s, %s) width=%d bar=%.0f\n", overlay_kind(window),
            overlay_renderer(window), width, bar_height);

    gs_mesh mesh = {mesh_vertices, mesh_indices, 0, 0, GS_MESH_VERTICES, GS_MESH_INDICES};
    gs_frame latest;
    unsigned char pending[sizeof(gs_frame) * 8];
    size_t used = 0;

    for (;;) {
        // Asleep until the kill switch sends something new: it sends only
        // when the picture changed, so an unchanged bar costs no redraws.
        struct pollfd wait = {STDIN_FILENO, POLLIN, 0};
        poll(&wait, 1, -1);
        bool fresh = false;
        if (!newest(pending, &used, sizeof pending, &latest, &fresh)) break;
        if (!fresh) continue;

        overlay_size(window, &width, &bar_height);
        gs_scene scene = {
            .width = width,
            .bar_height = bar_height,
            .position = latest.position,
            .fractions = latest.hold_fraction,
            .players = latest.hold_player,
            .hold_count = latest.hold_count,
            .joined = latest.joined,
            .joined_count = latest.joined_count,
            .exit_progress = latest.exit_progress,
            .clock = latest.clock,
        };
        gs_scene_build(&scene, &mesh);
        overlay_draw(window, &mesh);
        // Where presenting does not wait for the panel, a frame's worth, so
        // a flood of frames cannot turn into a spin.
        if (!overlay_vsync(window)) SDL_Delay(16);
    }
    overlay_close(window);
    return 0;
}
