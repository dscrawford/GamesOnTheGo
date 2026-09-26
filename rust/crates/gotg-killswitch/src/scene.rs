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
use crate::leaders;
use crate::shapes::{Colour, Mesh};
use crate::text;
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

/// Where a label sits against its point: its left edge, its middle, or its
/// right edge there; vertically it is always centred on the point.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Align {
    Left,
    Centre,
    Right,
}

/// A line of words, drawn last, over everything else.
#[derive(Debug, Clone, PartialEq)]
pub struct Label {
    pub text: String,
    pub x: f32,
    pub y: f32,
    pub size: f32,
    pub colour: Colour,
    pub align: Align,
}

/// A frame: flat shapes under the drawings, the drawings, what goes on top
/// of them, and the words. Reused from frame to frame.
#[derive(Debug, Clone, Default)]
pub struct Drawing {
    pub under: Mesh,
    pub sprites: Vec<Sprite>,
    /// Leader lines: over the drawing they point into, under its ring.
    pub lines: Mesh,
    pub over: Mesh,
    pub labels: Vec<Label>,
}

impl Drawing {
    fn clear(&mut self) {
        self.under.clear();
        self.sprites.clear();
        self.lines.clear();
        self.over.clear();
        self.labels.clear();
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

/// The card everything sits on: centred on the top edge, `w` wide and `h`
/// tall, its bottom corners rounded and a hairline under it so it has an
/// edge over a dark scene. Sized to what it holds rather than the width of
/// the screen -- a ring for the exit, a row of pads for joining, a
/// controller for a rebind -- so it covers only as much of the game as it
/// has to, and sits in the middle however wide the screen is.
fn card(mesh: &mut Mesh, cx: f32, top: f32, w: f32, h: f32) {
    let r = (h * 0.22).min(w / 2.0).min(28.0);
    let x = cx - w / 2.0;
    mesh.rect(x, top, w, h - r, BAR);
    mesh.rect(x + r, top + h - r, w - 2.0 * r, r, BAR);
    // Filled quarters: a ring as thick as its radius, centred on half of it.
    mesh.arc(x + r, top + h - r, r / 2.0, r, 0.5, 0.25, BAR, FEATHER);
    mesh.arc(x + w - r, top + h - r, r / 2.0, r, 0.25, 0.25, BAR, FEATHER);
    mesh.rect(
        x + r,
        top + h - 1.0,
        w - 2.0 * r,
        1.0,
        Colour::rgb(theme::TEXT, 0.18),
    );
}

/// How wide the joining card is for `count` pads: the row, and a margin.
pub fn joining_width(bar_height: f32, count: usize) -> f32 {
    (bar_height * 1.15 * count as f32 + bar_height * 0.5).max(exit_width(bar_height))
}

/// How wide the exit card is: the ring and its margin.
pub fn exit_width(bar_height: f32) -> f32 {
    bar_height * 1.6
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
/// Where the labels' rail may reach down to, and where the words under the
/// drawing sit, as fractions of the panel.
const RAIL_BOTTOM: f32 = 0.74;
const SAID_AT: f32 = 0.82;

/// How the rebind panel is laid out: the drawing's centre, height and width,
/// the labels' size, and how wide the card is to hold drawing and labels.
struct RebindLayout {
    cx: f32,
    cy: f32,
    height: f32,
    drawn_width: f32,
    label_size: f32,
    card_width: f32,
}

fn rebind_layout(scene: &Scene, rebind: &Rebinding, top: f32) -> RebindLayout {
    let panel = scene.panel_height;
    let screen = scene.width as f32;
    let console = CONSOLES.get(rebind.console as usize);
    let aspect = console.map_or(1.6, |console| console.aspect);
    let label_size = panel * 0.045;
    // The longest label a rail holds sets how far the card reaches past the
    // drawing on each side.
    let longest = console
        .map(|console| {
            console
                .controls
                .iter()
                .filter(|c| c.anchor.is_some())
                .map(|c| text::width(c.label, label_size))
                .fold(0.0, f32::max)
        })
        .unwrap_or(0.0);
    let beside = |drawn: f32| drawn * leaders::GUTTER_FRACTION + longest + label_size * 1.5;
    // The labels may reach 18% of the drawing's height past it at either end
    // (leaders::place's band); that whole band sits between the card's top
    // and the words under it, so a pad with a long rail cannot run into them.
    let band_top = top + panel * 0.05;
    let band_bottom = top + panel * RAIL_BOTTOM;
    let mut height = (panel * 0.6).min((band_bottom - band_top) / 1.36);
    let mut drawn_width = height * aspect;
    let most = screen * 0.96;
    if drawn_width + 2.0 * beside(drawn_width) > most {
        // Too wide for the screen: a smaller drawing, labels kept readable.
        drawn_width = ((most - 2.0 * (longest + label_size * 1.5)) / (1.0 + 2.0 * leaders::GUTTER_FRACTION))
            .max(panel * 0.2);
        height = drawn_width / aspect;
    }
    RebindLayout {
        cx: screen / 2.0,
        cy: band_top + height * 0.18 + height / 2.0,
        height,
        drawn_width,
        label_size,
        card_width: (drawn_width + 2.0 * beside(drawn_width)).min(screen),
    }
}

/// Which rail a control's label goes on when its half would be wrong: the
/// shoulders and triggers sit near the top middle, and a midline split would
/// run their leaders across the pad -- the picker pins them the same way.
fn pinned_side(id: &str) -> Option<leaders::Side> {
    let shoulder = id.ends_with("shoulder") || id.ends_with("trigger");
    if shoulder && id.starts_with("left") {
        Some(leaders::Side::Left)
    } else if (shoulder && id.starts_with("right")) || id.starts_with("rightstick") {
        Some(leaders::Side::Right)
    } else {
        None
    }
}

/// A controller being rebound: its drawing, the button being asked for
/// ringed in the seat's colour, a dot a step under it, and the ring that
/// fills while the early finish is held.
fn build_rebind(scene: &Scene, rebind: &Rebinding, top: f32, drawing: &mut Drawing) {
    let panel = scene.panel_height;
    let width = scene.width as f32;
    let colour = player_colour(rebind.player);
    let layout = rebind_layout(scene, rebind, top);
    let (cx, cy, height, drawn_width) = (layout.cx, layout.cy, layout.height, layout.drawn_width);
    let total = rebind.total.clamp(0, 40);
    let dots = panel * 0.045 * total as f32;
    let card_width = layout.card_width.max(dots + panel * 0.2).min(width);
    card(&mut drawing.under, cx, top, card_width, panel);
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
    // Every control the drawing shows, labelled down a rail on its side with
    // a line to its button -- the picker's controller screen -- the one being
    // asked for lit in the seat's colour.
    if let Some(console) = console {
        let at = |(u, v): (f32, f32)| {
            (
                cx - drawn_width / 2.0 + u * drawn_width,
                cy - height / 2.0 + v * height,
            )
        };
        let anchors: Vec<leaders::Anchor> = console
            .controls
            .iter()
            .enumerate()
            .filter_map(|(id, control)| {
                let (x, y) = at(control.anchor?);
                Some(leaders::Anchor { id, x, y })
            })
            .collect();
        let label_height = layout.label_size * 1.3;
        let diagram = (cx - drawn_width / 2.0, cy - height / 2.0, drawn_width, height);
        let pinned = |id: usize| pinned_side(console.controls[id].id);
        for placed in leaders::place(&anchors, diagram, label_height, &pinned) {
            let control = console.controls[placed.anchor.id];
            let lit = rebind.ended == 0 && usize::try_from(rebind.control).ok() == Some(placed.anchor.id);
            let ink = if lit {
                colour
            } else {
                Colour::rgb(theme::TEXT_DIM, 0.9)
            };
            drawing.lines.line(
                placed.from.0,
                placed.from.1,
                placed.to.0,
                placed.to.1,
                if lit { 2.5 } else { 1.25 },
                ink.with_alpha(if lit { 1.0 } else { 0.55 }),
                FEATHER,
            );
            drawing.labels.push(Label {
                text: control.label.to_owned(),
                x: placed.to.0
                    + if placed.side == leaders::Side::Left {
                        -6.0
                    } else {
                        6.0
                    },
                y: placed.to.1,
                size: layout.label_size,
                colour: if lit {
                    colour
                } else {
                    Colour::rgb(theme::TEXT_DIM, 1.0)
                },
                align: if placed.side == leaders::Side::Left {
                    Align::Right
                } else {
                    Align::Left
                },
            });
        }
    }
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
    // What is being asked for, in words, under the drawing: the button's
    // name as the console config says it, or what is happening instead.
    let said = match (rebind.ended, asked) {
        (1, _) => "Saved".to_owned(),
        (2, _) => "Nothing changed".to_owned(),
        (_, Some(control)) => format!("Press {}", control.label),
        (_, None) => "Getting ready".to_owned(),
    };
    let said_size = panel * 0.06;
    let said_y = top + panel * SAID_AT;
    let said_width = text::width(&said, said_size);
    drawing.labels.push(Label {
        text: said,
        x: cx,
        y: said_y,
        size: said_size,
        colour: Colour::rgb(theme::TEXT, 1.0),
        align: Align::Centre,
    });
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
    // The early finish: a ring beside the words, filling while the hold that
    // ends the walk runs, as the wizard draws it at the gate.
    let (fx, fy, fr) = (cx + said_width / 2.0 + said_size * 1.2, said_y, said_size * 0.55);
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
    let count = scene.joined.len() + scene.fractions.len();
    let card_width = if scene.exit_progress > 0.0 {
        exit_width(bar)
    } else {
        joining_width(bar, count)
    };
    card(mesh, width / 2.0, top, card_width.min(width), bar);

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
            near(highest_y(&drawing.under), panel_height(800), 2.0),
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
            near(highest_y(&drawing.under), bar_height(800), 2.0),
            "and only the bar"
        );
    }

    #[test]
    fn the_button_being_asked_for_is_named_in_words() {
        let (console, a) = n64();
        let label = CONSOLES[console as usize].controls[a].label;
        let mut drawing = Drawing::default();
        build(&rebinding(a as i32, 0), &mut drawing);
        let said: Vec<&str> = drawing.labels.iter().map(|l| l.text.as_str()).collect();
        assert!(said.contains(&format!("Press {label}").as_str()), "{said:?}");
        build(&rebinding(a as i32, 1), &mut drawing);
        assert!(drawing.labels.iter().any(|l| l.text == "Saved"));
    }

    #[test]
    fn every_label_stays_above_the_words_under_the_drawing() {
        // A GameCube pad has eleven labels down one side: its rail ran into
        // "Press D-pad up" and the finish ring beside it.
        for platform in ["n64", "gamecube", "switch", "snes"] {
            let console = crate::consoles::for_platform(platform);
            let mut drawing = Drawing::default();
            build(
                &Scene {
                    rebind: Some(Rebinding {
                        player: 1,
                        console: console as u32,
                        control: 0,
                        index: 0,
                        total: 14,
                        finish: 0.0,
                        ended: 0,
                    }),
                    ..rebinding(0, 0)
                },
                &mut drawing,
            );
            let said = drawing
                .labels
                .iter()
                .find(|l| l.text.starts_with("Press"))
                .expect("words");
            for label in drawing.labels.iter().filter(|l| l.align != Align::Centre) {
                assert!(
                    label.y + label.size / 2.0 < said.y - said.size / 2.0,
                    "{platform}: {} at {} runs into the words at {}",
                    label.text,
                    label.y,
                    said.y
                );
            }
        }
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
            |d: &Drawing| d.over.vertices.iter().map(|p| p.y).sum::<f32>() / d.over.vertices.len() as f32;
        assert!(
            ring_at(&kept) > ring_at(&walking) + 50.0,
            "the tick sits by the words under the drawing, not on A"
        );
    }

    fn span_x(mesh: &Mesh) -> (f32, f32) {
        let xs = mesh.vertices.iter().map(|v| v.x);
        (
            xs.clone().fold(f32::INFINITY, f32::min),
            xs.fold(f32::NEG_INFINITY, f32::max),
        )
    }

    #[test]
    fn the_card_is_centred_and_no_wider_than_it_has_to_be() {
        let mut drawing = Drawing::default();
        build(&down(0.5), &mut drawing);
        let (left, right) = span_x(&drawing.under);
        assert!(near((left + right) / 2.0, 640.0, 1.0), "centred: {left}..{right}");
        assert!(
            right - left < 400.0,
            "an exit ring's card, not the screen: {}",
            right - left
        );
    }

    #[test]
    fn the_exit_card_is_smallest_and_a_rebind_card_largest() {
        let width_of = |scene: &Scene| {
            let mut drawing = Drawing::default();
            build(scene, &mut drawing);
            let (left, right) = span_x(&drawing.under);
            right - left
        };
        let exit = width_of(&down(0.5));
        let joining = width_of(&Scene {
            joined: &[1, 2, 3],
            joined_icons: &[0, 0, 0],
            ..down(0.0)
        });
        let (_, a) = n64();
        let rebind = width_of(&rebinding(a as i32, 0));
        assert!(
            exit < joining && joining < rebind,
            "{exit} < {joining} < {rebind}"
        );
    }

    #[test]
    fn a_card_of_more_pads_is_wider() {
        let bar = bar_height(800);
        assert!(joining_width(bar, 4) > joining_width(bar, 1));
        assert_eq!(
            joining_width(bar, 0),
            exit_width(bar),
            "never smaller than the exit's"
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
        // Only the exit's card and ring: nothing reaches past it to where a
        // joining pad would be drawn.
        let reach = exit_width(bar_height(800)) / 2.0 + FEATHER + 0.5;
        assert!(
            drawing
                .under
                .vertices
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
