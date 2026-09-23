//! What one frame of the bar looks like, as a mesh -- no SDL, so a test can
//! ask where the ring is and whether the bar is on screen at all.
//!
//! The bar comes down from the top edge for two reasons and shows one at a
//! time:
//!
//!   - somebody is holding the exit chord: a red ring closes slowly across
//!     the hold, an X firming up inside it, and the game stops when it
//!     closes. This outranks everything; it is the one thing on screen that
//!     is about to end the session.
//!   - somebody is joining: one ring per pad holding a button, filling in its
//!     seat's colour, with a spinner turning inside so a hold that has just
//!     begun already reads as "working on it". A seat just taken shows as a
//!     solid disc with a tick for a moment before the bar goes back up.

use crate::shapes::{Colour, Mesh};
use crate::theme;

/// A pixel and a bit: wider reads as blur, narrower as the staircase back.
const FEATHER: f32 = 1.25;

/// The bar: the picker's background, nearly opaque.
const BAR: Colour = Colour::rgb(theme::BACKGROUND, 0.88);
const RED: Colour = Colour {
    r: 230.0 / 255.0,
    g: 40.0 / 255.0,
    b: 40.0 / 255.0,
    a: 1.0,
};

/// One frame's worth of what the kill switch knows.
#[derive(Debug, Clone, Default)]
pub struct Scene<'a> {
    /// The surface, in pixels.
    pub width: i32,
    /// The bar, in pixels: see [`bar_height`].
    pub bar_height: f32,
    /// The bar: 0 out of sight, 1 all the way down.
    pub position: f64,
    /// Pads holding to join, oldest first: how far, and the seat each fills.
    pub fractions: &'a [f32],
    pub players: &'a [i32],
    /// Seats just taken, oldest first.
    pub joined: &'a [i32],
    /// 0..1 through the exit hold; 0 when not held.
    pub exit_progress: f64,
    /// Seconds, for the spinner's turn.
    pub clock: f64,
}

/// The bar's height on a screen this tall: big enough to read from a sofa,
/// small enough to leave the game alone. A number rather than worked out
/// from the surface, because on layer-shell the surface *is* the bar and on
/// X11 it is the whole screen.
pub fn bar_height(screen_height: i32) -> f32 {
    (screen_height as f32 * 0.085).clamp(48.0, 128.0).round()
}

/// The colour of a seat: config/theme.yaml's `players`, the picker's four.
/// A seat past the last wraps, as the picker's does.
pub fn player_colour(player: i32) -> Colour {
    let Ok(seat) = usize::try_from(player - 1) else {
        return Colour::rgb(theme::TEXT_DIM, 1.0);
    };
    Colour::rgb(theme::PLAYERS[seat % theme::PLAYERS.len()], 1.0)
}

/// Where the n-th of `count` items sits across a bar this wide: centred, a
/// fixed step apart.
pub fn item_x(width: i32, bar_height: f32, index: usize, count: usize) -> f32 {
    let step = bar_height * 1.15;
    let span = step * count.saturating_sub(1) as f32;
    width as f32 / 2.0 - span / 2.0 + step * index as f32
}

fn exit_ring(mesh: &mut Mesh, cx: f32, cy: f32, radius: f32, width: f32, progress: f64) {
    let p = progress.min(1.0) as f32;
    mesh.arc(cx, cy, radius, width, 0.0, 1.0, RED.with_alpha(0.25), FEATHER);
    mesh.arc(cx, cy, radius, width, 0.0, p, RED, FEATHER);
    // The X firms up as the ring closes, so the last second reads as "now"
    // rather than as more of the same. Mixed towards the bar rather than
    // made see-through: two translucent strokes double up where they cross,
    // and the middle of the X was a brighter square than its arms.
    let reach = radius * 0.42;
    let firm = 0.35 + 0.6 * p;
    let cross = Colour {
        r: BAR.r + (RED.r - BAR.r) * firm,
        g: BAR.g + (RED.g - BAR.g) * firm,
        b: BAR.b + (RED.b - BAR.b) * firm,
        a: 1.0,
    };
    mesh.line(
        cx - reach,
        cy - reach,
        cx + reach,
        cy + reach,
        width * 0.9,
        cross,
        FEATHER,
    );
    mesh.line(
        cx + reach,
        cy - reach,
        cx - reach,
        cy + reach,
        width * 0.9,
        cross,
        FEATHER,
    );
}

#[allow(clippy::too_many_arguments)]
fn joining_ring(
    mesh: &mut Mesh,
    cx: f32,
    cy: f32,
    radius: f32,
    width: f32,
    fraction: f32,
    player: i32,
    clock: f64,
) {
    let colour = player_colour(player);
    mesh.arc(cx, cy, radius, width, 0.0, 1.0, colour.with_alpha(0.22), FEATHER);
    mesh.arc(cx, cy, radius, width, 0.0, fraction, colour, FEATHER);
    // The spinner: a short arc turning inside, once every 1.2 s. Its own
    // clock, not the fill's, so it moves the instant a hold begins.
    let turn = (clock / 1.2).rem_euclid(1.0) as f32;
    let ink = Colour::rgb(theme::TEXT, 0.85);
    mesh.arc(cx, cy, radius * 0.55, width * 0.7, turn, 0.22, ink, FEATHER);
}

fn joined_badge(mesh: &mut Mesh, cx: f32, cy: f32, radius: f32, width: f32, player: i32) {
    mesh.disc(cx, cy, radius + width / 2.0, player_colour(player), FEATHER);
    // A tick through the middle: short stroke down, long stroke up.
    let unit = radius * 0.5;
    let ink = BAR.with_alpha(0.95);
    let (x0, y0) = (cx - unit, cy);
    let (x1, y1) = (cx - unit * 0.25, cy + unit * 0.7);
    let (x2, y2) = (cx + unit, cy - unit * 0.75);
    mesh.line(x0, y0, x1, y1, width, ink, FEATHER);
    mesh.line(x1, y1, x2, y2, width, ink, FEATHER);
    // Where the strokes meet, so the corner is a corner and not a notch.
    mesh.disc(x1, y1, width / 2.0, ink, FEATHER);
}

/// The frame, into `mesh` (cleared first).
pub fn build(scene: &Scene, mesh: &mut Mesh) {
    mesh.clear();
    if scene.position <= 0.0 {
        return;
    }
    let bar = scene.bar_height;
    let width = scene.width as f32;
    let top = -bar * (1.0 - scene.position as f32);
    mesh.rect(0.0, top, width, bar, BAR);
    // A hairline under it, so the bar has an edge over a dark scene.
    mesh.rect(0.0, top + bar - 1.0, width, 1.0, Colour::rgb(theme::TEXT, 0.18));

    let cy = top + bar / 2.0;
    let radius = bar * 0.30;
    let stroke = bar * 0.085;
    if scene.exit_progress > 0.0 {
        exit_ring(mesh, width / 2.0, cy, radius, stroke, scene.exit_progress);
        return;
    }
    let count = scene.joined.len() + scene.fractions.len();
    let x = |at| item_x(scene.width, bar, at, count);
    for (at, &player) in scene.joined.iter().enumerate() {
        joined_badge(mesh, x(at), cy, radius, stroke, player);
    }
    let offset = scene.joined.len();
    for (i, (&fraction, &player)) in scene.fractions.iter().zip(scene.players).enumerate() {
        joining_ring(
            mesh,
            x(offset + i),
            cy,
            radius,
            stroke,
            fraction,
            player,
            scene.clock,
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::pairing::{HOLDS_MAX, JOINED_MAX};
    use crate::shapes::MESH_VERTICES;

    fn near(a: f32, b: f32, tolerance: f32) -> bool {
        (a - b).abs() <= tolerance
    }

    fn lowest_y(mesh: &Mesh) -> f32 {
        mesh.vertices.iter().map(|v| v.y).fold(f32::INFINITY, f32::min)
    }

    fn down(exit_progress: f64) -> Scene<'static> {
        Scene {
            width: 1280,
            bar_height: bar_height(800),
            position: 1.0,
            exit_progress,
            ..Scene::default()
        }
    }

    #[test]
    fn a_bar_out_of_sight_draws_nothing() {
        let mut mesh = Mesh::default();
        build(
            &Scene {
                position: 0.0,
                ..down(0.5)
            },
            &mut mesh,
        );
        assert!(mesh.vertices.is_empty());
    }

    #[test]
    fn the_bar_comes_down_from_the_top_edge() {
        let mut mesh = Mesh::default();
        build(&down(0.5), &mut mesh);
        assert!(
            near(lowest_y(&mesh), 0.0, 0.01),
            "all the way down, it sits on the top edge"
        );
        build(
            &Scene {
                position: 0.5,
                ..down(0.5)
            },
            &mut mesh,
        );
        assert!(
            near(lowest_y(&mesh), -bar_height(800) / 2.0, 0.01),
            "half way, half is above the screen"
        );
    }

    #[test]
    fn the_exit_ring_outranks_joining() {
        let mut mesh = Mesh::default();
        build(
            &Scene {
                fractions: &[0.5],
                players: &[2],
                ..down(0.5)
            },
            &mut mesh,
        );
        // Past the bar's own eight vertices, everything sits within the
        // ring's reach of the centre: nothing drawn for the pad joining.
        let reach = bar_height(800) * 0.5;
        assert!(mesh.vertices[8..].iter().all(|v| (v.x - 640.0).abs() <= reach));
    }

    #[test]
    fn items_are_centred_and_evenly_spaced() {
        let bar = bar_height(800);
        assert!(
            near(item_x(1280, bar, 0, 1), 640.0, 1e-3),
            "one item sits in the middle"
        );
        let (left, right) = (item_x(1280, bar, 0, 2), item_x(1280, bar, 1, 2));
        assert!(
            near(640.0 - left, right - 640.0, 1e-3),
            "two sit either side of it"
        );
    }

    #[test]
    fn seats_are_the_picker_s_colours() {
        let (one, five) = (player_colour(1), player_colour(5));
        assert!(
            near(one.r, 96.0 / 255.0, 1e-6) && near(one.g, 176.0 / 255.0, 1e-6),
            "player one is blue"
        );
        assert_eq!(five, one, "a fifth wraps round to the first colour");
        assert_eq!(
            player_colour(0),
            Colour::rgb(theme::TEXT_DIM, 1.0),
            "no seat is dim"
        );
    }

    #[test]
    fn a_full_scene_stays_centred_and_within_its_room() {
        let bar = bar_height(800);
        let count = HOLDS_MAX + JOINED_MAX;
        assert!(near(
            item_x(1280, bar, 0, count) + item_x(1280, bar, count - 1, count),
            1280.0,
            1e-3
        ));
        let fractions = [0.3; HOLDS_MAX];
        let players: Vec<i32> = (1..=HOLDS_MAX as i32).collect();
        let joined: Vec<i32> = (1..=JOINED_MAX as i32).collect();
        let mut mesh = Mesh::default();
        let scene = Scene {
            fractions: &fractions,
            players: &players,
            joined: &joined,
            clock: 0.5,
            ..down(0.0)
        };
        build(&scene, &mut mesh);
        // Short of the room, so the last badge was not cut off by running out.
        assert!(!mesh.vertices.is_empty() && mesh.vertices.len() < MESH_VERTICES);
    }
}
