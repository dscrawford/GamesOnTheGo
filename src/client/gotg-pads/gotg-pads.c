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

// Which raw joystick element drives each standard gamepad element.
//
// This is the piece that makes generated bindings possible at all. An emulator
// binds raw indices — "button 6" — while a person thinks in "Start", and the
// two only line up per controller model. SDL already knows the correspondence
// for anything in its mapping database, so ask it rather than asking the user
// to press every button in an emulator's settings screen.
static void print_gamepad_map(SDL_Gamepad *pad) {
    if (!pad) {
        fputs("null", stdout);
        return;
    }

    int n = 0;
    SDL_GamepadBinding **binds = SDL_GetGamepadBindings(pad, &n);
    if (!binds) {
        fputs("null", stdout);
        return;
    }

    fputs("{", stdout);
    int written = 0;
    for (int i = 0; i < n; i++) {
        const SDL_GamepadBinding *b = binds[i];

        // Only the standard elements a person names. An unmapped output is of
        // no use to a generator.
        const char *out = NULL;
        if (b->output_type == SDL_GAMEPAD_BINDTYPE_BUTTON)
            out = SDL_GetGamepadStringForButton(b->output.button);
        else if (b->output_type == SDL_GAMEPAD_BINDTYPE_AXIS)
            out = SDL_GetGamepadStringForAxis(b->output.axis.axis);
        if (!out) continue;

        printf("%s\n    ", written++ ? "," : "");
        print_json_string(out);
        fputs(": ", stdout);

        switch (b->input_type) {
        case SDL_GAMEPAD_BINDTYPE_BUTTON:
            printf("{\"type\": \"button\", \"index\": %d}", b->input.button);
            break;
        case SDL_GAMEPAD_BINDTYPE_AXIS:
            // min/max carry the direction: a trigger runs one way, a stick both.
            printf("{\"type\": \"axis\", \"index\": %d, \"min\": %d, \"max\": %d}",
                   b->input.axis.axis, b->input.axis.axis_min, b->input.axis.axis_max);
            break;
        case SDL_GAMEPAD_BINDTYPE_HAT:
            printf("{\"type\": \"hat\", \"index\": %d, \"mask\": %d}",
                   b->input.hat.hat, b->input.hat.hat_mask);
            break;
        default:
            fputs("null", stdout);
            break;
        }
    }
    fputs(written ? "\n  }" : "}", stdout);

    SDL_free(binds);
}

// Whether this pad can drive an emulator's motion controls.
//
// Both sensors, not just the gyro: Ryujinx and Cemu each require the pair
// before they will offer motion at all — Ryujinx sets its Motion feature flag
// only when SDL reports accelerometer *and* gyroscope, and Cemu's
// SDLController::has_motion() is `m_has_gyro && m_has_accel`. Asking the same
// question they ask is what keeps this from promising motion that the emulator
// then declines to use.
static bool gamepad_has_motion(SDL_Gamepad *pad) {
    return pad && SDL_GamepadHasSensor(pad, SDL_SENSOR_GYRO) &&
           SDL_GamepadHasSensor(pad, SDL_SENSOR_ACCEL);
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

        // Opened once for both answers: a hidapi pad's open is a device
        // transaction, and doing it twice per pad raced the background thread
        // that brings those devices up.
        SDL_Gamepad *pad = SDL_IsGamepad(id) ? SDL_OpenGamepad(id) : NULL;

        printf(", \"motion\": %s", gamepad_has_motion(pad) ? "true" : "false");

        fputs(", \"map\": ", stdout);
        print_gamepad_map(pad);

        if (pad) SDL_CloseGamepad(pad);

        fputs("}", stdout);
    }
    fputs(count ? "\n]\n" : "]\n", stdout);

    SDL_free(ids);
    SDL_Quit();
    return 0;
}
