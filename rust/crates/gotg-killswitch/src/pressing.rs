//! What somebody at the rebind panel is pressing, as the console's controls.
//!
//! The picker's controller screen lit a label while its button was down and
//! put a dot in each stick's gate where the thumb had it (pressing.py). That
//! is how somebody checks a rebind took: press B, see B light. The overlay
//! has two places to hear it from, and which one depends on whether danstick's
//! wizard is running:
//!
//! - **Walking.** danstick holds the pad and holds back its clone, so SDL sees
//!   nothing. It sends `input` instead -- the pad's raw button, hat or axis --
//!   and every `mapping` step carries `captured`, what each control is bound
//!   to so far, in SDL's spelling (`b3`, `h0.4`, `-a1`). Read together they
//!   name the control, and the table is the run's own rather than a profile
//!   off disk, so what lights is what the walk has just set.
//! - **Not walking.** The clone is the player's pad again, and SDL maps it:
//!   a clone is an Xbox 360 pad whatever it mirrors, and danstick's control ids
//!   are SDL's element names, so a button down is a control down.

use std::collections::BTreeSet;

/// Past here an axis is pressed, not resting: pressing.py's AXIS_ON.
pub const AXIS_ON: f32 = 0.5;

/// Under this a stick is in the middle: a resting stick reads a percent or
/// two off centre, and a dot that never sits still looks like a fault.
pub const STICK_DEAD: f32 = 0.12;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Button,
    Hat,
    Axis,
}

impl Kind {
    pub fn named(text: &str) -> Option<Self> {
        match text {
            "button" => Some(Self::Button),
            "hat" => Some(Self::Hat),
            "axis" => Some(Self::Axis),
            _ => None,
        }
    }
}

/// One control's input, as a capture records it. `value` is the hat's bit or
/// the axis's sign; a button's is unused.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Binding {
    pub control: String,
    pub kind: Kind,
    pub index: i32,
    pub value: i32,
}

/// `b3`, `h0.4`, `+a2`, `-a2` (and a bare `a2`, positive): SDL's spelling,
/// which is how `mapping`'s `captured` says it. None for anything else.
pub fn spelled(control: &str, sdl: &str) -> Option<Binding> {
    let binding = |kind, index: &str, value| {
        Some(Binding {
            control: control.to_owned(),
            kind,
            index: index.parse().ok().filter(|&i: &i32| (0..256).contains(&i))?,
            value,
        })
    };
    if let Some(rest) = sdl.strip_prefix('b') {
        return binding(Kind::Button, rest, 0);
    }
    if let Some((index, bit)) = sdl.strip_prefix('h').and_then(|rest| rest.split_once('.')) {
        let bit = bit.parse().ok().filter(|b| [1, 2, 4, 8].contains(b))?;
        return binding(Kind::Hat, index, bit);
    }
    let (sign, rest) = match sdl.as_bytes().first() {
        Some(b'-') => (-1, &sdl[1..]),
        Some(b'+') => (1, &sdl[1..]),
        _ => (1, sdl),
    };
    binding(Kind::Axis, rest.strip_prefix('a')?, sign)
}

/// Which way on screen a stick direction points, and which stick: 0 left, 1
/// right. None for a control that is not a stick direction.
fn stick_way(control: &str) -> Option<(usize, f32, f32)> {
    let (stick, way) = control.split_once('_')?;
    let stick = match stick {
        "leftstick" => 0,
        "rightstick" => 1,
        _ => return None,
    };
    let (dx, dy) = match way {
        "left" => (-1.0, 0.0),
        "right" => (1.0, 0.0),
        "up" => (0.0, -1.0),
        "down" => (0.0, 1.0),
        _ => return None,
    };
    Some((stick, dx, dy))
}

/// What is down right now on one seat, and where its sticks are: x then y,
/// left stick then right, each -1..1 with y down as a screen counts.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Held {
    pub controls: BTreeSet<String>,
    pub sticks: [f32; 4],
}

impl Held {
    /// One raw input from the pad under the wizard, named through what the
    /// walk has captured. Everything else that input drives -- the other end
    /// of an axis, a hat let go -- comes up, as the picker's `controls_on` did.
    pub fn raw(&mut self, table: &[Binding], kind: Kind, index: i32, value: f32) {
        for binding in table.iter().filter(|b| b.kind == kind && b.index == index) {
            let down = match kind {
                Kind::Button => value >= 0.5,
                Kind::Hat => (value as i32) & binding.value != 0,
                Kind::Axis => value * binding.value.signum() as f32 >= AXIS_ON,
            };
            if down {
                self.controls.insert(binding.control.clone());
            } else {
                self.controls.remove(&binding.control);
            }
            if kind == Kind::Axis
                && let Some((stick, dx, dy)) = stick_way(&binding.control)
            {
                // The binding's end of the axis, turned into the direction it
                // stands for; the other end comes out the same, so either
                // binding places the dot.
                let reading = value * binding.value.signum() as f32;
                let reading = if reading.abs() < STICK_DEAD { 0.0 } else { reading };
                if dx != 0.0 {
                    self.sticks[stick * 2] = (dx * reading).clamp(-1.0, 1.0);
                } else {
                    self.sticks[stick * 2 + 1] = (dy * reading).clamp(-1.0, 1.0);
                }
            }
        }
    }

    /// A clone, as SDL maps it: `buttons` are SDL's element names that are
    /// down, `axes` its six in SDL's order (left x, left y, right x, right y,
    /// left trigger, right trigger), -1..1.
    pub fn standard(buttons: &[&str], axes: [f32; 6]) -> Self {
        let mut controls: BTreeSet<String> = buttons.iter().map(|&name| name.to_owned()).collect();
        let ends = [
            ("leftstick_left", "leftstick_right"),
            ("leftstick_up", "leftstick_down"),
            ("rightstick_left", "rightstick_right"),
            ("rightstick_up", "rightstick_down"),
        ];
        for (at, (low, high)) in ends.iter().enumerate() {
            if axes[at] <= -AXIS_ON {
                controls.insert((*low).to_owned());
            } else if axes[at] >= AXIS_ON {
                controls.insert((*high).to_owned());
            }
        }
        for (at, trigger) in [(4, "lefttrigger"), (5, "righttrigger")] {
            if axes[at] >= AXIS_ON {
                controls.insert(trigger.to_owned());
            }
        }
        let still = |value: f32| {
            if value.abs() < STICK_DEAD {
                0.0
            } else {
                value.clamp(-1.0, 1.0)
            }
        };
        Self {
            controls,
            sticks: [still(axes[0]), still(axes[1]), still(axes[2]), still(axes[3])],
        }
    }

    /// The controls down as bits of a console's list, in its order: what a
    /// frame carries. Past 64 controls nothing is said.
    pub fn bits(&self, ids: impl Iterator<Item = &'static str>) -> u64 {
        ids.take(64)
            .enumerate()
            .filter(|(_, id)| self.controls.contains(*id))
            .fold(0, |bits, (at, _)| bits | 1 << at)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn table(pairs: &[(&str, &str)]) -> Vec<Binding> {
        pairs
            .iter()
            .filter_map(|(control, sdl)| spelled(control, sdl))
            .collect()
    }

    #[test]
    fn sdl_spellings_are_read_and_nonsense_is_not() {
        let b = spelled("a", "b3").expect("a button");
        assert_eq!((b.kind, b.index), (Kind::Button, 3));
        let h = spelled("dpup", "h0.1").expect("a hat");
        assert_eq!((h.kind, h.index, h.value), (Kind::Hat, 0, 1));
        assert_eq!(
            spelled("x", "-a1").map(|b| (b.kind, b.index, b.value)),
            Some((Kind::Axis, 1, -1))
        );
        assert_eq!(spelled("x", "+a4").map(|b| b.value), Some(1));
        assert_eq!(spelled("x", "a4").map(|b| b.value), Some(1));
        for nonsense in ["", "b", "bx", "h0.3", "h0", "q2", "-b2", "b-1", "b9999"] {
            assert_eq!(spelled("x", nonsense), None, "{nonsense}");
        }
    }

    #[test]
    fn a_button_down_lights_its_control_and_up_puts_it_out() {
        let table = table(&[("a", "b0"), ("b", "b1")]);
        let mut held = Held::default();
        held.raw(&table, Kind::Button, 1, 1.0);
        assert_eq!(held.controls, BTreeSet::from(["b".to_owned()]));
        held.raw(&table, Kind::Button, 1, 0.0);
        assert!(held.controls.is_empty());
        held.raw(&table, Kind::Button, 7, 1.0);
        assert!(
            held.controls.is_empty(),
            "an input nothing is bound to lights nothing"
        );
    }

    #[test]
    fn a_hat_diagonal_is_two_directions_and_centred_is_none() {
        let table = table(&[("dpup", "h0.1"), ("dpright", "h0.2"), ("dpdown", "h0.4")]);
        let mut held = Held::default();
        held.raw(&table, Kind::Hat, 0, 3.0);
        assert_eq!(
            held.controls,
            BTreeSet::from(["dpup".to_owned(), "dpright".to_owned()])
        );
        held.raw(&table, Kind::Hat, 0, 0.0);
        assert!(held.controls.is_empty());
    }

    #[test]
    fn a_stick_lights_one_end_moves_the_dot_and_rests_in_the_middle() {
        let table = table(&[
            ("leftstick_left", "-a0"),
            ("leftstick_right", "+a0"),
            ("leftstick_up", "-a1"),
        ]);
        let mut held = Held::default();
        held.raw(&table, Kind::Axis, 0, -0.9);
        assert_eq!(held.controls, BTreeSet::from(["leftstick_left".to_owned()]));
        assert!((held.sticks[0] + 0.9).abs() < 1e-6, "{:?}", held.sticks);
        held.raw(&table, Kind::Axis, 0, 0.7);
        assert_eq!(held.controls, BTreeSet::from(["leftstick_right".to_owned()]));
        assert!((held.sticks[0] - 0.7).abs() < 1e-6);
        held.raw(&table, Kind::Axis, 1, -0.3);
        assert!((held.sticks[1] + 0.3).abs() < 1e-6, "up is up the screen");
        assert!(!held.controls.contains("leftstick_up"), "a little is not a press");
        held.raw(&table, Kind::Axis, 0, 0.05);
        assert_eq!(held.sticks[0], 0.0, "a resting stick is the middle");
        assert!(held.controls.is_empty());
    }

    #[test]
    fn a_stick_bound_backwards_still_places_the_dot_where_the_thumb_is() {
        // A pad whose y axis runs the other way: up is its positive end.
        let table = table(&[("rightstick_up", "+a4")]);
        let mut held = Held::default();
        held.raw(&table, Kind::Axis, 4, 0.8);
        assert!(held.controls.contains("rightstick_up"));
        assert!((held.sticks[3] + 0.8).abs() < 1e-6, "{:?}", held.sticks);
    }

    #[test]
    fn a_trigger_is_one_way_and_a_clone_names_its_own_buttons() {
        let held = Held::standard(&["a", "leftshoulder"], [0.0, -0.8, 0.6, 0.02, 0.9, 0.1]);
        let want = [
            "a",
            "leftshoulder",
            "leftstick_up",
            "rightstick_right",
            "lefttrigger",
        ];
        assert_eq!(held.controls, want.iter().map(|s| s.to_string()).collect());
        assert_eq!(held.sticks, [0.0, -0.8, 0.6, 0.0]);
    }

    #[test]
    fn what_is_down_crosses_as_bits_in_the_consoles_order() {
        let held = Held::standard(&["b", "start"], [0.0; 6]);
        assert_eq!(held.bits(["a", "b", "start"].into_iter()), 0b110);
        let many = (0..80).map(|_| "b");
        assert_eq!(
            held.bits(many),
            u64::MAX,
            "past 64 is not said, and does not overflow"
        );
    }
}
