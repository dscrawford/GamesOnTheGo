// The few padmap events the overlay listens to, read off one line of JSON.
//
// padmap broadcasts to every client on its socket; the overlay is one more,
// beside the picker and the gate. It wants three things: a hold filling
// (`progress`), a seat taken (`claim`), and who is seated (`state`) -- and
// nothing else, so everything else parses to GS_EVENT_OTHER and is ignored.

#ifndef GOTG_EVENTS_H
#define GOTG_EVENTS_H

#include <stdbool.h>
#include <stddef.h>

#include "pairing.h"

#define GS_SEATED_MAX 8

typedef enum {
    GS_EVENT_OTHER = 0,
    GS_EVENT_PROGRESS,
    GS_EVENT_CLAIM,
    GS_EVENT_STATE,
} gs_event_type;

typedef struct {
    char node[GS_KEY_MAX];
    char name[GS_KEY_MAX];
    int player;
} gs_seat;

typedef struct {
    gs_event_type type;
    double frac;             // progress
    char node[GS_KEY_MAX];   // progress, claim
    char name[GS_KEY_MAX];   // progress, claim
    int player;              // progress, claim; 0 when unsaid
    gs_seat seated[GS_SEATED_MAX];  // state
    size_t seated_count;
} gs_event;

// One line, without its newline. False for anything that is not a JSON
// object; an object this does not know is GS_EVENT_OTHER, and true.
bool gs_event_parse(const char *line, size_t length, gs_event *event);

// What one event does to the pairing picture.
void gs_event_apply(const gs_event *event, gs_pairing *pairing, double now);

#endif
