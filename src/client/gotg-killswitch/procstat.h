// What /proc says about the process we are guarding.
//
// Two questions, both of which the obvious call gets wrong. "Is it still
// running?" is not kill(pid, 0): a game whose parent has not reaped it yet is
// a zombie, and a zombie answers that exactly like a healthy process. "Is it
// still the *same* process?" has no signal-based answer at all — pids are
// recycled, and a watcher that sits for hours can outlive one.
//
// Kept apart from the SDL half so the parsing can be tested against lines a
// real /proc would be inconvenient to produce.

#ifndef GOTG_PROCSTAT_H
#define GOTG_PROCSTAT_H

#include <stdbool.h>
#include <stddef.h>
#include <sys/types.h>

// The contents of /proc/<pid>/stat. False when there is nothing to read.
bool proc_read_stat(pid_t pid, char *line, size_t size);

// The state letter — 'R', 'S', 'Z' — or 0 when the line does not parse.
char proc_stat_state(const char *line);

// The process's start time in clock ticks since boot: the one field that makes
// a pid identify a process rather than a slot. 0 when it cannot be read, which
// means "do not claim to know".
unsigned long long proc_stat_starttime(const char *line);

// Whether that pid is a process that has not exited. A pid we may not signal
// counts as alive: it exists, and it is not ours to judge.
bool proc_alive(pid_t pid);

// This pid's start time, for pinning an identity to it.
unsigned long long proc_started(pid_t pid);

#endif
