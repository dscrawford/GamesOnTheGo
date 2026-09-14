#define _POSIX_C_SOURCE 200809L

#include "procstat.h"

#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

// Where every field after the process name begins.
//
// The name is parenthesised and is whatever the program was called — it can
// contain spaces, and it can contain a close paren. So the fields are found
// from the *last* one in the line rather than by counting from the left,
// which is the difference between reading a process called ") Z 1 1 1" right
// and believing whatever it decided to claim.
static const char *after_comm(const char *line) {
    const char *end = line ? strrchr(line, ')') : NULL;
    return end ? end + 1 : NULL;
}

char proc_stat_state(const char *line) {
    const char *rest = after_comm(line);
    if (!rest) return 0;
    while (*rest == ' ') rest++;
    return *rest ? *rest : 0;
}

unsigned long long proc_stat_starttime(const char *line) {
    const char *rest = after_comm(line);
    if (!rest) return 0;

    // starttime is field 22, and the first field here is the state (field 3),
    // so it is the twentieth token along.
    for (int field = 0; field < 19; field++) {
        while (*rest == ' ') rest++;
        if (!*rest) return 0;
        while (*rest && *rest != ' ') rest++;
    }
    while (*rest == ' ') rest++;
    if (*rest < '0' || *rest > '9') return 0;

    errno = 0;
    char *stop = NULL;
    unsigned long long value = strtoull(rest, &stop, 10);
    if (errno || stop == rest) return 0;
    return value;
}

bool proc_read_stat(pid_t pid, char *line, size_t size) {
    if (size == 0) return false;
    line[0] = '\0';

    char path[64];
    snprintf(path, sizeof(path), "/proc/%d/stat", (int)pid);
    FILE *stat = fopen(path, "re");
    if (!stat) return false;

    size_t got = fread(line, 1, size - 1, stat);
    fclose(stat);
    line[got] = '\0';
    return got > 0;
}

bool proc_alive(pid_t pid) {
    if (kill(pid, 0) != 0 && errno != EPERM) return false;

    char line[512];
    if (!proc_read_stat(pid, line, sizeof line)) {
        // No /proc to ask, but the signal said the pid exists. Believing the
        // signal is the conservative answer: it delays a report of the game
        // ending, where the other way round would kill on a guess.
        return true;
    }
    return proc_stat_state(line) != 'Z';
}

unsigned long long proc_started(pid_t pid) {
    char line[512];
    if (!proc_read_stat(pid, line, sizeof line)) return 0;
    return proc_stat_starttime(line);
}
