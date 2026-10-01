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
use crate::frame::{EMPTY_SEAT, MenuFrame, Rebinding, Saying};
use crate::leaders;
use crate::loading::SaveLine;
use crate::menu::{Browse, Listed};
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
    /// The menu, if somebody has it down.
    pub menu: Option<MenuFrame>,
    /// Something the bar says on its own.
    pub saying: Saying,
    /// danstick has nobody seated and nobody joining.
    pub nobody: bool,
    /// The saves the menu lists, as the client said them: read by the
    /// painter from the session, never carried in a frame.
    pub saves: &'a [SaveLine],
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
    (screen_height as f32 * 0.55)
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
    labelled_layout(
        scene,
        rebind.console,
        (top + panel * 0.05, top + panel * RAIL_BOTTOM),
        0.0,
    )
}

/// A console's drawing with its labels on rails either side, fitted between
/// `band`'s top and bottom, with `extra` more room past each rail.
fn labelled_layout(scene: &Scene, console: u32, band: (f32, f32), extra: f32) -> RebindLayout {
    let panel = scene.panel_height;
    let screen = scene.width as f32;
    let console = CONSOLES.get(console as usize);
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
    let beside = |drawn: f32| drawn * leaders::GUTTER_FRACTION + longest + label_size * 1.5 + extra;
    // The labels may reach 18% of the drawing's height past it at either end
    // (leaders::place's band); that whole band sits between the card's top
    // and the words under it, so a pad with a long rail cannot run into them.
    let (band_top, band_bottom) = band;
    let mut height = (panel * 0.6).min((band_bottom - band_top) / 1.36);
    let mut drawn_width = height * aspect;
    let most = screen * 0.96;
    if drawn_width + 2.0 * beside(drawn_width) > most {
        // Too wide for the screen: a smaller drawing, labels kept readable.
        drawn_width = ((most - 2.0 * (longest + label_size * 1.5 + extra))
            / (1.0 + 2.0 * leaders::GUTTER_FRACTION))
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

/// A stick drawn as its travel: where its four directions' circles are on
/// the drawing, the ring through them, and its gate's shape.
struct Ring {
    stick: usize,
    cx: f32,
    cy: f32,
    radius: f32,
    octagon: bool,
}

/// The sticks a console's drawing shows as rings, and the controls they
/// stand in for. A stick is one thing, not four rails of text: its
/// directions are a ring with a dot in it, as the picker drew them. The
/// D-pad keeps its labels -- four switches read that way in the hand -- and
/// so does a group whose gate is `buttons` (an N64's C buttons). One circle
/// alone says nothing of where the middle is, so it takes two.
fn rings(
    console: &crate::consoles::Console,
    at: &dyn Fn((f32, f32)) -> (f32, f32),
    height: f32,
) -> (Vec<Ring>, Vec<usize>) {
    let mut rings = Vec::new();
    let mut taken = Vec::new();
    for (stick, name) in ["leftstick_", "rightstick_"].iter().enumerate() {
        let gate = console.gates[stick];
        if gate == "buttons" {
            continue;
        }
        let ways: Vec<(usize, (f32, f32))> = console
            .controls
            .iter()
            .enumerate()
            .filter(|(_, control)| control.id.starts_with(name))
            .filter_map(|(id, control)| Some((id, at(control.anchor?))))
            .collect();
        if ways.len() < 2 {
            continue;
        }
        let n = ways.len() as f32;
        let (cx, cy) = (
            ways.iter().map(|(_, (x, _))| x).sum::<f32>() / n,
            ways.iter().map(|(_, (_, y))| y).sum::<f32>() / n,
        );
        let reach = ways
            .iter()
            .map(|(_, (x, y))| (x - cx).hypot(y - cy))
            .fold(0.0, f32::max);
        rings.push(Ring {
            stick,
            cx,
            cy,
            // Big enough to see a dot move in: a stick's circles sit close
            // together on a drawing this size, and a ring through them was a
            // speck.
            radius: (reach + height * 0.03).max(height * 0.1),
            octagon: gate == "octagon",
        });
        taken.extend(ways.iter().map(|(id, _)| *id));
    }
    (rings, taken)
}

/// A stick's gate with a dot where the thumb has it: `at` is -1..1 each way,
/// y down. At rest and untouched, a small dim dot marks the middle, so the
/// ring is not an empty hoop whose meaning has to be guessed.
fn draw_ring(mesh: &mut Mesh, ring: &Ring, at: (f32, f32), ink: Colour, player: Colour, stroke: f32) {
    let r = ring.radius;
    if ring.octagon {
        // Flat side up, as a real gate sits: controllers.py's gate_points.
        let corner = |k: usize| {
            let turn = std::f32::consts::TAU * k as f32 / 8.0 + std::f32::consts::PI / 8.0;
            (ring.cx + r * turn.sin(), ring.cy - r * turn.cos())
        };
        for k in 0..8 {
            let ((x1, y1), (x2, y2)) = (corner(k), corner(k + 1));
            mesh.line(x1, y1, x2, y2, stroke, ink, FEATHER);
            mesh.disc(x1, y1, stroke / 2.0, ink, FEATHER);
        }
    } else {
        mesh.arc(ring.cx, ring.cy, r, stroke, 0.0, 1.0, ink, FEATHER);
    }
    let dot = (r * 0.25).max(3.0);
    if at == (0.0, 0.0) {
        mesh.disc(
            ring.cx,
            ring.cy,
            dot * 0.7,
            Colour::rgb(theme::TEXT_DIM, 0.8),
            FEATHER,
        );
    } else {
        let reach = r - dot;
        mesh.disc(
            ring.cx + at.0 * reach,
            ring.cy + at.1 * reach,
            dot,
            player,
            FEATHER,
        );
    }
}

/// A console's drawing's labels: every control it shows that is not part of
/// a stick drawn as a ring, down a rail on its side with a line to its
/// button -- the picker's controller screen. `lit` says which are lit, and
/// in what colour. The labels as placed, and the rings, for the caller to
/// draw its own way.
fn labelled(
    console: &crate::consoles::Console,
    layout: &RebindLayout,
    lit: &dyn Fn(usize) -> Option<Colour>,
    drawing: &mut Drawing,
) -> (Vec<leaders::Placed>, Vec<Ring>) {
    let (cx, cy, height, drawn_width) = (layout.cx, layout.cy, layout.height, layout.drawn_width);
    let at = |(u, v): (f32, f32)| {
        (
            cx - drawn_width / 2.0 + u * drawn_width,
            cy - height / 2.0 + v * height,
        )
    };
    let (sticks, taken) = rings(console, &at, height);
    let anchors: Vec<leaders::Anchor> = console
        .controls
        .iter()
        .enumerate()
        .filter(|(id, _)| !taken.contains(id))
        .filter_map(|(id, control)| {
            let (x, y) = at(control.anchor?);
            Some(leaders::Anchor { id, x, y })
        })
        .collect();
    let label_height = layout.label_size * 1.3;
    let diagram = (cx - drawn_width / 2.0, cy - height / 2.0, drawn_width, height);
    let pinned = |id: usize| pinned_side(console.controls[id].id);
    let placed = leaders::place(&anchors, diagram, label_height, &pinned);
    for placed in &placed {
        let control = console.controls[placed.anchor.id];
        let lit = lit(placed.anchor.id);
        let ink = lit.unwrap_or(Colour::rgb(theme::TEXT_DIM, 0.9));
        drawing.lines.line(
            placed.from.0,
            placed.from.1,
            placed.to.0,
            placed.to.1,
            if lit.is_some() { 2.5 } else { 1.25 },
            ink.with_alpha(if lit.is_some() { 1.0 } else { 0.55 }),
            FEATHER,
        );
        // A dot on the button, where the line starts: the picker's.
        drawing.lines.disc(
            placed.from.0,
            placed.from.1,
            (layout.label_size * 0.14).max(2.0),
            ink.with_alpha(if lit.is_some() { 1.0 } else { 0.8 }),
            FEATHER,
        );
        drawing.labels.push(Label {
            text: control.label.to_owned(),
            x: label_x(placed),
            y: placed.to.1,
            size: layout.label_size,
            colour: lit.unwrap_or(Colour::rgb(theme::TEXT_DIM, 1.0)),
            align: if placed.side == leaders::Side::Left {
                Align::Right
            } else {
                Align::Left
            },
        });
    }
    (placed, sticks)
}

/// Where a placed label's text is anchored: just off the end of its leader.
fn label_x(placed: &leaders::Placed) -> f32 {
    placed.to.0
        + if placed.side == leaders::Side::Left {
            -6.0
        } else {
            6.0
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
        let lit = |id: usize| {
            // Asked for, or under a thumb right now: both are this seat's.
            ((rebind.ended == 0 && usize::try_from(rebind.control).ok() == Some(id)) || pressed(rebind, id))
                .then_some(colour)
        };
        let (_, sticks) = labelled(console, &layout, &lit, drawing);
        for ring in &sticks {
            // Lit while one of its directions is asked for or pushed.
            let lit = console.controls.iter().enumerate().any(|(id, control)| {
                control.id.starts_with(["leftstick_", "rightstick_"][ring.stick])
                    && ((rebind.ended == 0 && usize::try_from(rebind.control).ok() == Some(id))
                        || pressed(rebind, id))
            });
            let ink = if lit {
                colour
            } else {
                Colour::rgb(theme::TEXT, 0.85)
            };
            let at = (rebind.sticks[ring.stick * 2], rebind.sticks[ring.stick * 2 + 1]);
            draw_ring(
                &mut drawing.lines,
                ring,
                at,
                ink,
                colour,
                if lit { 2.5 } else { 1.75 },
            );
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

/// What the bar says to a room with no controller in it.
pub const NOBODY: &str = "No controllers connected";
pub const NOBODY_HOW: &str = "Hold a button on a controller to join";

/// A game with nobody seated: said, in a card as wide as the words, until
/// somebody's hold starts -- which is the joining card taking its place.
fn build_nobody(scene: &Scene, top: f32, drawing: &mut Drawing) {
    let bar = scene.bar_height;
    let cx = scene.width as f32 / 2.0;
    let (big, small) = (bar * 0.3, bar * 0.19);
    let wide = text::width(NOBODY, big).max(text::width(NOBODY_HOW, small));
    card(
        &mut drawing.under,
        cx,
        top,
        (wide + bar).min(scene.width as f32),
        bar,
    );
    for (text, size, y, colour) in [
        (NOBODY, big, 0.4, theme::TEXT),
        (NOBODY_HOW, small, 0.72, theme::TEXT_DIM),
    ] {
        drawing.labels.push(Label {
            text: text.to_owned(),
            x: cx,
            y: top + bar * y,
            size,
            colour: Colour::rgb(colour, 1.0),
            align: Align::Centre,
        });
    }
}

/// What the bar says while a game stopped from the menu is saving.
pub const SAVING: &str = "Saving your game…";
/// And while a save picked from the menu is put back and the game started.
pub const LOADING: &str = "Loading your save…";

/// A line (and perhaps a second, dimmer one) in a card as wide as the words.
fn build_words(scene: &Scene, top: f32, first: &str, second: Option<&str>, drawing: &mut Drawing) {
    let bar = scene.bar_height;
    let cx = scene.width as f32 / 2.0;
    let (big, small) = (bar * 0.3, bar * 0.19);
    let wide = text::width(first, big).max(second.map_or(0.0, |s| text::width(s, small)));
    card(
        &mut drawing.under,
        cx,
        top,
        (wide + bar).min(scene.width as f32),
        bar,
    );
    let lines: &[(&str, f32, f32, [u8; 3])] = match second {
        Some(second) => &[
            (first, big, 0.4, theme::TEXT),
            (second, small, 0.72, theme::TEXT_DIM),
        ],
        None => &[(first, big, 0.5, theme::TEXT)],
    };
    for &(words, size, y, colour) in lines {
        drawing.labels.push(Label {
            text: words.to_owned(),
            x: cx,
            y: top + bar * y,
            size,
            colour: Colour::rgb(colour, 1.0),
            align: Align::Centre,
        });
    }
}

/// What the menu's footer says, by where the cursor is.
pub const MENU_KEYS: &str =
    "A  rebind      hold A  move      X  remove      Y  game on/off      hold B  close";
pub const TESTER_KEYS: &str = "A  try your buttons      hold B  close";
pub const TESTING_KEYS: &str = "hold Select  stop trying";
pub const EXIT_KEYS: &str = "hold A  exit      hold B  close";
pub const SAVES_ROW_KEYS: &str = "A  see your saves      hold B  close";
pub const BROWSE_KEYS: &str = "up / down  choose      A  load this one      B  back";
pub const LOAD_KEYS: &str = "hold A  load      B  back";

/// The saves row, and what its list says when it has no saves to show.
pub const LOAD_ROW: &str = "Load a save";
pub const LIST_LOADING: &str = "Looking for your saves…";
pub const LIST_FAILED: &str = "The saves could not be listed.";
pub const LIST_EMPTY: &str = "No saves yet: one is kept each time the game is closed.";
/// Under the prompt: what becomes of the game as it is now.
pub const KEPT: &str = "What you have now is kept, and can be loaded again from here.";
/// Saves shown at once; the list scrolls to keep the chosen one among them.
const SAVES_VISIBLE: usize = 5;

/// Where the menu's parts sit, as fractions of the panel.
const LINE_AT: f32 = 0.08;
const TESTER_BAND: (f32, f32) = (0.15, 0.82);
const EXIT_AT: f32 = 0.86;
/// With a saves row: the tester gives up the room for it, above Exit.
const TESTER_BAND_WITH_SAVES: (f32, f32) = (0.15, 0.74);
const SAVES_AT: f32 = 0.79;
const KEYS_AT: f32 = 0.94;

/// The menu: the controllers in a line along the top -- where one stands is
/// its player number, and its colour says the same -- the game's controller
/// under them for everybody to try their buttons on, and Exit under that.
fn build_menu(scene: &Scene, menu: &MenuFrame, top: f32, drawing: &mut Drawing) {
    let panel = scene.panel_height;
    let width = scene.width as f32;
    let cx = width / 2.0;
    let seats = (menu.rows as usize).min(menu.icons.len());
    let keys_size = panel * 0.034;
    let tester_row = seats;
    let saves_row = menu.saves.row.then_some(seats + 1);
    let exit_row = seats + 1 + usize::from(menu.saves.row);
    let browse = menu.saves.browse;
    let keys = if menu.testing {
        TESTING_KEYS
    } else if let Some(browse) = browse {
        if browse.confirming { LOAD_KEYS } else { BROWSE_KEYS }
    } else if menu.focus as usize == tester_row {
        TESTER_KEYS
    } else if Some(menu.focus as usize) == saves_row {
        SAVES_ROW_KEYS
    } else if menu.focus as usize == exit_row {
        EXIT_KEYS
    } else {
        MENU_KEYS
    };
    let band = if menu.saves.row {
        TESTER_BAND_WITH_SAVES
    } else {
        TESTER_BAND
    };
    let label_size = panel * 0.04;
    let marker = label_size * 1.3;
    let layout = labelled_layout(
        scene,
        menu.console,
        (top + panel * band.0, top + panel * band.1),
        marker * 2.4,
    );
    let slot = panel * 0.11;
    let card_w = layout
        .card_width
        .max(slot * seats as f32 + slot)
        .max(text::width(MENU_KEYS, keys_size) + keys_size * 5.0)
        .min(width * 0.96);
    card(&mut drawing.under, cx, top, card_w, panel);
    let dim = Colour::rgb(theme::TEXT_DIM, 1.0);
    let fill_ring = |over: &mut Mesh, x: f32, y: f32, radius: f32, fill: f32, ink: Colour| {
        over.arc(
            x,
            y,
            radius,
            radius * 0.3,
            0.0,
            1.0,
            ink.with_alpha(0.25),
            FEATHER,
        );
        over.arc(x, y, radius, radius * 0.3, 0.0, fill, ink, FEATHER);
    };
    build_line(menu, (cx, top + panel * LINE_AT), slot, drawing);
    if let Some(browse) = browse.filter(|b| !b.confirming) {
        // The list stands where the tester was, over the same band. Not under
        // the prompt: words are drawn over every card, so they would show
        // through it.
        let area = (top + panel * band.0, top + panel * band.1);
        build_saves_list(scene, menu, &browse, area, (cx, card_w), drawing);
    } else if menu.focus as usize == tester_row || menu.testing {
        // The test is the owner's while it runs: lit in their colour.
        let lit = if menu.testing {
            player_colour(menu.owner).with_alpha(0.14)
        } else {
            dim.with_alpha(0.1)
        };
        let (w, h) = (layout.card_width.min(card_w) - label_size, layout.height * 1.4);
        drawing.under.rect(cx - w / 2.0, layout.cy - h / 2.0, w, h, lit);
    }
    if browse.is_none()
        && let Some(console) = CONSOLES.get(menu.console as usize)
    {
        build_tester(menu, console, &layout, marker, drawing);
    }
    if saves_row.is_some() {
        let size = panel * 0.04;
        let y = top + panel * SAVES_AT;
        let on = Some(menu.focus as usize) == saves_row && !menu.testing && browse.is_none();
        if on {
            let (w, h) = (text::width(LOAD_ROW, size) + size * 4.0, size * 1.8);
            drawing
                .under
                .rect(cx - w / 2.0, y - h / 2.0, w, h, dim.with_alpha(0.18));
        }
        drawing.labels.push(Label {
            text: LOAD_ROW.to_owned(),
            x: cx,
            y,
            size,
            colour: if on { Colour::rgb(theme::TEXT, 1.0) } else { dim },
            align: Align::Centre,
        });
    }
    let on_exit = menu.focus as usize == exit_row && !menu.testing && browse.is_none();
    let exit_size = panel * 0.045;
    let exit_y = top + panel * EXIT_AT;
    let exit_w = text::width("Exit game", exit_size);
    if on_exit {
        let (w, h) = (exit_w + exit_size * 4.0, exit_size * 1.8);
        drawing
            .under
            .rect(cx - w / 2.0, exit_y - h / 2.0, w, h, RED.with_alpha(0.22));
    }
    drawing.labels.push(Label {
        text: "Exit game".to_owned(),
        x: cx,
        y: exit_y,
        size: exit_size,
        colour: if on_exit { RED } else { dim },
        align: Align::Centre,
    });
    if let Some(browse) = browse.filter(|b| b.confirming) {
        build_prompt(scene, menu, &browse, top, drawing);
    }
    if on_exit && menu.a_fill > 0.0 {
        let x = cx + exit_w / 2.0 + exit_size * 0.8;
        fill_ring(&mut drawing.over, x, exit_y, exit_size * 0.35, menu.a_fill, RED);
    }
    let keys_y = top + panel * KEYS_AT;
    drawing.labels.push(Label {
        text: keys.to_owned(),
        x: cx,
        y: keys_y,
        size: keys_size,
        colour: dim,
        align: Align::Centre,
    });
    if menu.b_fill > 0.0 {
        let x = cx + text::width(keys, keys_size) / 2.0 + keys_size * 1.2;
        fill_ring(&mut drawing.over, x, keys_y, keys_size * 0.55, menu.b_fill, dim);
    }
}

/// The saves, newest first, over `area` (top and bottom): a date as the
/// picker says it and a line on where it was made, the chosen one lit. Or a
/// line on why there are none to show.
fn build_saves_list(
    scene: &Scene,
    menu: &MenuFrame,
    browse: &Browse,
    area: (f32, f32),
    (cx, card_w): (f32, f32),
    drawing: &mut Drawing,
) {
    let panel = scene.panel_height;
    let dim = Colour::rgb(theme::TEXT_DIM, 1.0);
    let ink = Colour::rgb(theme::TEXT, 1.0);
    let heading = panel * 0.045;
    drawing.labels.push(Label {
        text: LOAD_ROW.to_owned(),
        x: cx,
        y: area.0 + heading * 0.6,
        size: heading,
        colour: ink,
        align: Align::Centre,
    });
    let said = match browse.listed {
        Listed::Loading => Some(LIST_LOADING),
        Listed::Failed => Some(LIST_FAILED),
        Listed::Ready(0) => Some(LIST_EMPTY),
        Listed::Ready(_) if scene.saves.is_empty() => Some(LIST_LOADING),
        Listed::Ready(_) => None,
    };
    if let Some(said) = said {
        drawing.labels.push(Label {
            text: said.to_owned(),
            x: cx,
            y: (area.0 + area.1) / 2.0,
            size: panel * 0.035,
            colour: dim,
            align: Align::Centre,
        });
        return;
    }
    let count = scene.saves.len();
    let selected = browse.selected.min(count - 1);
    let first = selected
        .saturating_sub(SAVES_VISIBLE / 2)
        .min(count.saturating_sub(SAVES_VISIBLE));
    let rows_top = area.0 + heading * 1.6;
    let row_h = (area.1 - rows_top) / SAVES_VISIBLE as f32;
    let (big, small) = (row_h * 0.36, row_h * 0.24);
    let row_w = (card_w * 0.8).min(scene.width as f32 * 0.9);
    let left = cx - row_w / 2.0;
    for (slot, line) in scene.saves.iter().enumerate().skip(first).take(SAVES_VISIBLE) {
        let y = rows_top + (slot - first) as f32 * row_h;
        let lit = slot == selected;
        if lit {
            let colour = player_colour(menu.owner).with_alpha(0.22);
            drawing
                .under
                .rect(left, y + row_h * 0.06, row_w, row_h * 0.88, colour);
        }
        drawing.labels.push(Label {
            text: line.when.clone(),
            x: left + big * 0.6,
            y: y + row_h * 0.36,
            size: big,
            colour: ink,
            align: Align::Left,
        });
        drawing.labels.push(Label {
            text: line.detail.clone(),
            x: left + big * 0.6,
            y: y + row_h * 0.72,
            size: small,
            colour: if lit { ink } else { dim },
            align: Align::Left,
        });
    }
}

/// Before a save is loaded, what loading it means: a card over the list,
/// and the ring of the hold that does it.
fn build_prompt(scene: &Scene, menu: &MenuFrame, browse: &Browse, top: f32, drawing: &mut Drawing) {
    let Some(line) = scene.saves.get(browse.selected) else {
        return;
    };
    let panel = scene.panel_height;
    let cx = scene.width as f32 / 2.0;
    let question = format!("Load the save from {}?", line.when);
    let (big, small) = (panel * 0.05, panel * 0.032);
    let wide = text::width(&question, big).max(text::width(KEPT, small)) + big * 2.0;
    let (w, h) = (wide.min(scene.width as f32 * 0.94), panel * 0.3);
    let y = top + panel * 0.33;
    // In the owner's colour, as the lit save was: the panel's own would not
    // stand out from the panel.
    let tint = player_colour(menu.owner);
    drawing.under.rect(cx - w / 2.0, y, w, h, tint.with_alpha(0.2));
    drawing.labels.push(Label {
        text: question.clone(),
        x: cx,
        y: y + h * 0.32,
        size: big,
        colour: Colour::rgb(theme::TEXT, 1.0),
        align: Align::Centre,
    });
    drawing.labels.push(Label {
        text: KEPT.to_owned(),
        x: cx,
        y: y + h * 0.62,
        size: small,
        colour: Colour::rgb(theme::TEXT_DIM, 1.0),
        align: Align::Centre,
    });
    let ink = player_colour(menu.owner);
    let (x, ring_y, radius) = (
        cx + text::width(&question, big) / 2.0 + big,
        y + h * 0.32,
        big * 0.35,
    );
    drawing.over.arc(
        x,
        ring_y,
        radius,
        radius * 0.3,
        0.0,
        1.0,
        ink.with_alpha(0.25),
        FEATHER,
    );
    if menu.a_fill > 0.0 {
        drawing
            .over
            .arc(x, ring_y, radius, radius * 0.3, 0.0, menu.a_fill, ink, FEATHER);
    }
}

/// The seats, small, in a line centred on `at`: where one stands is its
/// player number. The one under the cursor is lit, one being carried is
/// lifted and lit in its seat's colour, a gap before a later controller is
/// a faint ring, and a seat the game does not hear is dimmed.
fn build_line(menu: &MenuFrame, at: (f32, f32), slot: f32, drawing: &mut Drawing) {
    let seats = (menu.rows as usize).min(menu.icons.len());
    let dim = Colour::rgb(theme::TEXT_DIM, 1.0);
    let first = at.0 - slot * seats as f32 / 2.0;
    for seat_at in 0..seats {
        let seat = seat_at as i32 + 1;
        let x = first + (seat_at as f32 + 0.5) * slot;
        let focused = menu.focus as usize == seat_at && !menu.testing;
        let carried = menu.carried == seat;
        let y = at.1 - if carried { slot * 0.08 } else { 0.0 };
        if focused || carried {
            let lit = if carried {
                player_colour(seat).with_alpha(0.35)
            } else {
                dim.with_alpha(0.18)
            };
            let side = slot * 0.9;
            drawing
                .under
                .rect(x - side / 2.0, y - side / 2.0, side, side, lit);
        }
        let icon = menu.icons[seat_at];
        if icon == EMPTY_SEAT {
            let radius = slot * 0.08;
            drawing.over.arc(
                x,
                y,
                radius,
                radius * 0.25,
                0.0,
                1.0,
                dim.with_alpha(0.5),
                FEATHER,
            );
            continue;
        }
        let off = menu.off & (1 << seat_at) != 0;
        drawing.sprites.push(Sprite {
            icon,
            cx: x,
            cy: y,
            height: slot * 0.64,
            colour: player_colour(seat).with_alpha(if off { 0.35 } else { 1.0 }),
            revealed: 1.0,
            under: Colour::rgb(theme::EMPTY, 1.0),
            picture: false,
        });
        if off {
            drawing.labels.push(Label {
                text: "game off".to_owned(),
                x: x + slot * 0.5,
                y: y + slot * 0.3,
                size: slot * 0.16,
                colour: RED,
                align: Align::Right,
            });
        }
        if focused && menu.a_fill > 0.0 {
            let (rx, ry, radius) = (x + slot * 0.33, y - slot * 0.33, slot * 0.07);
            let ink = player_colour(seat);
            drawing.over.arc(
                rx,
                ry,
                radius,
                radius * 0.3,
                0.0,
                1.0,
                ink.with_alpha(0.25),
                FEATHER,
            );
            drawing
                .over
                .arc(rx, ry, radius, radius * 0.3, 0.0, menu.a_fill, ink, FEATHER);
        }
    }
}

/// The game's controller with its buttons named, for everybody to try:
/// beside the name of a button somebody has down, that person's own
/// controller, small and in their colour -- two on one button side by side,
/// outwards from the name. A stick is a ring with a dot per player who has
/// it off-centre, where they have it; nobody's, and a dim dot marks the
/// middle. A group bound as a stick that is buttons in the hand (an N64's C
/// buttons) is named and marked as buttons.
fn build_tester(
    menu: &MenuFrame,
    console: &crate::consoles::Console,
    layout: &RebindLayout,
    marker: f32,
    drawing: &mut Drawing,
) {
    drawing.sprites.push(Sprite {
        icon: u8::try_from(menu.console).unwrap_or(0),
        cx: layout.cx,
        cy: layout.cy,
        height: layout.height,
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
    let seats: Vec<usize> = (0..(menu.rows as usize).min(menu.icons.len()))
        .filter(|&at| menu.icons[at] != EMPTY_SEAT)
        .collect();
    let pressers = |id: usize| -> Vec<usize> {
        seats
            .iter()
            .copied()
            .filter(|&at| id < 64 && menu.pressed[at] & 1 << id != 0)
            .collect()
    };
    let lit = |id: usize| (!pressers(id).is_empty()).then_some(Colour::rgb(theme::TEXT, 1.0));
    let (placed, rings) = labelled(console, layout, &lit, drawing);
    for placed in &placed {
        let words = text::width(console.controls[placed.anchor.id].label, layout.label_size);
        let outward = if placed.side == leaders::Side::Left {
            -1.0
        } else {
            1.0
        };
        let edge = label_x(placed) + outward * words;
        for (nth, at) in pressers(placed.anchor.id).into_iter().enumerate() {
            drawing.sprites.push(Sprite {
                icon: menu.icons[at],
                cx: edge + outward * marker * (0.75 + 1.1 * nth as f32),
                cy: placed.to.1,
                height: marker,
                colour: player_colour(at as i32 + 1),
                revealed: 1.0,
                under: Colour::rgb(theme::EMPTY, 1.0),
                picture: false,
            });
        }
    }
    let stroke = 2.0;
    for ring in &rings {
        // Wider than the rebind's: several dots have to be told apart in it.
        let ring = &Ring {
            radius: ring.radius * 1.5,
            ..*ring
        };
        let ink = Colour::rgb(theme::TEXT, 0.85);
        let pushed: Vec<(usize, (f32, f32))> = seats
            .iter()
            .map(|&at| {
                (
                    at,
                    (
                        menu.sticks[at][ring.stick * 2],
                        menu.sticks[at][ring.stick * 2 + 1],
                    ),
                )
            })
            .filter(|(_, (x, y))| x.hypot(*y) > crate::pressing::STICK_DEAD)
            .collect();
        // The ring and the dim middle, drawn as a stick nobody has touched.
        draw_ring(&mut drawing.lines, ring, (0.0, 0.0), ink, ink, stroke);
        let dot = (ring.radius * 0.2).max(4.0);
        for (at, (x, y)) in pushed {
            let reach = ring.radius - dot;
            // A square gate's corner is past the circle: kept on the ring.
            let (x, y) = if x.hypot(y) > 1.0 {
                (x / x.hypot(y), y / x.hypot(y))
            } else {
                (x, y)
            };
            drawing.lines.disc(
                ring.cx + x * reach,
                ring.cy + y * reach,
                dot,
                player_colour(at as i32 + 1),
                FEATHER,
            );
        }
    }
}

/// Whether control `id` of the console is down on the seat being rebound.
fn pressed(rebind: &Rebinding, id: usize) -> bool {
    id < 64 && rebind.pressed & (1 << id) != 0
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
    // The menu is the same panel, for the player who asked for it.
    if let Some(menu) = scene.menu.filter(|_| scene.exit_progress <= 0.0) {
        let top = -scene.panel_height * (1.0 - scene.position as f32);
        build_menu(scene, &menu, top, drawing);
        return;
    }
    let bar = scene.bar_height;
    let width = scene.width as f32;
    let top = -bar * (1.0 - scene.position as f32);
    let count = scene.joined.len() + scene.fractions.len();
    if scene.saying == Saying::Saving && scene.exit_progress <= 0.0 {
        build_words(scene, top, SAVING, None, drawing);
        return;
    }
    if scene.saying == Saying::Loading && scene.exit_progress <= 0.0 {
        build_words(scene, top, LOADING, None, drawing);
        return;
    }
    if scene.nobody && count == 0 && scene.exit_progress <= 0.0 {
        build_nobody(scene, top, drawing);
        return;
    }
    let mesh = &mut drawing.under;
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
                pressed: 0,
                sticks: [0.0; 4],
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
        let nes = crate::consoles::for_platform("nes");
        let trigger = CONSOLES[nes]
            .control("lefttrigger")
            .expect("danstick's NES layout has one");
        for control in [trigger as i32, -1] {
            let mut drawing = Drawing::default();
            let scene = rebinding(control, 0);
            let scene = Scene {
                rebind: scene.rebind.map(|r| Rebinding {
                    console: nes as u32,
                    ..r
                }),
                ..scene
            };
            build(&scene, &mut drawing);
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
        for platform in ["n64", "gamecube", "switch", "snes", "no-such-platform"] {
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
                        pressed: 0,
                        sticks: [0.0; 4],
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
    fn a_pressed_button_lights_its_label_in_the_seats_colour() {
        let (console, a) = n64();
        let b = CONSOLES[console as usize].control("b").expect("an N64 has B");
        let label_of = |drawing: &Drawing, text: &str| {
            drawing
                .labels
                .iter()
                .find(|label| label.text == text)
                .map(|label| label.colour)
                .unwrap_or_else(|| panic!("no {text} label"))
        };
        let mut scene = rebinding(a as i32, 0);
        let mut drawing = Drawing::default();
        build(&scene, &mut drawing);
        assert_ne!(label_of(&drawing, "B"), player_colour(2), "B is not pressed");
        if let Some(rebind) = &mut scene.rebind {
            rebind.pressed = 1 << b;
        }
        build(&scene, &mut drawing);
        assert_eq!(label_of(&drawing, "B"), player_colour(2), "B, pressed, is lit");
        assert_eq!(
            label_of(&drawing, "A"),
            player_colour(2),
            "and A is still asked for"
        );
    }

    #[test]
    fn a_stick_is_a_ring_not_four_labels_and_its_dot_follows_the_thumb() {
        let gamecube = crate::consoles::for_platform("gamecube");
        let scene = |sticks: [f32; 4]| Scene {
            rebind: Some(Rebinding {
                console: gamecube as u32,
                control: -1,
                sticks,
                ..rebinding(0, 0).rebind.expect("a rebind")
            }),
            ..rebinding(0, 0)
        };
        let mut drawing = Drawing::default();
        build(&scene([0.0; 4]), &mut drawing);
        assert!(
            !drawing.labels.iter().any(|label| label.text.contains("stick")),
            "no stick direction has a label of its own: {:?}",
            drawing.labels.iter().map(|l| &l.text).collect::<Vec<_>>()
        );
        let ring = Ring {
            stick: 0,
            cx: 100.0,
            cy: 100.0,
            radius: 20.0,
            octagon: true,
        };
        let seat = player_colour(2);
        let dot_at = |at: (f32, f32)| {
            let mut mesh = Mesh::default();
            draw_ring(&mut mesh, &ring, at, Colour::rgb(theme::TEXT_DIM, 0.8), seat, 2.0);
            let dot: Vec<_> = mesh.vertices.iter().filter(|p| p.colour == seat).collect();
            let n = dot.len().max(1) as f32;
            (
                dot.iter().map(|p| p.x).sum::<f32>() / n,
                dot.iter().map(|p| p.y).sum::<f32>() / n,
                dot.len(),
            )
        };
        assert_eq!(dot_at((0.0, 0.0)).2, 0, "at rest, no seat's dot: the dim middle");
        let (x, y, _) = dot_at((1.0, 0.0));
        assert!(
            near(x, 115.0, 1.0) && near(y, 100.0, 1.0),
            "pushed right: ({x}, {y})"
        );
        let (x, y, _) = dot_at((0.0, -1.0));
        assert!(near(x, 100.0, 1.0) && near(y, 85.0, 1.0), "pushed up: ({x}, {y})");
    }

    #[test]
    fn an_n64s_c_buttons_stay_four_labels_and_its_stick_is_an_octagon() {
        let n64 = &CONSOLES[n64().0 as usize];
        assert_eq!(n64.gates, ["octagon", "buttons"]);
        let mut drawing = Drawing::default();
        build(&rebinding(-1, 0), &mut drawing);
        let texts: Vec<&str> = drawing.labels.iter().map(|l| l.text.as_str()).collect();
        assert!(texts.contains(&"C-up"), "{texts:?}");
        assert!(!texts.iter().any(|t| t.starts_with("Control stick")), "{texts:?}");
    }

    #[test]
    fn a_room_with_nobody_seated_is_told_so_until_somebody_joins() {
        let said = |scene: &Scene| {
            let mut drawing = Drawing::default();
            build(scene, &mut drawing);
            drawing.labels.iter().any(|label| label.text == NOBODY)
        };
        let nobody = Scene {
            nobody: true,
            ..down(0.0)
        };
        assert!(said(&nobody));
        let joining = Scene {
            fractions: &[0.4],
            players: &[1],
            hold_icons: &[0],
            ..nobody.clone()
        };
        assert!(!said(&joining), "a hold is the joining card instead");
        assert!(!said(&Scene { ..down(0.5) }), "the exit outranks it");
        assert!(!said(&Scene {
            nobody: false,
            ..down(0.0)
        }));
    }

    fn menu(focus: u32, a_fill: f32) -> Scene<'static> {
        Scene {
            panel_height: panel_height(800),
            menu: Some(MenuFrame {
                owner: 1,
                rows: 3,
                icons: [
                    2, EMPTY_SEAT, 5, EMPTY_SEAT, EMPTY_SEAT, EMPTY_SEAT, EMPTY_SEAT, EMPTY_SEAT,
                ],
                focus,
                carried: 0,
                a_fill,
                b_fill: 0.0,
                off: 0b100,
                console: 0,
                pressed: [0; crate::frame::ROWS_MAX],
                sticks: [[0.0; 4]; crate::frame::ROWS_MAX],
                testing: false,
                saves: crate::frame::Saves::default(),
            }),
            ..down(0.0)
        }
    }

    #[test]
    fn the_menu_is_a_line_of_controllers_in_player_order_and_exit() {
        let mut drawing = Drawing::default();
        build(&menu(0, 0.0), &mut drawing);
        let texts: Vec<&str> = drawing.labels.iter().map(|l| l.text.as_str()).collect();
        for want in ["game off", "Exit game", MENU_KEYS, "A (bottom face)"] {
            assert!(texts.contains(&want), "{want} missing from {texts:?}");
        }
        assert!(
            !texts.iter().any(|t| t.starts_with("Player")),
            "no \"Player N\" to read"
        );
        let pads: Vec<&Sprite> = drawing.sprites.iter().filter(|s| !s.picture).collect();
        assert_eq!(pads.len(), 2, "two seated pads, two drawings");
        assert!(
            drawing.sprites.iter().any(|s| s.picture),
            "and the game's controller"
        );
        let xs: Vec<f32> = pads.iter().map(|s| s.cx).collect();
        assert!(xs[0] < xs[1], "player one left of player three");
        assert!((pads[0].cy - pads[1].cy).abs() < 0.01, "one line");
        let gap = xs[1] - xs[0];
        let slot = gap / 2.0;
        assert!(slot > 0.0, "the empty second seat keeps its place between them");
        let off = drawing.labels.iter().find(|l| l.text == "game off").expect("off");
        assert!(
            (off.x - xs[1]).abs() < slot / 2.0 + 0.01,
            "under player three, whose port is off"
        );
    }

    fn lines() -> Vec<SaveLine> {
        ["today, 21:10", "yesterday, 08:02", "Sep 20, 10:00"]
            .iter()
            .enumerate()
            .map(|(at, when)| SaveLine {
                id: format!("remote:{}", 3 - at),
                when: (*when).to_owned(),
                detail: format!("daniel-deck · saved to the service {at}"),
            })
            .collect()
    }

    fn with_saves(focus: u32, browse: Option<Browse>, lines: &[SaveLine]) -> Scene<'_> {
        let mut scene = menu(focus, 0.0);
        scene.saves = lines;
        if let Some(frame) = scene.menu.as_mut() {
            frame.saves = crate::frame::Saves { row: true, browse };
        }
        scene
    }

    fn texts(scene: &Scene) -> Vec<String> {
        let mut drawing = Drawing::default();
        build(scene, &mut drawing);
        drawing.labels.iter().map(|l| l.text.clone()).collect()
    }

    #[test]
    fn a_game_with_its_own_saves_has_a_row_to_load_one() {
        // Three seats: the tester is 3, the saves 4, Exit 5.
        let said = texts(&with_saves(4, None, &[]));
        assert!(said.iter().any(|t| t == LOAD_ROW), "{said:?}");
        assert!(said.iter().any(|t| t == SAVES_ROW_KEYS), "{said:?}");
        assert!(said.iter().any(|t| t == "Exit game"));
        assert!(
            !texts(&menu(0, 0.0)).iter().any(|t| t == LOAD_ROW),
            "and none without"
        );
    }

    #[test]
    fn the_saves_list_says_when_and_where_each_was_made() {
        let lines = lines();
        let browse = Browse {
            listed: Listed::Ready(3),
            selected: 1,
            confirming: false,
        };
        let said = texts(&with_saves(4, Some(browse), &lines));
        for line in &lines {
            assert!(said.contains(&line.when), "{} missing from {said:?}", line.when);
            assert!(said.contains(&line.detail));
        }
        assert!(said.iter().any(|t| t == BROWSE_KEYS));
        assert!(
            !said.iter().any(|t| t == "A (bottom face)"),
            "the list stands where the tester was"
        );
    }

    #[test]
    fn the_prompt_names_the_save_and_what_happens_to_the_one_here() {
        let lines = lines();
        let browse = Browse {
            listed: Listed::Ready(3),
            selected: 2,
            confirming: true,
        };
        let mut scene = with_saves(4, Some(browse), &lines);
        scene.menu.as_mut().expect("a menu").a_fill = 0.5;
        let mut drawing = Drawing::default();
        build(&scene, &mut drawing);
        let said: Vec<&str> = drawing.labels.iter().map(|l| l.text.as_str()).collect();
        assert!(said.contains(&"Load the save from Sep 20, 10:00?"), "{said:?}");
        assert!(said.contains(&KEPT));
        assert!(said.contains(&LOAD_KEYS));
        assert!(!drawing.over.vertices.is_empty(), "the hold's ring fills");
    }

    #[test]
    fn a_list_still_coming_failed_or_empty_says_so() {
        for (listed, want) in [
            (Listed::Loading, LIST_LOADING),
            (Listed::Failed, LIST_FAILED),
            (Listed::Ready(0), LIST_EMPTY),
        ] {
            let browse = Browse {
                listed,
                selected: 0,
                confirming: false,
            };
            let said = texts(&with_saves(4, Some(browse), &[]));
            assert!(said.iter().any(|t| t == want), "{want} missing from {said:?}");
        }
    }

    #[test]
    fn the_bar_says_a_save_is_loading() {
        let scene = Scene {
            saying: Saying::Loading,
            ..down(0.0)
        };
        let mut drawing = Drawing::default();
        build(&scene, &mut drawing);
        assert!(drawing.labels.iter().any(|l| l.text == LOADING));
    }

    #[test]
    fn a_press_is_marked_by_the_pressers_controller_beside_the_button() {
        let (console, _) = n64();
        let a = CONSOLES[console as usize].control("a").expect("an N64 has A");
        let mut scene = menu(0, 0.0);
        let frame = scene.menu.as_mut().expect("menu");
        frame.console = console;
        // Players one and three both on A; nobody else pressing anything.
        frame.pressed[0] = 1 << a;
        frame.pressed[2] = 1 << a;
        let mut drawing = Drawing::default();
        build(&scene, &mut drawing);
        let marks = marks(&drawing);
        assert_eq!(marks.len(), 2, "one mark per presser");
        assert_eq!(
            (marks[0].icon, marks[1].icon),
            (2, 5),
            "each their own controller"
        );
        assert_eq!(marks[0].colour, player_colour(1));
        assert_eq!(marks[1].colour, player_colour(3));
        let label = drawing.labels.iter().find(|l| l.text == "A").expect("A is named");
        assert_eq!(label.colour, Colour::rgb(theme::TEXT, 1.0), "and lit");
        assert!(
            (marks[0].cy - label.y).abs() < 0.01 && (marks[1].cy - label.y).abs() < 0.01,
            "beside its name"
        );
        let (near, far) = ((marks[0].cx - label.x).abs(), (marks[1].cx - label.x).abs());
        assert!(near < far, "the second outwards from the first");
        // Nobody pressing: nothing beside any button.
        build(&menu(0, 0.0), &mut drawing);
        assert_eq!(drawing.sprites.iter().filter(|s| !s.picture).count(), 2);
    }

    /// The controllers drawn beside a button: every small pad off the line.
    fn marks(drawing: &Drawing) -> Vec<&Sprite> {
        let line_y = drawing.sprites.iter().find(|s| !s.picture).expect("the line").cy;
        drawing
            .sprites
            .iter()
            .filter(|s| !s.picture && (s.cy - line_y).abs() > 1.0)
            .collect()
    }

    #[test]
    fn a_trigger_danstick_says_the_owner_pulled_is_marked_beside_its_name() {
        use crate::events::Event;
        let console = crate::consoles::for_platform("generic");
        let generic = &CONSOLES[console];
        let mut focused = crate::menu::Focused::default();
        for (control, down) in [("lefttrigger", true), ("righttrigger", true)] {
            focused.apply(&Event::Focus {
                player: 1,
                control: control.into(),
                down,
            });
        }
        let held = focused.held(1);
        let mut scene = menu(0, 0.0);
        let frame = scene.menu.as_mut().expect("menu");
        frame.console = console as u32;
        frame.pressed[0] = held.bits(generic.controls.iter().map(|control| control.id));
        let mut drawing = Drawing::default();
        build(&scene, &mut drawing);
        let marks = marks(&drawing);
        for name in ["Left trigger", "Right trigger"] {
            let label = drawing.labels.iter().find(|l| l.text == name).expect(name);
            assert!(
                marks.iter().any(|m| (m.cy - label.y).abs() < 0.01),
                "{name} has no mark: {marks:?}"
            );
        }
    }

    #[test]
    fn an_n64s_c_buttons_are_buttons_and_its_stick_a_dot() {
        let (console, _) = n64();
        let n64 = &CONSOLES[console as usize];
        let c_up = n64.control("rightstick_up").expect("C-up is the right stick's");
        let mut scene = menu(0, 0.0);
        let frame = scene.menu.as_mut().expect("menu");
        frame.console = console;
        frame.pressed[0] = 1 << c_up;
        let mut drawing = Drawing::default();
        build(&scene, &mut drawing);
        let label = drawing
            .labels
            .iter()
            .find(|l| l.text == n64.controls[c_up].label)
            .expect("C-up is named, not drawn as a ring");
        let marks = marks(&drawing);
        assert_eq!(marks.len(), 1);
        assert!((marks[0].cy - label.y).abs() < 0.01, "marked beside its name");
        // The control stick is a ring: its halves are not named, and moving
        // it draws a dot in the mover's colour.
        assert!(
            !drawing
                .labels
                .iter()
                .any(|l| l.text == n64.controls[n64.control("leftstick_up").expect("stick")].label)
        );
        let still = drawing.lines.vertices.len();
        scene.menu.as_mut().expect("menu").sticks[2] = [0.8, -0.3, 0.0, 0.0];
        build(&scene, &mut drawing);
        assert!(drawing.lines.vertices.len() > still, "player three's dot");
        let wanted = player_colour(3);
        assert!(
            drawing.lines.vertices.iter().any(|v| v.colour == wanted),
            "in player three's colour"
        );
    }

    #[test]
    fn exit_under_the_cursor_is_red_and_its_hold_fills() {
        let mut drawing = Drawing::default();
        build(&menu(4, 0.5), &mut drawing);
        let exit = drawing
            .labels
            .iter()
            .find(|l| l.text == "Exit game")
            .expect("exit");
        assert_eq!(exit.colour, RED);
        let held = drawing.over.vertices.len();
        build(&menu(4, 0.0), &mut drawing);
        assert!(drawing.over.vertices.len() < held, "no hold, no ring");
    }

    #[test]
    fn a_game_saving_on_its_way_out_says_so() {
        let mut drawing = Drawing::default();
        build(
            &Scene {
                saying: Saying::Saving,
                ..down(0.0)
            },
            &mut drawing,
        );
        assert!(drawing.labels.iter().any(|l| l.text == SAVING));
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
