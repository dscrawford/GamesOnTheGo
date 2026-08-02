// gotg-pads — what SDL actually sees, as JSON.
//
// Everything downstream needs to agree with the emulators about which physical
// controller is which, and the only way to be sure of that is to ask the same
// library they ask. sdl-jstest is not a substitute: it links SDL2, so it cannot
// see hidapi-only devices at all, and its output is nobody's stability contract.
//
// A dumper, not a library. It prints and exits.

#include <SDL3/SDL.h>
#include <stdio.h>
#include <string.h>

// JSON string body, escaped enough for the characters a controller name can
// really contain.
static void print_json_string(const char *s) {
    putchar('"');
    for (; s && *s; s++) {
        unsigned char c = (unsigned char)*s;
        switch (c) {
        case '"':  fputs("\\\"", stdout); break;
        case '\\': fputs("\\\\", stdout); break;
        case '\n': fputs("\\n", stdout);  break;
        case '\r': fputs("\\r", stdout);  break;
        case '\t': fputs("\\t", stdout);  break;
        default:
            if (c < 0x20) printf("\\u%04x", c);
            else putchar(c);
        }
    }
    putchar('"');
}

static void print_json_string_or_null(const char *s) {
    if (s && *s) print_json_string(s);
    else fputs("null", stdout);
}

// The string ares identifies a controller by, built exactly as
// ruby/input/joypad/sdl.cpp does — the SDL GUID, or a VID/PID pair when the
// GUID comes back all zeros. It matters that this matches character for
// character: ares looks a binding up by comparing this against the identifier
// stored in settings.bml, so a difference of any kind is a controller that
// silently has no buttons.
static void ares_identity(SDL_JoystickID id, char *out, size_t n) {
    char guid[33] = {0};
    SDL_GUIDToString(SDL_GetJoystickGUIDForID(id), guid, sizeof(guid));

    if (*guid && strcmp(guid, "00000000000000000000000000000000") != 0) {
        SDL_strlcpy(out, guid, n);
        return;
    }

    // ares substitutes its generic ids for a missing vendor or product, and
    // formats both in decimal.
    Uint16 vid = SDL_GetJoystickVendorForID(id);
    Uint16 pid = SDL_GetJoystickProductForID(id);
    if (vid == 0) vid = 0x0000;  // HID::Joypad::GenericVendorID
    if (pid == 0) pid = 0x0003;  // HID::Joypad::GenericProductID
    SDL_snprintf(out, n, "VID:%u|PID:%u", (unsigned)vid, (unsigned)pid);
}

int main(void) {
    // See what the emulators see. The current Steam Controller is a hidapi
    // device whose SDL3 driver falls back to SDL_HINT_JOYSTICK_HIDAPI, so
    // without this it is invisible here while being visible in a game — the
    // most confusing possible disagreement.
    SDL_SetHint(SDL_HINT_JOYSTICK_HIDAPI_STEAM, "1");

    if (!SDL_Init(SDL_INIT_JOYSTICK | SDL_INIT_GAMEPAD)) {
        fprintf(stderr, "gotg-pads: SDL_Init: %s\n", SDL_GetError());
        return 1;
    }

    // hidapi devices are opened on a background thread, so a bare enumeration
    // straight after init can miss one that is plugged in and working.
    for (int i = 0; i < 10; i++) {
        SDL_UpdateJoysticks();
        SDL_PumpEvents();
        SDL_Delay(50);
    }

    int count = 0;
    SDL_JoystickID *ids = SDL_GetJoysticks(&count);
    if (!ids) {
        fprintf(stderr, "gotg-pads: SDL_GetJoysticks: %s\n", SDL_GetError());
        SDL_Quit();
        return 1;
    }

    fputs("[", stdout);
    for (int i = 0; i < count; i++) {
        SDL_JoystickID id = ids[i];

        char guid[33] = {0};
        SDL_GUIDToString(SDL_GetJoystickGUIDForID(id), guid, sizeof(guid));

        char identity[64] = {0};
        ares_identity(id, identity, sizeof(identity));

        // ares' slot: how many devices with this same *identity* came before
        // this one in SDL's enumeration order. Two identical pads are told
        // apart by nothing else, so this has to match
        // ruby/input/joypad/sdl.cpp exactly — getting it wrong silently swaps
        // player one and player two.
        int slot = 0;
        for (int j = 0; j < i; j++) {
            char other[64] = {0};
            ares_identity(ids[j], other, sizeof(other));
            if (strcmp(other, identity) == 0) slot++;
        }

        Uint16 vid = SDL_GetJoystickVendorForID(id);
        Uint16 pid = SDL_GetJoystickProductForID(id);
        const char *path = SDL_GetJoystickPathForID(id);

        // A Steam Virtual Gamepad is Steam Input presenting something else.
        // Its player index is the seat Steam has already assigned, which Phase 6
        // uses instead of matching the device itself.
        int steam_slot = -1;
        if (vid == 0x28de && pid == 0x11ff)
            steam_slot = SDL_GetJoystickPlayerIndexForID(id);

        printf("%s\n  {", i ? "," : "");
        printf("\"instance\": %u, ", (unsigned)id);
        fputs("\"name\": ", stdout);
        print_json_string_or_null(SDL_GetJoystickNameForID(id));
        printf(", \"guid\": \"%s\"", guid);
        // identity + slot is what an ares binding is keyed on, so it is emitted
        // ready to use rather than left to be reassembled downstream.
        fputs(", \"identity\": ", stdout);
        print_json_string(identity);
        printf(", \"slot\": %d", slot);
        printf(", \"vid\": \"%04x\", \"pid\": \"%04x\"", vid, pid);
        printf(", \"gamepad\": %s", SDL_IsGamepad(id) ? "true" : "false");

        fputs(", \"path\": ", stdout);
        print_json_string_or_null(path);

        // Distinguished on purpose: a device with no evdev node cannot be bound
        // by anything that speaks evdev, and downstream has to be able to tell.
        fputs(", \"evdev\": ", stdout);
        if (path && strncmp(path, "/dev/input/event", 16) == 0) print_json_string(path);
        else fputs("null", stdout);

        if (steam_slot >= 0) printf(", \"steamSlot\": %d", steam_slot);
        else fputs(", \"steamSlot\": null", stdout);

        fputs("}", stdout);
    }
    fputs(count ? "\n]\n" : "]\n", stdout);

    SDL_free(ids);
    SDL_Quit();
    return 0;
}
