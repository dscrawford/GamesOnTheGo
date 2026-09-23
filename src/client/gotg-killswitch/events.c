#include "events.h"

#include <cjson/cJSON.h>
#include <stdio.h>
#include <string.h>

static void copy_string(const cJSON *object, const char *field, char *out) {
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(object, field);
    out[0] = '\0';
    if (!cJSON_IsString(item) || !item->valuestring) return;
    snprintf(out, GS_KEY_MAX, "%s", item->valuestring);
}

static int player_of(const cJSON *object) {
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(object, "player");
    if (!cJSON_IsNumber(item)) return 0;
    double value = item->valuedouble;
    // A seat is a small positive integer; anything else is "not said".
    if (value < 1.0 || value > 64.0) return 0;
    return (int)value;
}

bool gs_event_parse(const char *line, size_t length, gs_event *event) {
    memset(event, 0, sizeof *event);
    cJSON *root = cJSON_ParseWithLength(line, length);
    if (!root) return false;
    if (!cJSON_IsObject(root)) {
        cJSON_Delete(root);
        return false;
    }
    const cJSON *kind = cJSON_GetObjectItemCaseSensitive(root, "event");
    const char *name = cJSON_IsString(kind) ? kind->valuestring : NULL;

    if (name && strcmp(name, "progress") == 0) {
        const cJSON *frac = cJSON_GetObjectItemCaseSensitive(root, "frac");
        if (cJSON_IsNumber(frac)) {
            event->type = GS_EVENT_PROGRESS;
            event->frac = frac->valuedouble;
            copy_string(root, "node", event->node);
            copy_string(root, "name", event->name);
            event->player = player_of(root);
        }
    } else if (name && strcmp(name, "claim") == 0) {
        event->type = GS_EVENT_CLAIM;
        copy_string(root, "node", event->node);
        copy_string(root, "name", event->name);
        event->player = player_of(root);
    } else if (name && strcmp(name, "state") == 0) {
        event->type = GS_EVENT_STATE;
        const cJSON *players = cJSON_GetObjectItemCaseSensitive(root, "players");
        const cJSON *seat = NULL;
        cJSON_ArrayForEach(seat, players) {
            if (event->seated_count >= GS_SEATED_MAX) break;
            if (!cJSON_IsObject(seat)) continue;
            gs_seat *out = &event->seated[event->seated_count++];
            copy_string(seat, "node", out->node);
            copy_string(seat, "name", out->name);
            out->player = player_of(seat);
        }
    }
    cJSON_Delete(root);
    return true;
}

void gs_event_apply(const gs_event *event, gs_pairing *pairing, double now) {
    switch (event->type) {
        case GS_EVENT_PROGRESS:
            gs_pairing_progress(pairing, event->node, event->name, event->frac, event->player, now);
            break;
        case GS_EVENT_CLAIM:
            gs_pairing_claim(pairing, event->node, event->name, event->player, now);
            break;
        case GS_EVENT_STATE:
            // Only the holds that have become seats. A `state` arrives while
            // somebody is still holding, and clearing everything on one was
            // the flash back to an empty seat in the picker.
            for (size_t i = 0; i < event->seated_count; i++) {
                const gs_seat *seat = &event->seated[i];
                gs_pairing_seated(pairing, seat->node, seat->name, seat->player);
            }
            break;
        case GS_EVENT_OTHER:
            break;
    }
}
