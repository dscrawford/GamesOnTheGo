//! The cursor over the game's controller in the menu: on the whole drawing,
//! or on one of its controls, moved by the d-pad from one to the nearest in
//! that direction.

/// A control the drawing places, and where (0..1 of its width and height).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Target {
    pub control: usize,
    pub at: (f32, f32),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum Hover {
    #[default]
    Whole,
    Control(usize),
}

impl Hover {
    pub fn control(self) -> Option<usize> {
        match self {
            Hover::Whole => None,
            Hover::Control(at) => Some(at),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Way {
    Up,
    Down,
    Left,
    Right,
}

/// A control has to be at least this far along the way to count as being
/// in that direction at all, so a row of buttons is walked, not leapt.
const AHEAD: f32 = 0.02;
/// Sideways distance counts two and a half times, so straight ahead beats
/// nearer-but-beside: a d-pad's arms are a diagonal apart, and Right from
/// the left arm has to be the right arm, not the nearer up.
const ACROSS: f32 = 2.5;
/// How far aside a control may be for each unit ahead and still be "that
/// way": tan 60 degrees. An N64's L is 54 degrees off the d-pad's up, and
/// 45 would have left it unreachable.
const CONE: f32 = 1.75;

/// Where the cursor lands going `way` from `from`: the nearest target in
/// that direction, or the whole controller when there is none left.
pub fn step(targets: &[Target], from: Hover, way: Way) -> Hover {
    let origin = match from {
        Hover::Whole => (0.5, 0.5),
        Hover::Control(at) => match targets.iter().find(|t| t.control == at) {
            Some(target) => target.at,
            None => (0.5, 0.5),
        },
    };
    let (dx, dy) = match way {
        Way::Up => (0.0, -1.0),
        Way::Down => (0.0, 1.0),
        Way::Left => (-1.0, 0.0),
        Way::Right => (1.0, 0.0),
    };
    targets
        .iter()
        .filter(|t| Hover::Control(t.control) != from)
        .filter_map(|t| {
            let (ox, oy) = (t.at.0 - origin.0, t.at.1 - origin.1);
            let along = ox * dx + oy * dy;
            let across = (ox * dy - oy * dx).abs();
            (along > AHEAD && across <= along * CONE).then_some((along.hypot(ACROSS * across), t.control))
        })
        .min_by(|a, b| a.0.total_cmp(&b.0).then(a.1.cmp(&b.1)))
        .map_or(Hover::Whole, |(_, control)| Hover::Control(control))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn at(control: usize, x: f32, y: f32) -> Target {
        Target { control, at: (x, y) }
    }

    /// A pad's face: a d-pad cross on the left, A and B on the right.
    fn pad() -> Vec<Target> {
        let (a, b, dpup, dpdown, dpleft, dpright, start) = (0, 1, 2, 3, 4, 5, 6);
        vec![
            at(a, 0.8, 0.6),
            at(b, 0.9, 0.5),
            at(dpup, 0.2, 0.4),
            at(dpdown, 0.2, 0.6),
            at(dpleft, 0.1, 0.5),
            at(dpright, 0.3, 0.5),
            at(start, 0.5, 0.75),
        ]
    }

    #[test]
    fn from_the_whole_controller_right_goes_to_the_nearest_control_right_of_the_middle() {
        assert_eq!(
            step(&pad(), Hover::Whole, Way::Right),
            Hover::Control(0),
            "A, nearer than B"
        );
        assert_eq!(step(&pad(), Hover::Whole, Way::Left), Hover::Control(5));
        assert_eq!(
            step(&pad(), Hover::Whole, Way::Down),
            Hover::Control(6),
            "Start; A is too far aside"
        );
        let corner = vec![at(0, 0.5, 0.5), at(1, 0.9, 0.05)];
        assert_eq!(
            step(&corner, Hover::Control(0), Way::Up),
            Hover::Control(1),
            "well aside, still up"
        );
        let beside = vec![at(0, 0.5, 0.5), at(1, 0.9, 0.4)];
        assert_eq!(
            step(&beside, Hover::Control(0), Way::Up),
            Hover::Whole,
            "more beside than up"
        );
    }

    #[test]
    fn straight_ahead_is_preferred_to_closer_but_sideways() {
        // From dpleft, right: dpright is straight on; dpup and dpdown are
        // nearer by distance but beside the way.
        assert_eq!(step(&pad(), Hover::Control(4), Way::Right), Hover::Control(5));
        assert_eq!(step(&pad(), Hover::Control(5), Way::Right), Hover::Control(0));
        assert_eq!(
            step(&pad(), Hover::Control(0), Way::Right),
            Hover::Control(1),
            "diagonal, but the only one"
        );
        assert_eq!(
            step(&pad(), Hover::Control(5), Way::Up),
            Hover::Control(2),
            "the cross's diagonal counts"
        );
    }

    #[test]
    fn past_the_last_control_in_a_direction_is_the_whole_controller_again() {
        assert_eq!(step(&pad(), Hover::Control(1), Way::Right), Hover::Whole);
        assert_eq!(step(&pad(), Hover::Control(2), Way::Up), Hover::Whole);
        assert_eq!(step(&pad(), Hover::Control(4), Way::Left), Hover::Whole);
    }

    #[test]
    fn a_control_the_drawing_has_no_place_for_is_never_hovered() {
        // Only what is in the targets can be reached; a control nobody
        // placed (an N64's Z, under the pad) is not among them.
        let mut reached = std::collections::BTreeSet::new();
        let mut frontier = vec![Hover::Whole];
        while let Some(from) = frontier.pop() {
            for way in [Way::Up, Way::Down, Way::Left, Way::Right] {
                if let Hover::Control(c) = step(&pad(), from, way)
                    && reached.insert(c)
                {
                    frontier.push(Hover::Control(c));
                }
            }
        }
        assert_eq!(reached.len(), pad().len(), "{reached:?}");
    }

    #[test]
    fn two_controls_at_the_same_place_are_told_apart_by_order() {
        let twins = vec![at(3, 0.7, 0.5), at(2, 0.7, 0.5)];
        assert_eq!(step(&twins, Hover::Whole, Way::Right), Hover::Control(2));
    }
}
