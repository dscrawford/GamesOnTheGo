#include "shapes.h"

#include <math.h>
#include <stdbool.h>

// -std=c17 is ISO C, where M_PI is an extension rather than a promise.
#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

void gs_mesh_clear(gs_mesh *mesh) {
    mesh->vertex_count = 0;
    mesh->index_count = 0;
}

static gs_colour faded(gs_colour colour) {
    colour.a = 0.0f;
    return colour;
}

static bool room(const gs_mesh *mesh, size_t vertices, size_t indices) {
    return mesh->vertex_count + vertices <= mesh->vertex_capacity &&
           mesh->index_count + indices <= mesh->index_capacity;
}

static int put(gs_mesh *mesh, float x, float y, gs_colour colour) {
    mesh->vertices[mesh->vertex_count] = (gs_vertex){x, y, colour};
    return (int)mesh->vertex_count++;
}

static void quad(gs_mesh *mesh, int a, int b, int c, int d) {
    int *out = &mesh->indices[mesh->index_count];
    out[0] = a;
    out[1] = b;
    out[2] = c;
    out[3] = a;
    out[4] = c;
    out[5] = d;
    mesh->index_count += 6;
}

// Clockwise from twelve: in screen coordinates y grows downwards, so twelve
// o'clock is -y and clockwise is +x first.
static void on_circle(float cx, float cy, float radius, float turn, float *x, float *y) {
    float angle = turn * 2.0f * (float)M_PI;
    *x = cx + radius * sinf(angle);
    *y = cy - radius * cosf(angle);
}

// Steps along an arc: enough that its edge reads as a curve at these sizes,
// few enough to stay a handful of triangles. More for a longer arc.
static size_t steps_for(float span) {
    size_t steps = (size_t)ceilf(fabsf(span) * 96.0f);
    return steps < 2 ? 2 : steps;
}

void gs_mesh_arc(gs_mesh *mesh, float cx, float cy, float radius, float width, float start, float span,
                 gs_colour colour, float feather) {
    if (span <= 0.0f || width <= 0.0f) return;
    if (span > 1.0f) span = 1.0f;
    size_t steps = steps_for(span);
    // Four radii per step: fade in, inner edge, outer edge, fade out -- three
    // bands of two triangles each between one step and the next.
    if (!room(mesh, (steps + 1) * 4, steps * 18)) return;

    float inner = radius - width / 2.0f, outer = radius + width / 2.0f;
    float radii[4] = {inner - feather, inner, outer, outer + feather};
    gs_colour colours[4] = {faded(colour), colour, colour, faded(colour)};

    int previous[4] = {0, 0, 0, 0};
    for (size_t step = 0; step <= steps; step++) {
        float turn = start + span * (float)step / (float)steps;
        int current[4];
        for (int k = 0; k < 4; k++) {
            float x, y;
            on_circle(cx, cy, radii[k] > 0.0f ? radii[k] : 0.0f, turn, &x, &y);
            current[k] = put(mesh, x, y, colours[k]);
        }
        if (step > 0) {
            for (int k = 0; k < 3; k++) quad(mesh, previous[k], previous[k + 1], current[k + 1], current[k]);
        }
        for (int k = 0; k < 4; k++) previous[k] = current[k];
    }
}

void gs_mesh_disc(gs_mesh *mesh, float cx, float cy, float radius, gs_colour colour, float feather) {
    if (radius <= 0.0f) return;
    size_t steps = steps_for(1.0f);
    if (!room(mesh, 1 + (steps + 1) * 2, steps * 9)) return;

    int centre = put(mesh, cx, cy, colour);
    int previous_edge = 0, previous_fade = 0;
    for (size_t step = 0; step <= steps; step++) {
        float turn = (float)step / (float)steps;
        float x, y, fx, fy;
        on_circle(cx, cy, radius, turn, &x, &y);
        on_circle(cx, cy, radius + feather, turn, &fx, &fy);
        int edge = put(mesh, x, y, colour);
        int fade = put(mesh, fx, fy, faded(colour));
        if (step > 0) {
            int *out = &mesh->indices[mesh->index_count];
            out[0] = centre;
            out[1] = previous_edge;
            out[2] = edge;
            mesh->index_count += 3;
            quad(mesh, previous_edge, previous_fade, fade, edge);
        }
        previous_edge = edge;
        previous_fade = fade;
    }
}

void gs_mesh_line(gs_mesh *mesh, float x1, float y1, float x2, float y2, float width, gs_colour colour,
                  float feather) {
    if (!room(mesh, 8, 18)) return;
    float dx = x2 - x1, dy = y2 - y1;
    float length = sqrtf(dx * dx + dy * dy);
    if (length <= 0.0f) return;
    // Perpendicular unit, scaled to half the width and to the feather beyond.
    float nx = -dy / length, ny = dx / length;
    float half = width / 2.0f, soft = half + feather;

    int outer_a1 = put(mesh, x1 + nx * soft, y1 + ny * soft, faded(colour));
    int edge_a1 = put(mesh, x1 + nx * half, y1 + ny * half, colour);
    int edge_b1 = put(mesh, x1 - nx * half, y1 - ny * half, colour);
    int outer_b1 = put(mesh, x1 - nx * soft, y1 - ny * soft, faded(colour));
    int outer_a2 = put(mesh, x2 + nx * soft, y2 + ny * soft, faded(colour));
    int edge_a2 = put(mesh, x2 + nx * half, y2 + ny * half, colour);
    int edge_b2 = put(mesh, x2 - nx * half, y2 - ny * half, colour);
    int outer_b2 = put(mesh, x2 - nx * soft, y2 - ny * soft, faded(colour));

    quad(mesh, outer_a1, edge_a1, edge_a2, outer_a2);
    quad(mesh, edge_a1, edge_b1, edge_b2, edge_a2);
    quad(mesh, edge_b1, outer_b1, outer_b2, edge_b2);
}

void gs_mesh_rect(gs_mesh *mesh, float x, float y, float w, float h, gs_colour colour) {
    if (w <= 0.0f || h <= 0.0f || !room(mesh, 4, 6)) return;
    int a = put(mesh, x, y, colour);
    int b = put(mesh, x + w, y, colour);
    int c = put(mesh, x + w, y + h, colour);
    int d = put(mesh, x, y + h, colour);
    quad(mesh, a, b, c, d);
}
