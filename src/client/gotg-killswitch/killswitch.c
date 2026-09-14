#include "killswitch.h"

// All three at once. Two shoulders is a grip somebody could stumble into;
// two shoulders and Start, held past three seconds, is not — which is the
// entire design brief for a control that must never fire by accident and must
// exist on every pad.
static bool combo_down(ks_input in) {
    return in.left && in.right && in.start;
}

void ks_init(ks_pad *pad, uint64_t hold_ms) {
    pad->hold_ms = hold_ms;
    pad->since_ms = 0;
    pad->holding = false;
    pad->fired = false;
}

bool ks_step(ks_pad *pad, ks_input in, uint64_t now_ms) {
    if (!combo_down(in)) {
        // Letting go of any one of them starts the three seconds over. A
        // switch that counted cumulative time would fire on a long session of
        // ordinary shoulder-button play.
        pad->holding = false;
        pad->fired = false;
        return false;
    }

    if (!pad->holding) {
        pad->holding = true;
        pad->since_ms = now_ms;
        pad->fired = false;
    } else if (now_ms < pad->since_ms) {
        // A clock that went backwards. Not expected from a monotonic source,
        // but the alternative is an enormous elapsed time and an instant kill.
        pad->since_ms = now_ms;
    }

    if (pad->fired) return false;
    if (now_ms - pad->since_ms < pad->hold_ms) return false;

    pad->fired = true;
    return true;
}

uint64_t ks_held_ms(const ks_pad *pad, uint64_t now_ms) {
    if (!pad->holding || now_ms < pad->since_ms) return 0;
    return now_ms - pad->since_ms;
}
