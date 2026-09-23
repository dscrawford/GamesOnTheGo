#include "scene.h"

#include <math.h>

#include "theme.h"

// A pixel and a bit: wider reads as blur, narrower as the staircase back.
#define FEATHER 1.25f

// The bar: the picker's background (theme.h, from config/theme.yaml), nearly
// opaque.
#define BAR_R (GS_THEME_BACKGROUND_R / 255.0f)
#define BAR_G (GS_THEME_BACKGROUND_G / 255.0f)
#define BAR_B (GS_THEME_BACKGROUND_B / 255.0f)
#define BAR_A 0.88f

static gs_colour rgb(int r, int g, int b, float a) {
    return (gs_colour){r / 255.0f, g / 255.0f, b / 255.0f, a};
}

static gs_colour with_alpha(gs_colour colour, float a) {
    colour.a = a;
    return colour;
}

float gs_bar_height(int screen_height) {
    float h = (float)screen_height * 0.085f;
    if (h < 48.0f) h = 48.0f;
    if (h > 128.0f) h = 128.0f;
    return roundf(h);
}

gs_colour gs_player_colour(int player) {
    // config/theme.yaml's `players`, generated into theme.h at build time so
    // there is one palette to edit. A seat past the last wraps, as the
    // picker's does.
    static const int palette[][3] = GS_THEME_PLAYERS;
    const int count = (int)(sizeof palette / sizeof palette[0]);
    if (player < 1) return rgb(GS_THEME_TEXT_DIM_R, GS_THEME_TEXT_DIM_G, GS_THEME_TEXT_DIM_B, 1.0f);
    const int *c = palette[(player - 1) % count];
    return rgb(c[0], c[1], c[2], 1.0f);
}

float gs_item_x(int width, float bar_height, size_t index, size_t count) {
    float step = bar_height * 1.15f;
    float span = step * (float)(count > 0 ? count - 1 : 0);
    return (float)width / 2.0f - span / 2.0f + step * (float)index;
}

static void exit_ring(gs_mesh *mesh, float cx, float cy, float radius, float width, double progress) {
    gs_colour red = rgb(230, 40, 40, 1.0f);
    float p = (float)(progress > 1.0 ? 1.0 : progress);
    gs_mesh_arc(mesh, cx, cy, radius, width, 0.0f, 1.0f, with_alpha(red, 0.25f), FEATHER);
    gs_mesh_arc(mesh, cx, cy, radius, width, 0.0f, p, red, FEATHER);
    // The X firms up as the ring closes, so the last second reads as "now"
    // rather than as more of the same. Mixed towards the bar rather than made
    // see-through: two translucent strokes double up where they cross, and
    // the middle of the X was a brighter square than its arms.
    float reach = radius * 0.42f;
    float firm = 0.35f + 0.6f * p;
    gs_colour cross = {BAR_R + (red.r - BAR_R) * firm, BAR_G + (red.g - BAR_G) * firm,
                       BAR_B + (red.b - BAR_B) * firm, 1.0f};
    gs_mesh_line(mesh, cx - reach, cy - reach, cx + reach, cy + reach, width * 0.9f, cross, FEATHER);
    gs_mesh_line(mesh, cx + reach, cy - reach, cx - reach, cy + reach, width * 0.9f, cross, FEATHER);
}

static void joining_ring(gs_mesh *mesh, float cx, float cy, float radius, float width, float fraction, int player,
                         double clock) {
    gs_colour colour = gs_player_colour(player);
    gs_mesh_arc(mesh, cx, cy, radius, width, 0.0f, 1.0f, with_alpha(colour, 0.22f), FEATHER);
    gs_mesh_arc(mesh, cx, cy, radius, width, 0.0f, fraction, colour, FEATHER);
    // The spinner: a short arc turning inside, once every 1.2 s. Its own
    // clock, not the fill's, so it moves the instant a hold begins.
    float turn = (float)fmod(clock / 1.2, 1.0);
    gs_mesh_arc(mesh, cx, cy, radius * 0.55f, width * 0.7f, turn, 0.22f, rgb(GS_THEME_TEXT_R, GS_THEME_TEXT_G, GS_THEME_TEXT_B, 0.85f), FEATHER);
}

static void joined_badge(gs_mesh *mesh, float cx, float cy, float radius, float width, int player) {
    gs_mesh_disc(mesh, cx, cy, radius + width / 2.0f, gs_player_colour(player), FEATHER);
    // A tick through the middle: short stroke down, long stroke up.
    float unit = radius * 0.5f;
    gs_colour ink = {BAR_R, BAR_G, BAR_B, 0.95f};
    float x0 = cx - unit, y0 = cy;
    float x1 = cx - unit * 0.25f, y1 = cy + unit * 0.7f;
    float x2 = cx + unit, y2 = cy - unit * 0.75f;
    gs_mesh_line(mesh, x0, y0, x1, y1, width, ink, FEATHER);
    gs_mesh_line(mesh, x1, y1, x2, y2, width, ink, FEATHER);
    // Where the strokes meet, so the corner is a corner and not a notch.
    gs_mesh_disc(mesh, x1, y1, width / 2.0f, ink, FEATHER);
}

void gs_scene_build(const gs_scene *scene, gs_mesh *mesh) {
    gs_mesh_clear(mesh);
    if (scene->position <= 0.0) return;

    float bar = scene->bar_height;
    float top = -bar * (1.0f - (float)scene->position);
    gs_mesh_rect(mesh, 0.0f, top, (float)scene->width, bar, (gs_colour){BAR_R, BAR_G, BAR_B, BAR_A});
    // A hairline under it, so the bar has an edge over a dark scene.
    gs_mesh_rect(mesh, 0.0f, top + bar - 1.0f, (float)scene->width, 1.0f, rgb(GS_THEME_TEXT_R, GS_THEME_TEXT_G, GS_THEME_TEXT_B, 0.18f));

    float cy = top + bar / 2.0f;
    float radius = bar * 0.30f;
    float width = bar * 0.085f;

    if (scene->exit_progress > 0.0) {
        exit_ring(mesh, (float)scene->width / 2.0f, cy, radius, width, scene->exit_progress);
        return;
    }

    size_t count = scene->hold_count + scene->joined_count;
    size_t at = 0;
    for (size_t i = 0; i < scene->joined_count; i++, at++) {
        joined_badge(mesh, gs_item_x(scene->width, bar, at, count), cy, radius, width, scene->joined[i]);
    }
    for (size_t i = 0; i < scene->hold_count; i++, at++) {
        joining_ring(mesh, gs_item_x(scene->width, bar, at, count), cy, radius, width, scene->fractions[i],
                     scene->players[i], scene->clock);
    }
}
