// The kill switch's decision, with no SDL and no clock of its own.
//
// Split out from main.c so it can be tested: "held for three seconds" is the
// whole feature, and the only way to check it without a controller, a game and
// three seconds of real time is to feed a made-up clock made-up inputs.

#ifndef GOTG_KILLSWITCH_H
#define GOTG_KILLSWITCH_H

#include <stdbool.h>
#include <stdint.h>

// One controller, reduced to the three things the combo asks about.
//
// Left and right are "that shoulder is down" *or* "that trigger is pulled" on
// purpose. Which one a pad reports is a property of the pad, not of the
// player's intent: a Switch Pro reports L and R as buttons where a GameCube
// adapter reports them as axes, and somebody squeezing both and holding Start
// means the same thing on either.
typedef struct {
    bool left;
    bool right;
    bool start;
} ks_input;

typedef struct {
    uint64_t hold_ms;   // how long the combo has to be held to fire
    uint64_t since_ms;  // when the current hold began
    bool holding;       // whether there is a current hold at all
    bool fired;         // one shot per hold, so a held combo fires once
} ks_pad;

void ks_init(ks_pad *pad, uint64_t hold_ms);

// One sample. Returns true exactly once per hold, on the first sample at or
// past hold_ms.
bool ks_step(ks_pad *pad, ks_input in, uint64_t now_ms);

// How long the current hold has run, for the line in the log that tells
// somebody their controller is doing what they meant. Zero when nothing is
// held.
uint64_t ks_held_ms(const ks_pad *pad, uint64_t now_ms);

#endif
