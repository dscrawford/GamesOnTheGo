#define _POSIX_C_SOURCE 200809L

#include "overlay.h"

#include <SDL3/SDL.h>
#include <X11/Xatom.h>
#include <X11/Xlib.h>
#include <stdlib.h>

// Big enough to read at arm's length on a handheld, small enough to leave the
// game visible around it. Square, because the thing in it is a circle.
#define SIZE 260

#define RING_OUTER (SIZE * 0.42f)
#define RING_INNER (SIZE * 0.34f)
#define CROSS_REACH (SIZE * 0.17f)
#define CROSS_WIDTH (SIZE * 0.055f)

// One red, used everywhere: the ring, the X, the track behind them. Chosen for
// a game's worth of background rather than a desktop's — a dim red over a dark
// scene reads as part of the game, and this must not.
#define MAX_POINTS 256

struct overlay {
    SDL_Window *window;
    SDL_Renderer *renderer;
};

// A window the game keeps: on top, no border, no focus taken, and — where the
// display server allows it — no background at all.
//
// Under gamescope, which is how a game runs on a handheld, "on top" is not a
// window flag but a property: GAMESCOPE_EXTERNAL_OVERLAY is what mangoapp uses
// to draw over a game, and without it this would be composited as a second
// window nobody sees. Set through SDL's own X11 handles so there is no second
// connection to keep in step.
static void ask_to_float(SDL_Window *window) {
    SDL_PropertiesID props = SDL_GetWindowProperties(window);
    Display *display = SDL_GetPointerProperty(props, SDL_PROP_WINDOW_X11_DISPLAY_POINTER, NULL);
    Window handle = (Window)SDL_GetNumberProperty(props, SDL_PROP_WINDOW_X11_WINDOW_NUMBER, 0);
    if (!display || !handle) return;  // wayland, or no X11 at all

    Atom overlay = XInternAtom(display, "GAMESCOPE_EXTERNAL_OVERLAY", False);
    if (overlay == None) return;
    long on = 1;
    XChangeProperty(display, handle, overlay, XA_CARDINAL, 32, PropModeReplace, (unsigned char *)&on, 1);
    XFlush(display);
}

overlay *overlay_open(void) {
    // Inside gamescope, take Xwayland rather than its wayland socket: the
    // overlay property below is an X11 one, and a native wayland surface there
    // is a second window the compositor will not put over the game.
    if (SDL_getenv("GAMESCOPE_WAYLAND_DISPLAY") && SDL_getenv("DISPLAY")) {
        SDL_SetHint(SDL_HINT_VIDEO_DRIVER, "x11");
    }
    if (!SDL_InitSubSystem(SDL_INIT_VIDEO)) return NULL;

    SDL_WindowFlags flags = SDL_WINDOW_BORDERLESS | SDL_WINDOW_ALWAYS_ON_TOP | SDL_WINDOW_TRANSPARENT |
                            SDL_WINDOW_NOT_FOCUSABLE | SDL_WINDOW_UTILITY;
    SDL_Window *window = SDL_CreateWindow("gotg kill switch", SIZE, SIZE, flags);
    if (!window) {
        SDL_QuitSubSystem(SDL_INIT_VIDEO);
        return NULL;
    }
    SDL_SetWindowPosition(window, SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED);
    ask_to_float(window);

    SDL_Renderer *renderer = SDL_CreateRenderer(window, NULL);
    if (!renderer) {
        SDL_DestroyWindow(window);
        SDL_QuitSubSystem(SDL_INIT_VIDEO);
        return NULL;
    }
    SDL_SetRenderDrawBlendMode(renderer, SDL_BLENDMODE_BLEND);

    overlay *made = calloc(1, sizeof *made);
    if (!made) {
        SDL_DestroyRenderer(renderer);
        SDL_DestroyWindow(window);
        SDL_QuitSubSystem(SDL_INIT_VIDEO);
        return NULL;
    }
    made->window = window;
    made->renderer = renderer;
    return made;
}

static void fill(SDL_Renderer *renderer, const ks_point *points, size_t count, SDL_FColor colour, bool strip) {
    if (count < 3) return;
    SDL_Vertex vertices[MAX_POINTS];
    if (count > MAX_POINTS) count = MAX_POINTS;
    for (size_t i = 0; i < count; i++) {
        vertices[i].position = (SDL_FPoint){points[i].x, points[i].y};
        vertices[i].color = colour;
        vertices[i].tex_coord = (SDL_FPoint){0, 0};
    }

    // SDL draws triangle lists, so both shapes are turned into one here: a
    // strip walks the ring two points at a time, a quad is its two triangles.
    int indices[(MAX_POINTS - 2) * 3];
    int written = 0;
    if (strip) {
        for (size_t i = 0; i + 2 < count; i++) {
            indices[written++] = (int)i;
            indices[written++] = (int)i + 1;
            indices[written++] = (int)i + 2;
        }
    } else {
        for (size_t i = 2; i < count; i++) {
            indices[written++] = 0;
            indices[written++] = (int)i - 1;
            indices[written++] = (int)i;
        }
    }
    SDL_RenderGeometry(renderer, NULL, vertices, (int)count, indices, written);
}

void overlay_draw(overlay *window, float progress) {
    if (!window) return;
    if (progress < 0.0f) progress = 0.0f;
    if (progress > 1.0f) progress = 1.0f;

    const float centre = SIZE / 2.0f;
    // One red throughout — bright enough to read over a game rather than to sit
    // politely on a desktop.
    SDL_FColor track = {230 / 255.0f, 40 / 255.0f, 40 / 255.0f, 0.22f};
    SDL_FColor bright = {230 / 255.0f, 40 / 255.0f, 40 / 255.0f, 0.95f};
    // The X starts faint and is solid by the time the ring closes, so the
    // last second reads as "now" rather than as more of the same.
    SDL_FColor cross = {230 / 255.0f, 40 / 255.0f, 40 / 255.0f, 0.35f + 0.6f * progress};

    SDL_SetRenderDrawColor(window->renderer, 0, 0, 0, 0);
    SDL_RenderClear(window->renderer);

    ks_point points[MAX_POINTS];
    size_t count = ks_ring(centre, centre, RING_INNER, RING_OUTER, 1.0f, points, MAX_POINTS);
    fill(window->renderer, points, count, track, true);

    count = ks_ring(centre, centre, RING_INNER, RING_OUTER, progress, points, MAX_POINTS);
    fill(window->renderer, points, count, bright, true);

    ks_stroke(centre - CROSS_REACH, centre - CROSS_REACH, centre + CROSS_REACH, centre + CROSS_REACH, CROSS_WIDTH,
              points);
    fill(window->renderer, points, 4, cross, false);
    ks_stroke(centre + CROSS_REACH, centre - CROSS_REACH, centre - CROSS_REACH, centre + CROSS_REACH, CROSS_WIDTH,
              points);
    fill(window->renderer, points, 4, cross, false);

    SDL_RenderPresent(window->renderer);
}

void overlay_close(overlay *window) {
    if (!window) return;
    SDL_DestroyRenderer(window->renderer);
    SDL_DestroyWindow(window->window);
    SDL_QuitSubSystem(SDL_INIT_VIDEO);
    free(window);
}
