//! Where a controller's labels go, and the straight line from each to its
//! button: the picker's gotg_ui/leaders.py, ported so the overlay's rebind
//! panel lays a controller out the way the picker's screen did.
//!
//! Labels sit on a rail down each side of the drawing, split by which half
//! their button is in (the shoulders pinned, since they sit near the middle),
//! stacked in reading order, pushed apart without reordering, then untangled
//! by swapping: two crossing straight leaders are always strictly shorter
//! swapped (the triangle inequality), so swapping cannot loop and ends with
//! no two crossing. leaders.py carries the longer argument and the numbers
//! that chose this over sorting alone and over orthogonal leaders.

/// How far a rail sits outside the drawing, as a fraction of its width.
pub const GUTTER_FRACTION: f32 = 0.07;

/// A guard against a geometry bug, not an expected limit: every swap
/// shortens the layout, so the loop ends on its own.
const MAX_UNTANGLE_PASSES: usize = 200;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Side {
    Left,
    Right,
}

/// One button: an index back to whatever it stands for, and where it is.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Anchor {
    pub id: usize,
    pub x: f32,
    pub y: f32,
}

/// One label, placed: which side, the rail's x, its top, and its leader.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Placed {
    pub anchor: Anchor,
    pub side: Side,
    pub x: f32,
    pub y: f32,
    pub from: (f32, f32),
    pub to: (f32, f32),
}

/// Push overlapping labels apart, keeping their order: settle downwards,
/// borrow the slack at the top, close the gaps, and at last overlap evenly
/// rather than run out of the band -- a cramped label is readable, one drawn
/// off the screen is not.
pub fn spread(desired: &[f32], height: f32, top: f32, bottom: f32) -> Vec<f32> {
    if desired.is_empty() {
        return Vec::new();
    }
    let mut placed = desired.to_vec();
    for index in 1..placed.len() {
        let overlap = (placed[index - 1] + height) - placed[index];
        if overlap > 0.0 {
            placed[index] += overlap;
        }
    }
    let last = |p: &[f32]| p[p.len() - 1];
    let mut overflow = (last(&placed) + height) - bottom;
    if overflow > 0.0 {
        let shift = overflow.min(placed[0] - top);
        if shift > 0.0 {
            placed.iter_mut().for_each(|y| *y -= shift);
            overflow -= shift;
        }
    }
    if overflow > 0.0 {
        let total_gap: f32 = (1..placed.len())
            .map(|i| placed[i] - (placed[i - 1] + height))
            .sum();
        if total_gap > 0.0 {
            let keep = (1.0 - overflow / total_gap).max(0.0);
            let mut tightened = vec![placed[0]];
            for index in 1..placed.len() {
                let gap = placed[index] - (placed[index - 1] + height);
                let previous = tightened[tightened.len() - 1];
                tightened.push(previous + height + gap * keep);
            }
            placed = tightened;
            overflow = (last(&placed) + height) - bottom;
        }
    }
    if overflow > 0.0 && placed.len() > 1 {
        let step = (bottom - top - height) / (placed.len() - 1) as f32;
        placed = (0..placed.len()).map(|i| top + i as f32 * step).collect();
    } else if overflow > 0.0 {
        placed = vec![top];
    }
    placed
}

/// Every label laid out around `diagram` -- (x, y, width, height) where the
/// drawing is -- with its leader. `pinned` names the anchors that go on a
/// side whatever half they are in.
pub fn place(
    anchors: &[Anchor],
    diagram: (f32, f32, f32, f32),
    label_height: f32,
    pinned: &dyn Fn(usize) -> Option<Side>,
) -> Vec<Placed> {
    let (left, top, width, height) = diagram;
    let right = left + width;
    let centre_x = left + width / 2.0;
    let gutter = width * GUTTER_FRACTION;
    let band_top = top - height * 0.18;
    let band_bottom = top + height * 1.18;
    let side_of = |anchor: &Anchor| {
        pinned(anchor.id).unwrap_or(if anchor.x < centre_x {
            Side::Left
        } else {
            Side::Right
        })
    };
    let mut out = Vec::new();
    for side in [Side::Left, Side::Right] {
        let mut mine: Vec<Anchor> = anchors.iter().copied().filter(|a| side_of(a) == side).collect();
        if mine.is_empty() {
            continue;
        }
        mine.sort_by(|a, b| a.y.total_cmp(&b.y).then(a.x.total_cmp(&b.x)));
        let wanted: Vec<f32> = mine.iter().map(|a| a.y - label_height / 2.0).collect();
        let slots = spread(&wanted, label_height, band_top, band_bottom);
        let rail_x = if side == Side::Left {
            left - gutter
        } else {
            right + gutter
        };
        for (anchor, label_y) in mine.iter().zip(untangle(&mine, &slots, rail_x, label_height)) {
            out.push(Placed {
                anchor: *anchor,
                side,
                x: rail_x,
                y: label_y,
                from: (anchor.x, anchor.y),
                to: (rail_x, label_y + label_height / 2.0),
            });
        }
    }
    out
}

/// Do two open segments meet anywhere but at an endpoint? Open, so two
/// leaders from one row read as running alongside, not crossing.
fn segments_cross(p1: (f32, f32), p2: (f32, f32), p3: (f32, f32), p4: (f32, f32)) -> bool {
    let (d1x, d1y) = (p2.0 - p1.0, p2.1 - p1.1);
    let (d2x, d2y) = (p4.0 - p3.0, p4.1 - p3.1);
    let denominator = d1x * d2y - d1y * d2x;
    if denominator == 0.0 {
        return false;
    }
    let t = ((p3.0 - p1.0) * d2y - (p3.1 - p1.1) * d2x) / denominator;
    let u = ((p3.0 - p1.0) * d1y - (p3.1 - p1.1) * d1x) / denominator;
    0.0 < t && t < 1.0 && 0.0 < u && u < 1.0
}

fn untangle(anchors: &[Anchor], slots: &[f32], rail_x: f32, label_height: f32) -> Vec<f32> {
    let mut assigned = slots.to_vec();
    for _ in 0..MAX_UNTANGLE_PASSES {
        let mut swapped = false;
        for i in 0..anchors.len() {
            for j in i + 1..anchors.len() {
                let a = (
                    (anchors[i].x, anchors[i].y),
                    (rail_x, assigned[i] + label_height / 2.0),
                );
                let b = (
                    (anchors[j].x, anchors[j].y),
                    (rail_x, assigned[j] + label_height / 2.0),
                );
                if segments_cross(a.0, a.1, b.0, b.1) {
                    assigned.swap(i, j);
                    swapped = true;
                }
            }
        }
        if !swapped {
            break;
        }
    }
    assigned
}

/// How many leaders cross; zero once untangled. A test asserts it, so it has
/// to be able to fail -- the tangled-layout test below makes sure it can.
pub fn crossings(placed: &[Placed]) -> usize {
    let mut total = 0;
    for (index, first) in placed.iter().enumerate() {
        for second in &placed[index + 1..] {
            if segments_cross(first.from, first.to, second.from, second.to) {
                total += 1;
            }
        }
    }
    total
}

#[cfg(test)]
mod tests {
    use super::*;

    const RECT: (f32, f32, f32, f32) = (400.0, 200.0, 480.0, 288.0);
    const NAMES: [&str; 12] = [
        "Up", "Down", "Left", "Right", "A", "B", "Y", "X", "L", "R", "Select", "Start",
    ];
    const UV: [(f32, f32); 12] = [
        (0.29, 0.42),
        (0.29, 0.61),
        (0.24, 0.51),
        (0.35, 0.51),
        (0.77, 0.51),
        (0.71, 0.61),
        (0.65, 0.51),
        (0.71, 0.42),
        (0.28, 0.21),
        (0.72, 0.21),
        (0.45, 0.43),
        (0.55, 0.43),
    ];

    fn pad(rect: (f32, f32, f32, f32)) -> Vec<Anchor> {
        let (left, top, width, height) = rect;
        UV.iter()
            .enumerate()
            .map(|(id, &(u, v))| Anchor {
                id,
                x: left + u * width,
                y: top + v * height,
            })
            .collect()
    }

    fn none(_: usize) -> Option<Side> {
        None
    }

    #[test]
    fn every_anchor_is_placed_exactly_once() {
        let mut ids: Vec<usize> = place(&pad(RECT), RECT, 20.0, &none)
            .iter()
            .map(|p| p.anchor.id)
            .collect();
        ids.sort_unstable();
        assert_eq!(ids, (0..NAMES.len()).collect::<Vec<_>>());
    }

    #[test]
    fn no_leader_crosses_at_any_label_size() {
        for height in [8.0, 16.0, 20.0, 24.0, 40.0, 64.0] {
            assert_eq!(crossings(&place(&pad(RECT), RECT, height, &none)), 0, "{height}");
        }
    }

    #[test]
    fn no_leader_crosses_at_any_window_size() {
        for (w, h) in [
            (1280.0, 800.0),
            (1920.0, 1080.0),
            (3840.0, 2160.0),
            (800.0, 480.0),
        ] {
            let rect = (w * 0.3, h * 0.25, w * 0.4, h * 0.4);
            assert_eq!(
                crossings(&place(&pad(rect), rect, h * 0.028, &none)),
                0,
                "{w}x{h}"
            );
        }
    }

    #[test]
    fn labels_on_one_side_never_overlap() {
        let height = 22.0;
        let placed = place(&pad(RECT), RECT, height, &none);
        for side in [Side::Left, Side::Right] {
            let mut ys: Vec<f32> = placed.iter().filter(|p| p.side == side).map(|p| p.y).collect();
            ys.sort_by(f32::total_cmp);
            assert!(ys.windows(2).all(|w| w[1] - w[0] >= height - 1e-3), "{ys:?}");
        }
    }

    #[test]
    fn the_shoulders_are_pinned_apart() {
        let pinned = |id: usize| match NAMES[id] {
            "L" => Some(Side::Left),
            "R" => Some(Side::Right),
            _ => None,
        };
        let placed = place(&pad(RECT), RECT, 20.0, &pinned);
        let side = |name: &str| placed.iter().find(|p| NAMES[p.anchor.id] == name).map(|p| p.side);
        assert_eq!((side("L"), side("R")), (Some(Side::Left), Some(Side::Right)));
    }

    #[test]
    fn a_leader_starts_on_its_button_and_ends_on_its_label() {
        for item in place(&pad(RECT), RECT, 20.0, &none) {
            assert_eq!(item.from, (item.anchor.x, item.anchor.y));
            assert_eq!(item.to, (item.x, item.y + 10.0));
        }
    }

    #[test]
    fn a_deliberately_tangled_layout_is_reported_as_tangled() {
        let (left, top, width, _) = RECT;
        let rail = left - width * 0.07;
        let tangled: Vec<Placed> = [top + 180.0, top + 120.0, top + 60.0, top]
            .iter()
            .enumerate()
            .map(|(i, &y)| {
                let anchor = Anchor {
                    id: i,
                    x: left + 40.0,
                    y: top + i as f32 * 60.0,
                };
                Placed {
                    anchor,
                    side: Side::Left,
                    x: rail,
                    y,
                    from: (anchor.x, anchor.y),
                    to: (rail, y),
                }
            })
            .collect();
        assert!(crossings(&tangled) > 0);
    }

    #[test]
    fn a_crowded_rail_still_fits_inside_its_band() {
        let rect = (100.0, 40.0, 200.0, 120.0);
        let anchors: Vec<Anchor> = (0..12)
            .map(|i| Anchor {
                id: i,
                x: rect.0 + 10.0,
                y: rect.1 + i as f32 * 3.0,
            })
            .collect();
        let (top, bottom) = (rect.1 - rect.3 * 0.18, rect.1 + rect.3 * 1.18);
        for p in place(&anchors, rect, 30.0, &none) {
            assert!(top - 1e-3 <= p.y && p.y + 30.0 <= bottom + 1e-3, "{p:?}");
        }
    }

    #[test]
    fn nothing_in_nothing_out() {
        assert!(place(&[], RECT, 20.0, &none).is_empty());
        assert!(spread(&[], 10.0, 0.0, 100.0).is_empty());
    }

    #[test]
    fn spread_keeps_its_order_and_leaves_what_fits_alone() {
        let out = spread(&[50.0, 52.0, 54.0, 56.0], 20.0, 0.0, 400.0);
        assert!(out.windows(2).all(|w| w[1] - w[0] >= 20.0 - 1e-4), "{out:?}");
        assert_eq!(
            spread(&[0.0, 40.0, 80.0], 20.0, -50.0, 200.0),
            vec![0.0, 40.0, 80.0]
        );
    }

    #[test]
    fn random_arrangements_all_come_out_untangled() {
        // Seeded xorshift: a failure is the same failure every run.
        let mut state: u64 = 7;
        let mut next = || {
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
            (state % 1_000_000) as f32 / 1_000_000.0
        };
        for _ in 0..300 {
            let count = 2 + (next() * 19.0) as usize;
            let anchors: Vec<Anchor> = (0..count)
                .map(|id| Anchor {
                    id,
                    x: RECT.0 + (0.05 + next() * 0.9) * RECT.2,
                    y: RECT.1 + (0.05 + next() * 0.9) * RECT.3,
                })
                .collect();
            let height = [8.0, 20.0, 48.0, 80.0, 120.0][(next() * 5.0) as usize % 5];
            assert_eq!(crossings(&place(&anchors, RECT, height, &none)), 0);
        }
    }

    #[test]
    fn buttons_stacked_on_one_spot_do_not_tangle() {
        let anchors: Vec<Anchor> = (0..10)
            .map(|id| Anchor {
                id,
                x: 500.0,
                y: 300.0,
            })
            .collect();
        assert_eq!(crossings(&place(&anchors, RECT, 24.0, &none)), 0);
    }
}
