// gotg-killswitch — hold both shoulders and Start, and the game stops.
//
// Emulated games have no Quit menu that a controller can reach. A Switch title
// wants the Home button, an N64 ROM wants Alt-F4 on a keyboard that is not in
// the room, and a game that has hung wants neither — so the way out of a
// session on the couch has been "get up and find a keyboard". This is the way
// out: one combination, the same on every pad, that no game uses for anything.
//
//   both shoulders (or both triggers) + Start, held for three seconds
//
// It is also deliberately not a chord a game could want: emulators pass it
// straight through, so a game that happens to read it sees the same thing it
// always did.
//
// It watches rather than intercepts. SDL reads controllers through evdev, and
// several processes can read the same device at once, so this sees the pad
// without taking anything away from the emulator — which is what makes it
// work with every emulator rather than with the ones we could patch.
//
// It asks SDL because the emulators ask SDL: "the left shoulder" is a
// per-controller-model fact, and the alternative is a table of button indices
// per pad that would be wrong for the next one somebody plugs in.

#define _POSIX_C_SOURCE 200809L

#include <SDL3/SDL.h>
#include <errno.h>
#include <signal.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include <unistd.h>

#include "killswitch.h"
#include "procstat.h"

#define DEFAULT_HOLD_MS 3000
// How long the game gets to put itself away — write its save, flush its
// config — before it is killed outright. An emulator that is hung is the
// reason somebody reached for this, so the wait is bounded.
#define DEFAULT_GRACE_MS 5000
// Ten times a second. The hold is three seconds, so this is finer than the
// feature can notice — and on a handheld the cost of this loop is not the work
// (a poll is microseconds) but the wakeup itself, every one of which keeps a
// core out of a deeper idle state for the length of a session.
#define DEFAULT_POLL_MS 100

// Past halfway is "pulled". A trigger's resting position is not always a clean
// zero, and nobody holds a trigger at 40% for three seconds by accident.
#define TRIGGER_ON 16384

// More than anybody has plugged in, and small enough to keep flat.
#define MAX_PADS 16

typedef struct {
    SDL_JoystickID id;
    SDL_Gamepad *pad;
    ks_pad state;
    bool announced;  // whether this hold has already been mentioned in the log
} watched;

static watched pads[MAX_PADS];
static volatile sig_atomic_t stop = 0;

static void on_signal(int sig) {
    (void)sig;
    stop = 1;
}

// A controller's name comes off its USB descriptor and lands in a terminal or
// in the per-game log somebody later cats. Same rule as the rest of the client
// (printable() in lib/common.sh): nothing device-supplied writes live escape
// sequences into whoever is reading.
static void printable(const char *in, char *out, size_t size) {
    size_t n = 0;
    for (; in && *in && n + 1 < size; in++) {
        unsigned char c = (unsigned char)*in;
        out[n++] = (c >= 0x20 && c < 0x7f) ? (char)c : '?';
    }
    out[n] = '\0';
}

static void open_pad(SDL_JoystickID id, uint64_t hold_ms, bool quiet) {
    for (int i = 0; i < MAX_PADS; i++) {
        if (pads[i].pad && pads[i].id == id) return;
    }
    for (int i = 0; i < MAX_PADS; i++) {
        if (pads[i].pad) continue;
        SDL_Gamepad *pad = SDL_OpenGamepad(id);
        if (!pad) return;
        pads[i].id = id;
        pads[i].pad = pad;
        pads[i].announced = false;
        ks_init(&pads[i].state, hold_ms);
        if (!quiet) {
            char name[128];
            printable(SDL_GetGamepadName(pad), name, sizeof name);
            fprintf(stderr, "gotg-killswitch: watching %s\n", name[0] ? name : "a controller");
        }
        return;
    }
}

static void close_pad(SDL_JoystickID id) {
    for (int i = 0; i < MAX_PADS; i++) {
        if (!pads[i].pad || pads[i].id != id) continue;
        SDL_CloseGamepad(pads[i].pad);
        pads[i].pad = NULL;
        return;
    }
}

static void close_all(void) {
    for (int i = 0; i < MAX_PADS; i++) {
        if (pads[i].pad) SDL_CloseGamepad(pads[i].pad);
        pads[i].pad = NULL;
    }
}

// What one controller is doing. Button or axis, either counts — see ks_input.
static ks_input read_pad(SDL_Gamepad *pad) {
    ks_input in;
    in.left = SDL_GetGamepadButton(pad, SDL_GAMEPAD_BUTTON_LEFT_SHOULDER) ||
              SDL_GetGamepadAxis(pad, SDL_GAMEPAD_AXIS_LEFT_TRIGGER) >= TRIGGER_ON;
    in.right = SDL_GetGamepadButton(pad, SDL_GAMEPAD_BUTTON_RIGHT_SHOULDER) ||
               SDL_GetGamepadAxis(pad, SDL_GAMEPAD_AXIS_RIGHT_TRIGGER) >= TRIGGER_ON;
    in.start = SDL_GetGamepadButton(pad, SDL_GAMEPAD_BUTTON_START);
    return in;
}

// The process we were pointed at, pinned by when it started.
//
// A pid on its own is a slot, not a process: the game exits, the kernel hands
// the number to something else, and a watcher that has been sitting for three
// hours signals a stranger. Start time is the field that tells those apart.
static unsigned long long game_started = 0;

static bool same_game(pid_t pid) {
    if (!game_started) return true;  // never learned it; the pid is all we have
    unsigned long long now = proc_started(pid);
    return now == 0 || now == game_started;
}

// Everything the game started, not just the process we were handed.
//
// `gotg play` execs the environment's wrapper, which execs the emulator, so
// the pid given to us is usually the emulator itself *and* its process group's
// leader — and an emulator run under a compositor or a shim has children that
// a signal to one pid would leave running with the screen to themselves.
static void signal_game(pid_t pid, int sig) {
    if (!same_game(pid)) {
        fprintf(stderr, "gotg-killswitch: %d is not the game any more; signalling nobody\n", (int)pid);
        return;
    }
    pid_t group = getpgid(pid);
    // Never kill(-1): that is "every process this user may signal", which is
    // what a group kill becomes for pid 1 — a container entrypoint, or a
    // mistyped --pid.
    if (group == pid && group > 1) {
        kill(-group, sig);
    } else {
        kill(pid, sig);
    }
}

static void stop_game(pid_t pid, uint64_t grace_ms, uint64_t poll_ms) {
    fprintf(stderr, "gotg-killswitch: kill switch held; stopping %d\n", (int)pid);
    signal_game(pid, SIGTERM);

    // Politely first: an emulator that takes SIGTERM writes its save and
    // closes its files, which is the difference between quitting a game and
    // losing an hour of it.
    for (uint64_t waited = 0; waited < grace_ms && !stop; waited += poll_ms) {
        if (!proc_alive(pid)) {
            fprintf(stderr, "gotg-killswitch: stopped\n");
            return;
        }
        SDL_Delay((Uint32)poll_ms);
    }
    if (!proc_alive(pid)) {
        fprintf(stderr, "gotg-killswitch: stopped\n");
        return;
    }

    fprintf(stderr, "gotg-killswitch: it did not go; killing\n");
    signal_game(pid, SIGKILL);
}

static bool number(const char *text, uint64_t *out) {
    // Digits only, checked before strtoull sees it: strtoull accepts a leading
    // minus and hands back the wraparound, so "--hold-ms -1" would otherwise
    // parse as a hold of 584 million years — a kill switch that the launch
    // reports as armed and that can never fire.
    if (!text || *text < '0' || *text > '9') return false;

    char *end = NULL;
    errno = 0;
    unsigned long long value = strtoull(text, &end, 10);
    if (errno || !end || *end || end == text) return false;
    *out = (uint64_t)value;
    return true;
}

static void usage(FILE *where) {
    fputs("usage: gotg-killswitch --pid <pid> [--hold-ms N] [--grace-ms N] [--poll-ms N] [--quiet]\n"
          "\n"
          "Watches every controller SDL can see. When both shoulders (or both\n"
          "triggers) and Start are held together for the hold time, the process\n"
          "is asked to stop, and killed if it will not. Exits on its own when\n"
          "that process is gone.\n",
          where);
}

int main(int argc, char **argv) {
    uint64_t target = 0, hold_ms = DEFAULT_HOLD_MS, grace_ms = DEFAULT_GRACE_MS, poll_ms = DEFAULT_POLL_MS;
    bool quiet = false;

    for (int i = 1; i < argc; i++) {
        const char *arg = argv[i];
        uint64_t *slot = NULL;
        if (strcmp(arg, "--help") == 0 || strcmp(arg, "-h") == 0) {
            usage(stdout);
            return 0;
        }
        if (strcmp(arg, "--quiet") == 0) {
            quiet = true;
            continue;
        }
        if (strcmp(arg, "--pid") == 0) slot = &target;
        else if (strcmp(arg, "--hold-ms") == 0) slot = &hold_ms;
        else if (strcmp(arg, "--grace-ms") == 0) slot = &grace_ms;
        else if (strcmp(arg, "--poll-ms") == 0) slot = &poll_ms;
        if (!slot || i + 1 >= argc || !number(argv[++i], slot)) {
            fprintf(stderr, "gotg-killswitch: bad argument: %s\n", arg);
            usage(stderr);
            return 2;
        }
    }
    // Not pid 1, and not 0: a group kill of either is a signal to everything
    // this user owns rather than to one game.
    if (target < 2 || target > INT32_MAX) {
        fprintf(stderr, "gotg-killswitch: --pid is required, and is a process to watch\n");
        return 2;
    }
    if (poll_ms == 0) poll_ms = DEFAULT_POLL_MS;

    pid_t pid = (pid_t)target;
    game_started = proc_started(pid);
    if (!proc_alive(pid)) {
        // Nothing to guard. Not an error: the game it was started for has
        // already exited, which is the outcome this exists to produce.
        return 0;
    }

    // A process group of its own. Spawned from a non-interactive shell this
    // would otherwise share the game's, and the group kill below would land on
    // the watcher mid-escalation — so the one process that has to outlive the
    // signal steps out of its way first.
    setpgid(0, 0);

    // A broken pipe when the launcher's log rotates is not worth a signal.
    signal(SIGPIPE, SIG_IGN);
    signal(SIGTERM, on_signal);
    signal(SIGINT, on_signal);

    // No window, so tell SDL that input still counts. Without it a build that
    // defaults to foreground-only events sees a controller that never presses
    // anything.
    SDL_SetHint(SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS, "1");
    // See what the emulators see — the same hint gotg-pads sets, for the same
    // hidapi-only Steam Controller.
    SDL_SetHint(SDL_HINT_JOYSTICK_HIDAPI_STEAM, "1");

    if (!SDL_Init(SDL_INIT_GAMEPAD)) {
        // A machine with no input stack at all — a container, a headless
        // build. The game is already running and must stay running: no kill
        // switch is a worse session, not a failed one.
        fprintf(stderr, "gotg-killswitch: no controller support here (%s); carrying on without it\n", SDL_GetError());
        return 0;
    }

    // Armed, and the log says so. Without this a launch that promised a kill
    // switch and a launch whose watcher died on the first line look the same
    // afterwards — which is the wrong thing to be unsure of about the control
    // that exists for when a game has hung.
    if (!quiet) {
        fprintf(stderr, "gotg-killswitch: watching %d; both shoulders and Start, held %llums\n", (int)pid,
                (unsigned long long)hold_ms);
    }

    int count = 0;
    SDL_JoystickID *ids = SDL_GetGamepads(&count);
    if (ids) {
        for (int i = 0; i < count; i++) open_pad(ids[i], hold_ms, quiet);
        SDL_free(ids);
    }

    while (!stop) {
        SDL_Event event;
        while (SDL_PollEvent(&event)) {
            if (event.type == SDL_EVENT_GAMEPAD_ADDED) open_pad(event.gdevice.which, hold_ms, quiet);
            else if (event.type == SDL_EVENT_GAMEPAD_REMOVED) close_pad(event.gdevice.which);
        }

        uint64_t now = SDL_GetTicks();
        for (int i = 0; i < MAX_PADS; i++) {
            if (!pads[i].pad) continue;
            bool fire = ks_step(&pads[i].state, read_pad(pads[i].pad), now);

            // One line when a hold starts, so the log of a session that ended
            // this way says why — and so somebody testing the combo can see
            // the machine noticing before three seconds are up.
            if (!quiet && pads[i].state.holding && !pads[i].announced) {
                pads[i].announced = true;
                fprintf(stderr, "gotg-killswitch: kill switch held; %llums to go\n",
                        (unsigned long long)hold_ms);
            }
            if (!pads[i].state.holding) pads[i].announced = false;

            if (fire) {
                stop_game(pid, grace_ms, poll_ms);
                close_all();
                SDL_Quit();
                return 0;
            }
        }

        if (!proc_alive(pid)) break;
        SDL_Delay((Uint32)poll_ms);
    }

    close_all();
    SDL_Quit();
    return 0;
}
