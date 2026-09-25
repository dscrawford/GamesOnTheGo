//! What one frame of the bar looks like -- no SDL, so a test can ask where
//! the ring is and whether the bar is on screen at all.
//!
//! The bar comes down from the top edge for two reasons and shows one at a
//! time:
//!
//!   - somebody is holding the exit chord: a red ring closes slowly across
//!     the hold, an X firming up inside it, and the game stops when it
//!     closes. This outranks everything; it is the one thing on screen that
//!     is about to end the session.
//!   - somebody is joining: the pad's own drawing -- the picker's -- revealed
//!     clockwise in its seat's colour over a dim copy of itself, the way the
//!     picker's strip draws a hold, so a hold looks like one thing wherever
//!     it happens. A seat just taken is the whole drawing with a tick, for a
//!     moment before the bar goes back up.
//!
//! Flat shapes are a mesh; a drawing is a [`Sprite`] the painter draws from
//! a texture. What goes over a drawing (the tick) is a second mesh.

use crate::consoles::CONSOLES;
use crate::frame::Rebinding;
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
    /// Pads holding to join, oldest first: how far, the seat each fills, and
    /// the drawing that stands for it.
    pub fractions: &'a [f32],
    pub players: &'a [i32],
    pub hold_icons: &'a [u8],
    /// Seats just taken, oldest first, and their drawings.
    pub joined: &'a [i32],
    pub joined_icons: &'a [u8],
    /// 0..1 through the exit hold; 0 when not held.
    pub exit_progress: f64,
    /// The panel a rebind pulls down, in pixels: see [`panel_height`].
    pub panel_height: f32,
    /// A controller being rebound, if one is.
    pub rebind: Option<Rebinding>,
}

/// A controller's drawing, centred at (`cx`, `cy`) and `height` tall: shown
/// in `colour` clockwise from twelve through `revealed` of a turn, over the
/// whole of it in `under` where it is not yet.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Sprite {
    pub icon: u8,
    pub cx: f32,
    pub cy: f32,
    pub height: f32,
    pub colour: Colour,
    pub revealed: f32,
    pub under: Colour,
    /// A console's drawing in its own colours ([`crate::consoles::picture`];
    /// `icon` is then the console), rather than a pad's silhouette.
    pub picture: bool,
}

/// A frame: flat shapes under the drawings, the drawings, and what goes on
/// top of them. Reused from frame to frame, so drawing allocates nothing.
#[derive(Debug, Clone, Default)]
pub struct Drawing {
    pub under: Mesh,
    pub sprites: Vec<Sprite>,
    pub over: Mesh,
}

impl Drawing {
    fn clear(&mut self) {
        self.under.clear();
        self.sprites.clear();
        self.over.clear();
    }
}

/// The bar's height on a screen this tall: big enough to read from a sofa,
/// small enough to leave the game alone. A number rather than worked out
/// from the surface, because on layer-shell the surface *is* the bar and on
/// X11 it is the whole screen.
pub fn bar_height(screen_height: i32) -> f32 {
    (screen_height as f32 * 0.085).clamp(48.0, 128.0).round()
}

/// How far a rebind pulls the bar down: room for a controller's drawing big
/// enough to find one button on from a sofa. The overlay's window is this
/// tall wherever it is only a strip, so the panel has somewhere to be.
pub fn panel_height(screen_height: i32) -> f32 {
    (screen_height as f32 * 0.46)
        .round()
        .max(bar_height(screen_height))
}

/// The colour of a seat: config/theme.yaml's `players`, the picker's four.
/// A seat past the last wraps, as the picker's does.
pub fn player_colour(player: i32) -> Colour {
    // `player` crosses the painter's pipe; any i32 is drawable, none panics.
    let Some(seat) = player.checked_sub(1).and_then(|seat| usize::try_from(seat).ok()) else {
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

/// The seat just taken's tick, on a disc of its colour at the drawing's
/// lower right, so the drawing itself stays the pad somebody is holding.
fn tick(over: &mut Mesh, cx: f32, cy: f32, radius: f32, stroke: f32, colour: Colour) {
    over.disc(cx, cy, radius, colour, FEATHER);
    // Short stroke down, long stroke up.
    let unit = radius * 0.55;
    let ink = BAR.with_alpha(0.95);
    let (x0, y0) = (cx - unit, cy);
    let (x1, y1) = (cx - unit * 0.25, cy + unit * 0.7);
    let (x2, y2) = (cx + unit, cy - unit * 0.75);
    over.line(x0, y0, x1, y1, stroke, ink, FEATHER);
    over.line(x1, y1, x2, y2, stroke, ink, FEATHER);
    // Where the strokes meet, so the corner is a corner and not a notch.
    over.disc(x1, y1, stroke / 2.0, ink, FEATHER);
}

/// Where a rebind's parts go on a panel `panel` tall at `top`: the drawing,
/// centred, and its height and width.
fn rebind_layout(scene: &Scene, rebind: &Rebinding, top: f32) -> (f32, f32, f32, f32) {
    let panel = scene.panel_height;
    let aspect = CONSOLES
        .get(rebind.console as usize)
        .map_or(1.6, |console| console.aspect);
    let height = (panel * 0.72).min(scene.width as f32 * 0.8 / aspect);
    let (cx, cy) = (scene.width as f32 / 2.0, top + panel * 0.44);
    (cx, cy, height, height * aspect)
}

/// A controller being rebound: its drawing, the button being asked for
/// ringed in the seat's colour, a dot a step under it, and the ring that
/// fills while the early finish is held.
fn build_rebind(scene: &Scene, rebind: &Rebinding, top: f32, drawing: &mut Drawing) {
    let panel = scene.panel_height;
    let width = scene.width as f32;
    let colour = player_colour(rebind.player);
    drawing.under.rect(0.0, top, width, panel, BAR);
    drawing
        .under
        .rect(0.0, top + panel - 1.0, width, 1.0, Colour::rgb(theme::TEXT, 0.18));
    let (cx, cy, height, drawn_width) = rebind_layout(scene, rebind, top);
    drawing.sprites.push(Sprite {
        icon: u8::try_from(rebind.console).unwrap_or(0),
        cx,
        cy,
        height,
        // Untinted: the vertex colour multiplies the drawing's own.
        colour: Colour {
            r: 1.0,
            g: 1.0,
            b: 1.0,
            a: 1.0,
        },
        revealed: 1.0,
        under: Colour::rgb(theme::EMPTY, 1.0),
        picture: true,
    });
    let console = CONSOLES.get(rebind.console as usize);
    let asked = usize::try_from(rebind.control)
        .ok()
        .and_then(|at| console?.controls.get(at));
    if rebind.ended == 0
        && let Some((u, v)) = asked.and_then(|control| control.anchor)
    {
        let (x, y) = (
            cx - drawn_width / 2.0 + u * drawn_width,
            cy - height / 2.0 + v * height,
        );
        let radius = height * 0.075;
        drawing.over.disc(x, y, radius, colour.with_alpha(0.28), FEATHER);
        drawing
            .over
            .arc(x, y, radius, radius * 0.28, 0.0, 1.0, colour, FEATHER);
    }
    // One dot a step, the ones done in the seat's colour.
    let dots_y = top + panel * 0.9;
    let total = rebind.total.clamp(0, 40);
    let step = (panel * 0.045).min(width * 0.8 / total.max(1) as f32);
    for i in 0..total {
        let x = width / 2.0 + (i as f32 - (total - 1) as f32 / 2.0) * step;
        let dot = if i < rebind.index {
            colour
        } else if i == rebind.index && rebind.ended == 0 {
            Colour::rgb(theme::TEXT, 1.0)
        } else {
            Colour::rgb(theme::EMPTY, 1.0)
        };
        drawing.under.disc(x, dots_y, step * 0.28, dot, FEATHER);
    }
    // The early finish: a ring to the right of the drawing, filling while the
    // hold runs, as the wizard draws it at the gate.
    let (fx, fy, fr) = (cx + drawn_width / 2.0 + panel * 0.1, cy, panel * 0.06);
    if rebind.finish > 0.0 && rebind.ended == 0 {
        drawing
            .over
            .arc(fx, fy, fr, fr * 0.3, 0.0, 1.0, colour.with_alpha(0.25), FEATHER);
        drawing.over.arc(
            fx,
            fy,
            fr,
            fr * 0.3,
            0.0,
            rebind.finish.clamp(0.0, 1.0),
            colour,
            FEATHER,
        );
    }
    if rebind.ended == 1 {
        tick(&mut drawing.over, fx, fy, fr, fr * 0.35, colour);
    }
}

/// The frame, into `drawing` (cleared first).
pub fn build(scene: &Scene, drawing: &mut Drawing) {
    drawing.clear();
    if scene.position <= 0.0 {
        return;
    }
    // A rebind is the whole panel coming down, unless the exit is being held:
    // that is about to end the game, rebinding or not.
    if let Some(rebind) = scene.rebind.filter(|_| scene.exit_progress <= 0.0) {
        let top = -scene.panel_height * (1.0 - scene.position as f32);
        build_rebind(scene, &rebind, top, drawing);
        return;
    }
    let bar = scene.bar_height;
    let width = scene.width as f32;
    let top = -bar * (1.0 - scene.position as f32);
    let mesh = &mut drawing.under;
    mesh.rect(0.0, top, width, bar, BAR);
    // A hairline under it, so the bar has an edge over a dark scene.
    mesh.rect(0.0, top + bar - 1.0, width, 1.0, Colour::rgb(theme::TEXT, 0.18));

    let cy = top + bar / 2.0;
    if scene.exit_progress > 0.0 {
        exit_ring(
            mesh,
            width / 2.0,
            cy,
            bar * 0.30,
            bar * 0.085,
            scene.exit_progress,
        );
        return;
    }
    let icon_height = bar * 0.62;
    let count = scene.joined.len() + scene.fractions.len();
    let x = |at| item_x(scene.width, bar, at, count);
    let empty = Colour::rgb(theme::EMPTY, 1.0);
    for (at, (&player, &icon)) in scene.joined.iter().zip(scene.joined_icons).enumerate() {
        let colour = player_colour(player);
        let sprite = Sprite {
            icon,
            cx: x(at),
            cy,
            height: icon_height,
            colour,
            revealed: 1.0,
            under: empty,
            picture: false,
        };
        drawing.sprites.push(sprite);
        let (tx, ty) = (x(at) + icon_height * 0.5, cy + icon_height * 0.32);
        tick(&mut drawing.over, tx, ty, bar * 0.13, bar * 0.045, colour);
    }
    let offset = scene.joined.len();
    let holds = scene.fractions.iter().zip(scene.players).zip(scene.hold_icons);
    for (i, ((&fraction, &player), &icon)) in holds.enumerate() {
        drawing.sprites.push(Sprite {
            icon,
            cx: x(offset + i),
            cy,
            height: icon_height,
            colour: player_colour(player),
            revealed: fraction.clamp(0.0, 1.0),
            under: empty,
            picture: false,
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::pairing::{HOLDS_MAX, JOINED_MAX};

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

    fn n64() -> (u32, usize) {
        let console = crate::consoles::for_platform("n64");
        let a = CONSOLES[console].control("a").expect("an N64 has A");
        (console as u32, a)
    }

    fn rebinding(control: i32, ended: u32) -> Scene<'static> {
        let (console, _) = n64();
        Scene {
            panel_height: panel_height(800),
            rebind: Some(Rebinding {
                player: 2,
                console,
                control,
                index: 3,
                total: 14,
                finish: 0.0,
                ended,
            }),
            ..down(0.0)
        }
    }

    fn highest_y(mesh: &Mesh) -> f32 {
        mesh.vertices
            .iter()
            .map(|v| v.y)
            .fold(f32::NEG_INFINITY, f32::max)
    }

    #[test]
    fn a_rebind_pulls_the_panel_down_with_the_consoles_drawing_in_colour() {
        let (console, a) = n64();
        let mut drawing = Drawing::default();
        build(&rebinding(a as i32, 0), &mut drawing);
        assert!(
            near(highest_y(&drawing.under), panel_height(800), 0.5),
            "the panel, not the bar: {}",
            highest_y(&drawing.under)
        );
        assert!(
            panel_height(800) > bar_height(800) * 2.0,
            "and it is further down"
        );
        let [picture] = drawing.sprites.as_slice() else {
            panic!("one drawing: {:?}", drawing.sprites)
        };
        assert!(picture.picture && u32::from(picture.icon) == console);
    }

    #[test]
    fn the_button_being_asked_for_is_ringed_where_the_drawing_has_it() {
        let (_, a) = n64();
        let scene = rebinding(a as i32, 0);
        let mut drawing = Drawing::default();
        build(&scene, &mut drawing);
        let (u, v) = CONSOLES[n64().0 as usize].controls[a].anchor.expect("A is drawn");
        let picture = drawing.sprites[0];
        let width = picture.height * CONSOLES[n64().0 as usize].aspect;
        let (x, y) = (
            picture.cx - width / 2.0 + u * width,
            picture.cy - picture.height / 2.0 + v * picture.height,
        );
        let ring = &drawing.over.vertices;
        assert!(!ring.is_empty(), "a ring over the drawing");
        let (mx, my) = (
            ring.iter().map(|p| p.x).sum::<f32>() / ring.len() as f32,
            ring.iter().map(|p| p.y).sum::<f32>() / ring.len() as f32,
        );
        assert!(
            near(mx, x, 2.0) && near(my, y, 2.0),
            "ring at ({mx}, {my}), A at ({x}, {y})"
        );
        assert!(
            ring.iter().any(|p| near(p.colour.r, player_colour(2).r, 0.01)),
            "in the seat's colour"
        );
    }

    #[test]
    fn a_button_the_drawing_cannot_show_rings_nothing_and_a_rebind_before_its_first_step_neither() {
        let (console, _) = n64();
        let z = CONSOLES[console as usize].control("lefttrigger").expect("Z");
        for control in [z as i32, -1] {
            let mut drawing = Drawing::default();
            build(&rebinding(control, 0), &mut drawing);
            assert!(
                drawing.over.vertices.is_empty(),
                "control {control} ringed something"
            );
            assert_eq!(drawing.sprites.len(), 1, "the drawing is still there");
        }
    }

    #[test]
    fn the_exit_outranks_a_rebind() {
        let (_, a) = n64();
        let mut drawing = Drawing::default();
        build(
            &Scene {
                exit_progress: 0.5,
                ..rebinding(a as i32, 0)
            },
            &mut drawing,
        );
        assert!(
            drawing.sprites.is_empty(),
            "no controller while the game is being stopped"
        );
        assert!(
            near(highest_y(&drawing.under), bar_height(800), 0.5),
            "and only the bar"
        );
    }

    #[test]
    fn a_kept_rebind_ends_on_a_tick_and_rings_no_button() {
        let (_, a) = n64();
        let mut walking = Drawing::default();
        build(&rebinding(a as i32, 0), &mut walking);
        let mut kept = Drawing::default();
        build(&rebinding(a as i32, 1), &mut kept);
        let mut dropped = Drawing::default();
        build(&rebinding(a as i32, 2), &mut dropped);
        assert!(!kept.over.vertices.is_empty(), "a tick");
        assert!(dropped.over.vertices.is_empty(), "nothing kept, nothing ticked");
        let ring_at =
            |d: &Drawing| d.over.vertices.iter().map(|p| p.x).sum::<f32>() / d.over.vertices.len() as f32;
        assert!(
            ring_at(&kept) > ring_at(&walking) + 50.0,
            "the tick sits beside the drawing, not on A"
        );
    }

    #[test]
    fn a_bar_out_of_sight_draws_nothing() {
        let mut drawing = Drawing::default();
        build(
            &Scene {
                position: 0.0,
                joined: &[1],
                joined_icons: &[0],
                ..down(0.5)
            },
            &mut drawing,
        );
        assert!(drawing.under.vertices.is_empty() && drawing.sprites.is_empty());
    }

    #[test]
    fn the_bar_comes_down_from_the_top_edge() {
        let mut drawing = Drawing::default();
        build(&down(0.5), &mut drawing);
        assert!(
            near(lowest_y(&drawing.under), 0.0, 0.01),
            "all the way down, it sits on the top edge"
        );
        build(
            &Scene {
                position: 0.5,
                ..down(0.5)
            },
            &mut drawing,
        );
        assert!(
            near(lowest_y(&drawing.under), -bar_height(800) / 2.0, 0.01),
            "half way, half above"
        );
    }

    #[test]
    fn the_exit_ring_outranks_joining() {
        let mut drawing = Drawing::default();
        let scene = Scene {
            fractions: &[0.5],
            players: &[2],
            hold_icons: &[3],
            ..down(0.5)
        };
        build(&scene, &mut drawing);
        assert!(
            drawing.sprites.is_empty(),
            "no pad's drawing while the exit is held"
        );
        let reach = bar_height(800) * 0.5;
        assert!(
            drawing.under.vertices[8..]
                .iter()
                .all(|v| (v.x - 640.0).abs() <= reach)
        );
    }

    #[test]
    fn a_hold_is_its_pad_s_drawing_revealed_in_its_seat_s_colour() {
        let mut drawing = Drawing::default();
        let scene = Scene {
            fractions: &[0.4],
            players: &[2],
            hold_icons: &[7],
            ..down(0.0)
        };
        build(&scene, &mut drawing);
        let [sprite] = drawing.sprites[..] else {
            panic!("one hold, one drawing: {:?}", drawing.sprites)
        };
        assert_eq!((sprite.icon, sprite.revealed), (7, 0.4));
        assert_eq!(sprite.colour, player_colour(2));
        assert_eq!(
            sprite.under,
            Colour::rgb(theme::EMPTY, 1.0),
            "over the empty seat's dim copy"
        );
        assert!(near(sprite.cx, 640.0, 1e-3), "centred");
        assert!(
            drawing.over.vertices.is_empty(),
            "no tick until the seat is taken"
        );
    }

    #[test]
    fn a_seat_just_taken_is_the_whole_drawing_with_a_tick() {
        let mut drawing = Drawing::default();
        build(
            &Scene {
                joined: &[1],
                joined_icons: &[4],
                ..down(0.0)
            },
            &mut drawing,
        );
        let [sprite] = drawing.sprites[..] else {
            panic!("one seat, one drawing")
        };
        assert_eq!(
            (sprite.icon, sprite.revealed, sprite.colour),
            (4, 1.0, player_colour(1))
        );
        assert!(!drawing.over.vertices.is_empty(), "and its tick over it");
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
    fn a_full_scene_draws_every_seat_in_order() {
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
        let (icons, joined_icons) = ([1; HOLDS_MAX], [2; JOINED_MAX]);
        let scene = Scene {
            fractions: &fractions,
            players: &players,
            hold_icons: &icons,
            joined: &joined,
            joined_icons: &joined_icons,
            ..down(0.0)
        };
        let mut drawing = Drawing::default();
        build(&scene, &mut drawing);
        assert_eq!(drawing.sprites.len(), count, "seats first, then holds");
        assert!(
            drawing.sprites.windows(2).all(|pair| pair[0].cx < pair[1].cx),
            "left to right"
        );
        assert!(drawing.sprites[..JOINED_MAX].iter().all(|s| s.revealed == 1.0));
    }

    #[test]
    fn any_player_number_from_the_pipe_is_drawable() {
        // A frame's player numbers are not range-checked on the way in.
        for player in [i32::MIN, -1, 0] {
            assert_eq!(
                player_colour(player),
                Colour::rgb(theme::TEXT_DIM, 1.0),
                "{player}"
            );
        }
        for player in [i32::MAX, 100] {
            let _ = player_colour(player);
        }
    }
}
