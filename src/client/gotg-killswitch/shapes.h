// The shapes the bar is drawn with, as triangles with soft edges.
//
// SDL draws triangles with hard edges, and a ring built from them shows its
// staircase the moment it sits over a game at a television's distance. So
// every shape here has a feather: a band a pixel or so wide round its edge
// that fades from its colour to nothing, which is anti-aliasing a GPU does
// for free as it blends. No SDL in this file -- the vertices are plain, and a
// test can check a ring sweeps the right way and ends where it should.

#ifndef GOTG_SHAPES_H
#define GOTG_SHAPES_H

#include <stddef.h>

typedef struct {
    float r, g, b, a;
} gs_colour;

typedef struct {
    float x, y;
    gs_colour colour;
} gs_vertex;

// Room for a frame of the bar: twelve rings at most, each a few hundred
// vertices with its soft edges. One number, so what a test says fits is what
// the painter can draw.
#define GS_MESH_VERTICES 16384
#define GS_MESH_INDICES (GS_MESH_VERTICES * 3)

// Vertices and triangle indices, appended to by every shape. Fixed buffers
// owned by the caller: a frame of this bar is a few thousand vertices, and a
// frame that ran out draws less rather than failing.
typedef struct {
    gs_vertex *vertices;
    int *indices;
    size_t vertex_count, index_count;
    size_t vertex_capacity, index_capacity;
} gs_mesh;

void gs_mesh_clear(gs_mesh *mesh);

// An arc of a ring, clockwise from `start` (turns from twelve o'clock) through
// `span` turns. `width` is the ring's thickness, `feather` its soft edge.
void gs_mesh_arc(gs_mesh *mesh, float cx, float cy, float radius, float width, float start, float span,
                 gs_colour colour, float feather);

// A filled circle.
void gs_mesh_disc(gs_mesh *mesh, float cx, float cy, float radius, gs_colour colour, float feather);

// A thick line, soft along both long edges.
void gs_mesh_line(gs_mesh *mesh, float x1, float y1, float x2, float y2, float width, gs_colour colour,
                  float feather);

// A plain rectangle, no feather: the bar itself, whose edges are the screen's
// and one moving line.
void gs_mesh_rect(gs_mesh *mesh, float x, float y, float w, float h, gs_colour colour);

#endif
