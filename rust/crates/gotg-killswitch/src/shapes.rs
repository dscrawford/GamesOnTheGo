//! The shapes the bar is drawn with, as triangles with soft edges.
//!
//! SDL draws triangles with hard edges, and a ring built from them shows its
//! staircase the moment it sits over a game at a television's distance. So
//! every shape here has a feather: a band a pixel or so wide round its edge
//! that fades from its colour to nothing, which is anti-aliasing a GPU does
//! for free as it blends. No SDL here -- the vertices are plain, and a test
//! can check a ring sweeps the right way and ends where it should.

use std::f32::consts::TAU;

/// Laid out as SDL_FColor, so a mesh is handed to SDL with no copy.
#[repr(C)]
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct Colour {
    pub r: f32,
    pub g: f32,
    pub b: f32,
    pub a: f32,
}

impl Colour {
    pub const fn rgb(rgb: [u8; 3], a: f32) -> Self {
        Self {
            r: rgb[0] as f32 / 255.0,
            g: rgb[1] as f32 / 255.0,
            b: rgb[2] as f32 / 255.0,
            a,
        }
    }

    pub const fn with_alpha(self, a: f32) -> Self {
        Self { a, ..self }
    }

    fn faded(self) -> Self {
        self.with_alpha(0.0)
    }
}

/// A position and a colour: SDL_Vertex without its texture coordinate,
/// because nothing here is textured.
#[repr(C)]
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct Vertex {
    pub x: f32,
    pub y: f32,
    pub colour: Colour,
}

/// Room for a frame of the bar: twelve rings at most, each a few hundred
/// vertices with its soft edges. One number, so what a test says fits is
/// what the painter can draw.
pub const MESH_VERTICES: usize = 16384;
pub const MESH_INDICES: usize = MESH_VERTICES * 3;

/// Vertices and triangle indices, appended to by every shape. Bounded: a
/// frame that runs out draws less rather than growing, and a shape that does
/// not fit whole is left out whole rather than drawn in part.
#[derive(Debug, Clone)]
pub struct Mesh {
    pub vertices: Vec<Vertex>,
    pub indices: Vec<i32>,
    vertex_capacity: usize,
    index_capacity: usize,
}

impl Default for Mesh {
    fn default() -> Self {
        Self::with_room(MESH_VERTICES, MESH_INDICES)
    }
}

/// A point on a circle, `turn` turns clockwise from twelve: y grows downwards
/// on a screen, so twelve o'clock is -y and clockwise is +x first.
fn on_circle(cx: f32, cy: f32, radius: f32, turn: f32) -> (f32, f32) {
    let angle = turn * TAU;
    (cx + radius * angle.sin(), cy - radius * angle.cos())
}

/// Steps along an arc: enough that its edge reads as a curve at these sizes,
/// few enough to stay a handful of triangles. More for a longer arc.
fn steps_for(span: f32) -> usize {
    ((span.abs() * 96.0).ceil() as usize).max(2)
}

impl Mesh {
    pub fn with_room(vertex_capacity: usize, index_capacity: usize) -> Self {
        Self {
            vertices: Vec::with_capacity(vertex_capacity),
            indices: Vec::with_capacity(index_capacity),
            vertex_capacity,
            index_capacity,
        }
    }

    pub fn clear(&mut self) {
        self.vertices.clear();
        self.indices.clear();
    }

    fn room(&self, vertices: usize, indices: usize) -> bool {
        self.vertices.len() + vertices <= self.vertex_capacity
            && self.indices.len() + indices <= self.index_capacity
    }

    fn put(&mut self, x: f32, y: f32, colour: Colour) -> i32 {
        self.vertices.push(Vertex { x, y, colour });
        (self.vertices.len() - 1) as i32
    }

    fn quad(&mut self, a: i32, b: i32, c: i32, d: i32) {
        self.indices.extend_from_slice(&[a, b, c, a, c, d]);
    }

    /// An arc of a ring, clockwise from `start` (turns from twelve o'clock)
    /// through `span` turns. `width` is the ring's thickness, `feather` its
    /// soft edge.
    #[allow(clippy::too_many_arguments)]
    pub fn arc(
        &mut self,
        cx: f32,
        cy: f32,
        radius: f32,
        width: f32,
        start: f32,
        span: f32,
        colour: Colour,
        feather: f32,
    ) {
        if span <= 0.0 || width <= 0.0 {
            return;
        }
        let span = span.min(1.0);
        let steps = steps_for(span);
        // Four radii per step: fade in, inner edge, outer edge, fade out --
        // three bands of two triangles each between one step and the next.
        if !self.room((steps + 1) * 4, steps * 18) {
            return;
        }
        let (inner, outer) = (radius - width / 2.0, radius + width / 2.0);
        let radii = [inner - feather, inner, outer, outer + feather];
        let colours = [colour.faded(), colour, colour, colour.faded()];
        let mut previous = [0; 4];
        for step in 0..=steps {
            let turn = start + span * step as f32 / steps as f32;
            let mut current = [0; 4];
            for k in 0..4 {
                let (x, y) = on_circle(cx, cy, radii[k].max(0.0), turn);
                current[k] = self.put(x, y, colours[k]);
            }
            if step > 0 {
                for k in 0..3 {
                    self.quad(previous[k], previous[k + 1], current[k + 1], current[k]);
                }
            }
            previous = current;
        }
    }

    /// A filled circle.
    pub fn disc(&mut self, cx: f32, cy: f32, radius: f32, colour: Colour, feather: f32) {
        if radius <= 0.0 {
            return;
        }
        let steps = steps_for(1.0);
        if !self.room(1 + (steps + 1) * 2, steps * 9) {
            return;
        }
        let centre = self.put(cx, cy, colour);
        let (mut previous_edge, mut previous_fade) = (0, 0);
        for step in 0..=steps {
            let turn = step as f32 / steps as f32;
            let (x, y) = on_circle(cx, cy, radius, turn);
            let (fx, fy) = on_circle(cx, cy, radius + feather, turn);
            let edge = self.put(x, y, colour);
            let fade = self.put(fx, fy, colour.faded());
            if step > 0 {
                self.indices.extend_from_slice(&[centre, previous_edge, edge]);
                self.quad(previous_edge, previous_fade, fade, edge);
            }
            previous_edge = edge;
            previous_fade = fade;
        }
    }

    /// A thick line, soft along both long edges.
    #[allow(clippy::too_many_arguments)]
    pub fn line(&mut self, x1: f32, y1: f32, x2: f32, y2: f32, width: f32, colour: Colour, feather: f32) {
        let (dx, dy) = (x2 - x1, y2 - y1);
        let length = (dx * dx + dy * dy).sqrt();
        if length <= 0.0 || !self.room(8, 18) {
            return;
        }
        // Perpendicular unit, scaled to half the width and to the feather beyond.
        let (nx, ny) = (-dy / length, dx / length);
        let (half, soft) = (width / 2.0, width / 2.0 + feather);
        let outer_a1 = self.put(x1 + nx * soft, y1 + ny * soft, colour.faded());
        let edge_a1 = self.put(x1 + nx * half, y1 + ny * half, colour);
        let edge_b1 = self.put(x1 - nx * half, y1 - ny * half, colour);
        let outer_b1 = self.put(x1 - nx * soft, y1 - ny * soft, colour.faded());
        let outer_a2 = self.put(x2 + nx * soft, y2 + ny * soft, colour.faded());
        let edge_a2 = self.put(x2 + nx * half, y2 + ny * half, colour);
        let edge_b2 = self.put(x2 - nx * half, y2 - ny * half, colour);
        let outer_b2 = self.put(x2 - nx * soft, y2 - ny * soft, colour.faded());
        self.quad(outer_a1, edge_a1, edge_a2, outer_a2);
        self.quad(edge_a1, edge_b1, edge_b2, edge_a2);
        self.quad(edge_b1, outer_b1, outer_b2, edge_b2);
    }

    /// A plain rectangle, no feather: the bar itself, whose edges are the
    /// screen's and one hairline.
    pub fn rect(&mut self, x: f32, y: f32, w: f32, h: f32, colour: Colour) {
        if w <= 0.0 || h <= 0.0 || !self.room(4, 6) {
            return;
        }
        let a = self.put(x, y, colour);
        let b = self.put(x + w, y, colour);
        let c = self.put(x + w, y + h, colour);
        let d = self.put(x, y + h, colour);
        self.quad(a, b, c, d);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const WHITE: Colour = Colour {
        r: 1.0,
        g: 1.0,
        b: 1.0,
        a: 1.0,
    };

    fn near(a: f32, b: f32, tolerance: f32) -> bool {
        (a - b).abs() <= tolerance
    }

    #[test]
    fn an_arc_starts_at_twelve_and_turns_clockwise() {
        let mut mesh = Mesh::default();
        mesh.arc(100.0, 100.0, 50.0, 10.0, 0.0, 0.25, WHITE, 1.0);
        // Four vertices per step; the second of the first four is the inner
        // edge at twelve o'clock, the second of the last four at three.
        let (first, last) = (mesh.vertices[1], mesh.vertices[mesh.vertices.len() - 3]);
        assert!(
            near(first.x, 100.0, 0.01) && first.y < 100.0,
            "it begins straight up"
        );
        assert!(
            last.x > 100.0 && near(last.y, 100.0, 0.01),
            "a quarter turn ends at three o'clock"
        );
    }

    #[test]
    fn an_arc_has_a_soft_edge() {
        let mut mesh = Mesh::default();
        mesh.arc(100.0, 100.0, 50.0, 10.0, 0.0, 1.0, WHITE, 1.0);
        assert!(
            mesh.vertices[0].colour.a == 0.0 && mesh.vertices[3].colour.a == 0.0,
            "outermost fade out"
        );
        assert!(
            mesh.vertices[1].colour.a == 1.0 && mesh.vertices[2].colour.a == 1.0,
            "the body is solid"
        );
    }

    #[test]
    fn nothing_is_drawn_for_nothing() {
        let mut mesh = Mesh::default();
        mesh.arc(100.0, 100.0, 50.0, 10.0, 0.0, 0.0, WHITE, 1.0);
        assert!(mesh.vertices.is_empty());
    }

    #[test]
    fn a_full_mesh_draws_less_rather_than_overflowing() {
        let mut mesh = Mesh::with_room(16, 16);
        mesh.arc(100.0, 100.0, 50.0, 10.0, 0.0, 1.0, WHITE, 1.0);
        mesh.disc(100.0, 100.0, 50.0, WHITE, 1.0);
        assert!(mesh.vertices.len() <= 16 && mesh.indices.len() <= 16);
    }

    #[test]
    fn a_rect_at_exactly_its_capacity_is_drawn_whole() {
        let mut mesh = Mesh::with_room(4, 6);
        mesh.rect(0.0, 0.0, 10.0, 10.0, WHITE);
        assert_eq!((mesh.vertices.len(), mesh.indices.len()), (4, 6));
    }

    #[test]
    fn short_of_room_draws_nothing_rather_than_half() {
        let (mut short_vertex, mut short_index) = (Mesh::with_room(3, 6), Mesh::with_room(4, 5));
        short_vertex.rect(0.0, 0.0, 10.0, 10.0, WHITE);
        short_index.rect(0.0, 0.0, 10.0, 10.0, WHITE);
        assert!(short_vertex.vertices.is_empty() && short_index.vertices.is_empty());
    }

    #[test]
    fn a_second_shape_is_dropped_once_the_first_fills_the_mesh() {
        let mut mesh = Mesh::with_room(4, 6);
        mesh.rect(0.0, 0.0, 10.0, 10.0, WHITE);
        mesh.rect(20.0, 20.0, 10.0, 10.0, WHITE);
        assert_eq!(mesh.vertices.len(), 4);
    }

    #[test]
    fn a_vertex_is_laid_out_as_sdl_expects() {
        // Position then an SDL_FColor, handed to SDL_RenderGeometryRaw by stride.
        assert_eq!(std::mem::size_of::<Colour>(), 16);
        assert_eq!(std::mem::size_of::<Vertex>(), 24);
        assert_eq!(std::mem::offset_of!(Vertex, colour), 8);
    }
}
