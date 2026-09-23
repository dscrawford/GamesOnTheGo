// Who is holding a button to join, in the order they started -- over a game.
//
// The picker draws this in its seat strip (gotg_ui/joining.py); a game had no
// way to say it at all, so a controller picked up mid-level took a seat with
// nothing on screen to show the hold was counting. The overlay draws it now,
// from the same padmap events and by the same rules, which were measured
// before they were written down:
//
//   - A named reading's release is said out loud (`frac: 0`), so silence is
//     only a safety net for it. padmap sends progress from the loop that
//     also rescans every device, and on a desktop with many of them readings
//     arrive ~58 ms apart: taking 50 ms of quiet as a release dropped a
//     steady press between every reading.
//   - A nameless reading (an older daemon) has no release to say, so three
//     frames of silence still ends it.
//   - A reading smaller than the last one is a new press, at the back.
//   - Between readings the fill carries on at the hold's own rate, and never
//     steps backwards when the next reading lands a hair behind it.
//
// No SDL and no clock of its own: the loop passes the time, so a test can.

#ifndef GOTG_PAIRING_H
#define GOTG_PAIRING_H

#include <stdbool.h>
#include <stddef.h>

#define GS_HOLDS_MAX 8
#define GS_JOINED_MAX 4
#define GS_KEY_MAX 96

// Silence that ends a hold: a nameless one at once, a named one only if the
// daemon has gone away mid-press. See pairing.c.
#define GS_STALE_ANONYMOUS 0.05
#define GS_STALE_NAMED 0.5

// How long a seat that has just been taken stays on screen, ticked, before
// the bar goes back up.
#define GS_JOINED_SHOWN 1.5

typedef struct {
    char key[GS_KEY_MAX];  // node, else name, else "" for a nameless reading
    double fraction;       // the last reading
    double started;        // when this press began, for the order
    double seen;           // when the last reading came
    double drawn;          // the furthest it has been drawn
    int player;            // the seat it is filling towards, 0 if unsaid
    bool used;
} gs_hold;

typedef struct {
    int player;
    double at;
    bool used;
} gs_joined;

typedef struct {
    gs_hold holds[GS_HOLDS_MAX];
    gs_joined joined[GS_JOINED_MAX];
    double hold_seconds;  // the length padmap was asked for, to carry fills
    bool named;           // this daemon names its readings
} gs_pairing;

void gs_pairing_init(gs_pairing *pairing, double hold_seconds);

// One `progress` event. `node` and `name` may be NULL or empty; `player` is 0
// when the event did not say.
void gs_pairing_progress(gs_pairing *pairing, const char *node, const char *name, double frac, int player,
                         double now);

// A seat was taken: that pad's hold is over -- everybody else's is not -- and
// the new seat shows as joined for a moment. With no node or name, every hold.
void gs_pairing_claim(gs_pairing *pairing, const char *node, const char *name, int player, double now);

// A `state` says this pad is seated: its hold is finished, nobody else's.
// By identity, not by seat number -- a reading's number is the seat it is
// filling towards, and it is stale for a tick after somebody else's claim.
void gs_pairing_seated(gs_pairing *pairing, const char *node, const char *name, int player);

// The holds to draw, oldest press first, carried forward to `now`. Drops the
// ones whose readings stopped. Returns how many were written.
size_t gs_pairing_now(gs_pairing *pairing, double now, gs_hold *out, size_t max);

// The seats that joined in the last GS_JOINED_SHOWN seconds, oldest first.
size_t gs_pairing_joined(gs_pairing *pairing, double now, int *players, size_t max);

// Whether there is anything to show.
bool gs_pairing_busy(gs_pairing *pairing, double now);

#endif
