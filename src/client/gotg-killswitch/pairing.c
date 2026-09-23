#include "pairing.h"

#include <stdio.h>
#include <string.h>

static const char *key_of(const char *node, const char *name) {
    if (node && *node) return node;
    if (name && *name) return name;
    return "";
}

static void set_key(gs_hold *hold, const char *key) {
    // Truncated rather than refused: two keys that share their first 95
    // bytes are two device paths nobody has.
    snprintf(hold->key, sizeof hold->key, "%s", key);
}

static gs_hold *find(gs_pairing *pairing, const char *key) {
    for (size_t i = 0; i < GS_HOLDS_MAX; i++) {
        gs_hold *hold = &pairing->holds[i];
        if (hold->used && strncmp(hold->key, key, GS_KEY_MAX - 1) == 0) return hold;
    }
    return NULL;
}

static gs_hold *slot(gs_pairing *pairing) {
    for (size_t i = 0; i < GS_HOLDS_MAX; i++) {
        if (!pairing->holds[i].used) return &pairing->holds[i];
    }
    // Full: the oldest press gives way. Eight people holding buttons at once
    // is a party this screen has no room to draw anyway.
    gs_hold *oldest = &pairing->holds[0];
    for (size_t i = 1; i < GS_HOLDS_MAX; i++) {
        if (pairing->holds[i].started < oldest->started) oldest = &pairing->holds[i];
    }
    return oldest;
}

void gs_pairing_init(gs_pairing *pairing, double hold_seconds) {
    memset(pairing, 0, sizeof *pairing);
    pairing->hold_seconds = hold_seconds;
}

void gs_pairing_progress(gs_pairing *pairing, const char *node, const char *name, double frac, int player,
                         double now) {
    const char *key = key_of(node, name);
    if (*key) pairing->named = true;
    gs_hold *found = find(pairing, key);
    if (frac <= 0.0) {
        // A release said out loud -- the one thing silence cannot tell apart
        // when two pads are holding.
        if (found) found->used = false;
        return;
    }
    if (!found || frac < found->fraction - 1e-6) {
        // A new hold, or the same pad starting again: padmap's fraction only
        // climbs within one press, so a smaller one is a different press, and
        // it goes to the back however small the gap.
        gs_hold *hold = found ? found : slot(pairing);
        memset(hold, 0, sizeof *hold);
        set_key(hold, key);
        hold->fraction = frac;
        hold->started = now;
        hold->seen = now;
        hold->player = player;
        hold->used = true;
        return;
    }
    found->fraction = frac;
    found->seen = now;
    if (player > 0) found->player = player;
}

void gs_pairing_claim(gs_pairing *pairing, const char *node, const char *name, int player, double now) {
    // Only the pad that took the seat stops filling. Two people holding A a
    // moment apart are two seats, and clearing every hold on the first claim
    // drew the second one's ring empty while they were still holding -- the
    // picture of padmap forgetting them, whether or not it had. A claim that
    // names nobody cannot say whose fill it ended, so it ends them all.
    if (*key_of(node, name)) {
        gs_pairing_seated(pairing, node, name, player);
    } else {
        for (size_t i = 0; i < GS_HOLDS_MAX; i++) pairing->holds[i].used = false;
    }
    if (player <= 0) return;
    gs_joined *free_slot = NULL;
    for (size_t i = 0; i < GS_JOINED_MAX; i++) {
        gs_joined *joined = &pairing->joined[i];
        if (joined->used && joined->player == player) {
            joined->at = now;
            return;
        }
        if (!joined->used && !free_slot) free_slot = joined;
    }
    if (!free_slot) {
        free_slot = &pairing->joined[0];
        for (size_t i = 1; i < GS_JOINED_MAX; i++) {
            if (pairing->joined[i].at < free_slot->at) free_slot = &pairing->joined[i];
        }
    }
    free_slot->player = player;
    free_slot->at = now;
    free_slot->used = true;
}

void gs_pairing_seated(gs_pairing *pairing, const char *node, const char *name, int player) {
    for (size_t i = 0; i < GS_HOLDS_MAX; i++) {
        gs_hold *hold = &pairing->holds[i];
        if (!hold->used) continue;
        bool gone;
        if (*hold->key) {
            gone = (node && *node && strncmp(hold->key, node, GS_KEY_MAX - 1) == 0) ||
                   (name && *name && strncmp(hold->key, name, GS_KEY_MAX - 1) == 0);
        } else {
            // Nameless: the seat number is all there is.
            gone = player > 0 && hold->player == player;
        }
        if (gone) hold->used = false;
    }
}

static void expire(gs_pairing *pairing, double now) {
    for (size_t i = 0; i < GS_HOLDS_MAX; i++) {
        gs_hold *hold = &pairing->holds[i];
        if (!hold->used) continue;
        double stale = *hold->key ? GS_STALE_NAMED : GS_STALE_ANONYMOUS;
        if (now - hold->seen > stale) hold->used = false;
    }
    for (size_t i = 0; i < GS_JOINED_MAX; i++) {
        gs_joined *joined = &pairing->joined[i];
        if (joined->used && now - joined->at > GS_JOINED_SHOWN) joined->used = false;
    }
}

size_t gs_pairing_now(gs_pairing *pairing, double now, gs_hold *out, size_t max) {
    expire(pairing, now);
    size_t count = 0;
    for (size_t i = 0; i < GS_HOLDS_MAX && count < max; i++) {
        gs_hold *hold = &pairing->holds[i];
        if (!hold->used) continue;
        double carried = hold->fraction;
        if (pairing->hold_seconds > 0.0 && now > hold->seen) {
            carried += (now - hold->seen) / pairing->hold_seconds;
        }
        if (carried > 1.0) carried = 1.0;
        if (carried < hold->drawn) carried = hold->drawn;
        hold->drawn = carried;

        // Insertion by start time, so the first press is drawn first.
        size_t at = count;
        while (at > 0 && out[at - 1].started > hold->started) {
            out[at] = out[at - 1];
            at--;
        }
        out[at] = *hold;
        out[at].fraction = carried;
        count++;
    }
    return count;
}

size_t gs_pairing_joined(gs_pairing *pairing, double now, int *players, size_t max) {
    expire(pairing, now);
    gs_joined sorted[GS_JOINED_MAX];
    size_t count = 0;
    for (size_t i = 0; i < GS_JOINED_MAX; i++) {
        if (!pairing->joined[i].used) continue;
        size_t at = count;
        while (at > 0 && sorted[at - 1].at > pairing->joined[i].at) {
            sorted[at] = sorted[at - 1];
            at--;
        }
        sorted[at] = pairing->joined[i];
        count++;
    }
    if (count > max) count = max;
    for (size_t i = 0; i < count; i++) players[i] = sorted[i].player;
    return count;
}

bool gs_pairing_busy(gs_pairing *pairing, double now) {
    expire(pairing, now);
    for (size_t i = 0; i < GS_HOLDS_MAX; i++) {
        if (pairing->holds[i].used) return true;
    }
    for (size_t i = 0; i < GS_JOINED_MAX; i++) {
        if (pairing->joined[i].used) return true;
    }
    return false;
}
