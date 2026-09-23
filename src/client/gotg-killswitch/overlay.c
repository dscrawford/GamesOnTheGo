#define _POSIX_C_SOURCE 200809L

#include "overlay.h"

#include <SDL3/SDL.h>
#include <X11/Xatom.h>
#include <X11/Xlib.h>
#include <X11/extensions/shape.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wayland-client.h>

#include "scene.h"
#include "wlr-layer-shell-unstable-v1-client-protocol.h"

// A mesh's colours are handed to SDL as they are, with no copy per frame.
_Static_assert(sizeof(gs_colour) == sizeof(SDL_FColor), "gs_colour must be laid out as SDL_FColor");

typedef enum { KIND_GAMESCOPE, KIND_LAYER_SHELL, KIND_X11 } kind;

struct overlay {
    kind how;
    SDL_Window *window;
    SDL_Renderer *renderer;
    int screen_height;  // logical, for the bar's size
    float bar_height;   // in pixels, worked out once: see overlay_size
    // X11 (and gamescope) only: SDL's own handles, read once.
    Display *x11;
    Window x11_window;
    Uint64 raised_at;
    // layer-shell only
    struct wl_registry *registry;
    struct wl_compositor *compositor;
    struct zwlr_layer_shell_v1 *shell;
    struct zwlr_layer_surface_v1 *layer;
    int layer_width;
    bool configured;
    bool closed;
    bool vsync;  // presenting waits for the panel
};

// --- finding out which way in there is ------------------------------------------

static void probe_global(void *data, struct wl_registry *registry, uint32_t name, const char *interface,
                         uint32_t version) {
    (void)registry;
    (void)name;
    (void)version;
    if (strcmp(interface, zwlr_layer_shell_v1_interface.name) == 0) *(bool *)data = true;
}

static void probe_global_remove(void *data, struct wl_registry *registry, uint32_t name) {
    (void)data;
    (void)registry;
    (void)name;
}

static const struct wl_registry_listener probe_listener = {probe_global, probe_global_remove};

// Whether the Wayland compositor here offers layer-shell. Asked on a
// connection of our own, before SDL picks a video driver: the answer decides
// which driver to ask for.
static bool has_layer_shell(void) {
    const char *name = getenv("WAYLAND_DISPLAY");
    if (!name || !*name) return false;
    struct wl_display *display = wl_display_connect(NULL);
    if (!display) return false;
    bool found = false;
    struct wl_registry *registry = wl_display_get_registry(display);
    wl_registry_add_listener(registry, &probe_listener, &found);
    wl_display_roundtrip(display);
    wl_registry_destroy(registry);
    wl_display_disconnect(display);
    return found;
}

static kind choose(void) {
    // Inside gamescope, Xwayland rather than its wayland socket: the overlay
    // property is an X11 one, and a native wayland surface there is a second
    // window gamescope will not put over the game.
    if (getenv("GAMESCOPE_WAYLAND_DISPLAY") && getenv("DISPLAY")) return KIND_GAMESCOPE;
    if (has_layer_shell()) return KIND_LAYER_SHELL;
    return KIND_X11;
}

// --- layer-shell ------------------------------------------------------------------

static void layer_configure(void *data, struct zwlr_layer_surface_v1 *layer, uint32_t serial, uint32_t width,
                            uint32_t height) {
    (void)height;
    struct overlay *made = data;
    zwlr_layer_surface_v1_ack_configure(layer, serial);
    if (width > 0 && (int)width != made->layer_width) {
        // A custom surface is told nothing about its size; SDL has to be.
        made->layer_width = (int)width;
        SDL_SetWindowSize(made->window, made->layer_width, (int)gs_bar_height(made->screen_height));
    }
    made->configured = true;
}

static void layer_closed(void *data, struct zwlr_layer_surface_v1 *layer) {
    (void)layer;
    ((struct overlay *)data)->closed = true;
}

static const struct zwlr_layer_surface_v1_listener layer_listener = {layer_configure, layer_closed};

static void layer_global(void *data, struct wl_registry *registry, uint32_t name, const char *interface,
                         uint32_t version) {
    struct overlay *made = data;
    if (strcmp(interface, zwlr_layer_shell_v1_interface.name) == 0) {
        made->shell = wl_registry_bind(registry, name, &zwlr_layer_shell_v1_interface, version < 4 ? version : 4);
    } else if (strcmp(interface, wl_compositor_interface.name) == 0) {
        made->compositor = wl_registry_bind(registry, name, &wl_compositor_interface, 1);
    }
}

static const struct wl_registry_listener layer_registry_listener = {layer_global, probe_global_remove};

static bool become_layer(struct overlay *made) {
    SDL_PropertiesID props = SDL_GetWindowProperties(made->window);
    struct wl_display *display = SDL_GetPointerProperty(props, SDL_PROP_WINDOW_WAYLAND_DISPLAY_POINTER, NULL);
    struct wl_surface *surface = SDL_GetPointerProperty(props, SDL_PROP_WINDOW_WAYLAND_SURFACE_POINTER, NULL);
    if (!display || !surface) return false;

    made->registry = wl_display_get_registry(display);
    wl_registry_add_listener(made->registry, &layer_registry_listener, made);
    wl_display_roundtrip(display);
    if (!made->shell || !made->compositor) return false;

    int bar = (int)gs_bar_height(made->screen_height);
    made->layer = zwlr_layer_shell_v1_get_layer_surface(made->shell, surface, NULL, ZWLR_LAYER_SHELL_V1_LAYER_OVERLAY,
                                                        "gotg-overlay");
    zwlr_layer_surface_v1_add_listener(made->layer, &layer_listener, made);
    zwlr_layer_surface_v1_set_anchor(made->layer, ZWLR_LAYER_SURFACE_V1_ANCHOR_TOP |
                                                      ZWLR_LAYER_SURFACE_V1_ANCHOR_LEFT |
                                                      ZWLR_LAYER_SURFACE_V1_ANCHOR_RIGHT);
    zwlr_layer_surface_v1_set_size(made->layer, 0, (uint32_t)bar);
    // -1: neither reserve room nor be pushed aside by a panel's reservation.
    // The bar goes over the game, not beside it.
    zwlr_layer_surface_v1_set_exclusive_zone(made->layer, -1);
    zwlr_layer_surface_v1_set_keyboard_interactivity(made->layer,
                                                     ZWLR_LAYER_SURFACE_V1_KEYBOARD_INTERACTIVITY_NONE);
    // An empty input region: the pointer goes through to the game.
    struct wl_region *nothing = wl_compositor_create_region(made->compositor);
    wl_surface_set_input_region(surface, nothing);
    wl_region_destroy(nothing);
    wl_surface_commit(surface);
    wl_display_roundtrip(display);
    return made->configured && !made->closed;
}

// --- X11 ----------------------------------------------------------------------

// SDL's own X11 handles, so there is no second connection to keep in step.
static bool take_x11_handles(struct overlay *made) {
    SDL_PropertiesID props = SDL_GetWindowProperties(made->window);
    made->x11 = SDL_GetPointerProperty(props, SDL_PROP_WINDOW_X11_DISPLAY_POINTER, NULL);
    made->x11_window = (Window)SDL_GetNumberProperty(props, SDL_PROP_WINDOW_X11_WINDOW_NUMBER, 0);
    return made->x11 && made->x11_window;
}

// gamescope's overlay slot.
static void claim_gamescope_slot(struct overlay *made) {
    Atom overlay = XInternAtom(made->x11, "GAMESCOPE_EXTERNAL_OVERLAY", False);
    if (overlay == None) return;
    long on = 1;
    XChangeProperty(made->x11, made->x11_window, overlay, XA_CARDINAL, 32, PropModeReplace, (unsigned char *)&on, 1);
    XFlush(made->x11);
}

// Clicks go through: an input shape with nothing in it.
static void let_input_through(struct overlay *made) {
    XShapeCombineRectangles(made->x11, made->x11_window, ShapeInput, 0, 0, NULL, 0, ShapeSet, Unsorted);
    XFlush(made->x11);
}

// Override-redirect windows stack by who was raised last, so a game that goes
// fullscreen or raises itself after the bar came down would cover it. Raised
// twice a second rather than every frame: a restack is the X server's and the
// compositor's work, and the bar is only ever covered by something new.
static void keep_on_top(struct overlay *made) {
    Uint64 now = SDL_GetTicks();
    if (made->raised_at && now - made->raised_at < 500) return;
    made->raised_at = now;
    XRaiseWindow(made->x11, made->x11_window);
}

// --- the window ---------------------------------------------------------------

static void release(struct overlay *made) {
    if (made->layer) zwlr_layer_surface_v1_destroy(made->layer);
    if (made->shell) zwlr_layer_shell_v1_destroy(made->shell);
    if (made->compositor) wl_compositor_destroy(made->compositor);
    if (made->registry) wl_registry_destroy(made->registry);
    if (made->renderer) SDL_DestroyRenderer(made->renderer);
    if (made->window) SDL_DestroyWindow(made->window);
    SDL_QuitSubSystem(SDL_INIT_VIDEO);
    free(made);
}

// Every way this gives up says why, once, on stderr: a painter that exits in
// silence looks exactly like a bar nobody asked for. On a Deck it was "Could
// not get EGL display" from every GPU renderer, and nothing on screen.
static void fail(struct overlay *made) {
    fprintf(stderr, "gotg-killswitch: no overlay (%s): %s\n", overlay_kind(made), SDL_GetError());
    release(made);
}

overlay *overlay_open(void) {
    struct overlay *made = calloc(1, sizeof *made);
    if (!made) return NULL;
    made->how = choose();
    SDL_SetHint(SDL_HINT_VIDEO_DRIVER, made->how == KIND_LAYER_SHELL ? "wayland" : "x11");
    if (made->how == KIND_X11) SDL_SetHint(SDL_HINT_X11_FORCE_OVERRIDE_REDIRECT, "1");
    if (!SDL_InitSubSystem(SDL_INIT_VIDEO)) {
        free(made);
        return NULL;
    }

    SDL_Rect screen = {0, 0, 1280, 800};
    SDL_GetDisplayBounds(SDL_GetPrimaryDisplay(), &screen);
    made->screen_height = screen.h;
    int bar = (int)gs_bar_height(screen.h);

    SDL_PropertiesID props = SDL_CreateProperties();
    SDL_SetStringProperty(props, SDL_PROP_WINDOW_CREATE_TITLE_STRING, "gotg overlay");
    SDL_SetBooleanProperty(props, SDL_PROP_WINDOW_CREATE_TRANSPARENT_BOOLEAN, true);
    SDL_SetBooleanProperty(props, SDL_PROP_WINDOW_CREATE_BORDERLESS_BOOLEAN, true);
    SDL_SetBooleanProperty(props, SDL_PROP_WINDOW_CREATE_FOCUSABLE_BOOLEAN, false);
    SDL_SetBooleanProperty(props, SDL_PROP_WINDOW_CREATE_ALWAYS_ON_TOP_BOOLEAN, true);
    SDL_SetBooleanProperty(props, SDL_PROP_WINDOW_CREATE_UTILITY_BOOLEAN, true);
    if (made->how == KIND_LAYER_SHELL) {
        SDL_SetBooleanProperty(props, SDL_PROP_WINDOW_CREATE_WAYLAND_SURFACE_ROLE_CUSTOM_BOOLEAN, true);
        SDL_SetBooleanProperty(props, SDL_PROP_WINDOW_CREATE_HIGH_PIXEL_DENSITY_BOOLEAN, true);
        SDL_SetNumberProperty(props, SDL_PROP_WINDOW_CREATE_WIDTH_NUMBER, screen.w);
        SDL_SetNumberProperty(props, SDL_PROP_WINDOW_CREATE_HEIGHT_NUMBER, bar);
    } else {
        // gamescope paints its overlay at the screen's size and origin, as
        // mangoapp's is; elsewhere the window is just the bar's strip.
        SDL_SetNumberProperty(props, SDL_PROP_WINDOW_CREATE_X_NUMBER, screen.x);
        SDL_SetNumberProperty(props, SDL_PROP_WINDOW_CREATE_Y_NUMBER, screen.y);
        SDL_SetNumberProperty(props, SDL_PROP_WINDOW_CREATE_WIDTH_NUMBER, screen.w);
        SDL_SetNumberProperty(props, SDL_PROP_WINDOW_CREATE_HEIGHT_NUMBER,
                              made->how == KIND_GAMESCOPE ? screen.h : bar);
    }
    made->window = SDL_CreateWindowWithProperties(props);
    SDL_DestroyProperties(props);
    if (!made->window) {
        fail(made);
        return NULL;
    }
    made->renderer = SDL_CreateRenderer(made->window, NULL);
    // No GPU userspace this program can load (a nix build on SteamOS run
    // without the package's foreign-GL script): software draws a strip of
    // flat shapes in well under a frame. X11 only, in practice -- SDL's
    // Wayland backend has no window framebuffer without EGL either.
    if (!made->renderer) made->renderer = SDL_CreateRenderer(made->window, SDL_SOFTWARE_RENDERER);
    if (!made->renderer) {
        fail(made);
        return NULL;
    }
    SDL_SetRenderDrawBlendMode(made->renderer, SDL_BLENDMODE_BLEND);
    // On the panel's beat, where the driver will: the slide is the one thing
    // here that moves, and a slide timed by a sleep steps.
    made->vsync = SDL_SetRenderVSync(made->renderer, 1);

    switch (made->how) {
        case KIND_GAMESCOPE:
            // gamescope's window is the whole screen: the bar is sized for it.
            made->bar_height = gs_bar_height(screen.h);
            if (take_x11_handles(made)) claim_gamescope_slot(made);
            break;
        case KIND_LAYER_SHELL:
            if (!become_layer(made)) {
                fail(made);
                return NULL;
            }
            break;
        case KIND_X11:
            made->bar_height = (float)bar;
            if (take_x11_handles(made)) {
                let_input_through(made);
                keep_on_top(made);
            }
            break;
    }
    return made;
}

bool overlay_vsync(const overlay *window) {
    return window->vsync;
}

const char *overlay_renderer(const overlay *window) {
    const char *name = window->renderer ? SDL_GetRendererName(window->renderer) : NULL;
    return name ? name : "none";
}

const char *overlay_kind(const overlay *window) {
    switch (window->how) {
        case KIND_GAMESCOPE:
            return "gamescope";
        case KIND_LAYER_SHELL:
            return "layer-shell";
        case KIND_X11:
            break;
    }
    return "x11";
}

void overlay_size(const overlay *window, int *width, float *bar_height) {
    int h = 0;
    SDL_GetRenderOutputSize(window->renderer, width, &h);
    // On layer-shell the surface is the bar, so its height in pixels is the
    // bar's however the output is scaled -- read live, since the compositor
    // decides it. Elsewhere the window is bigger than the bar, and the
    // height worked out at open stands.
    *bar_height = window->how == KIND_LAYER_SHELL ? (float)h : window->bar_height;
}

void overlay_draw(overlay *window, const gs_mesh *mesh) {
    if (window->closed) return;
    SDL_SetRenderDrawColor(window->renderer, 0, 0, 0, 0);
    SDL_RenderClear(window->renderer);
    if (mesh->vertex_count > 0 && mesh->index_count > 0) {
        // gs_vertex is laid out as SDL_Vertex's position and colour; its
        // texture coordinate is absent because nothing here is textured.
        SDL_RenderGeometryRaw(window->renderer, NULL, &mesh->vertices[0].x, (int)sizeof(gs_vertex),
                              (const SDL_FColor *)&mesh->vertices[0].colour, (int)sizeof(gs_vertex), NULL, 0,
                              (int)mesh->vertex_count, mesh->indices, (int)mesh->index_count, (int)sizeof(int));
    }
    SDL_RenderPresent(window->renderer);
    if (window->how == KIND_X11 && window->x11) keep_on_top(window);
}

void overlay_close(overlay *window) {
    release(window);
}
