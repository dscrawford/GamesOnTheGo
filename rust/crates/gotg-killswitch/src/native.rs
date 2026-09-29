//! The overlay's own controls: the chords, read off each pad as the pad is.
//!
//! Two layers of controls, and they must not be one. The game's are a walk
//! somebody chose (an N64 walk puts L and R on the triggers) and change with
//! every rebind. The overlay's -- L + R + A held for the menu, L + R + Start
//! held to stop the game -- are the pad's own and never move: L and R are the
//! bumpers, A the bottom face button, whatever the game has been told.
//!
//! The overlay cannot read the pads itself (danstick holds them), and the
//! clones carry the game's walk, so danstick reports the pad's own controls:
//! `native` opens a watch on every seated pad, leased to this connection, and
//! each change of a, b, start, back, the shoulders and the triggers is said
//! as a `native` event. Held for the length of the connection -- sent again on
//! each new one. A danstick that does not know it says so, and the chords fall
//! back to the clones as they were read before.

use std::collections::{BTreeMap, BTreeSet};

use serde_json::json;

use crate::events::Event;
use crate::killswitch::Input;

#[derive(Debug, Clone, Default)]
pub struct Native {
    /// Asked on this connection.
    sent: bool,
    /// danstick does not know `native`: not asked again.
    refused: bool,
    down: BTreeMap<i32, BTreeSet<String>>,
}

impl Native {
    /// The line that opens the watch, if it is due on this connection.
    pub fn wanted(&mut self) -> Option<String> {
        if self.sent || self.refused {
            return None;
        }
        self.sent = true;
        Some(json!({"cmd": "native"}).to_string())
    }

    /// The connection went: the watch went with it, and so did what it said.
    pub fn lost(&mut self) {
        self.sent = false;
        self.down.clear();
    }

    /// Whether the chords come from here rather than the clones.
    pub fn active(&self) -> bool {
        self.sent && !self.refused
    }

    pub fn apply(&mut self, event: &Event) {
        match event {
            Event::Native {
                player,
                control,
                down,
            } => {
                let held = self.down.entry(*player).or_default();
                if *down {
                    held.insert(control.clone());
                } else {
                    held.remove(control);
                }
            }
            // A seat that emptied holds nothing down.
            Event::State { seated, .. } => {
                self.down
                    .retain(|player, _| seated.iter().any(|seat| seat.player == *player));
            }
            Event::Error { message } if message == "unknown command \"native\"" => {
                self.refused = true;
                self.down.clear();
            }
            _ => {}
        }
    }

    /// The players danstick has said anything about.
    pub fn players(&self) -> impl Iterator<Item = i32> + '_ {
        self.down.keys().copied()
    }

    /// What `player` has down, as the pad's own controls.
    pub fn held(&self, player: i32) -> BTreeSet<String> {
        self.down.get(&player).cloned().unwrap_or_default()
    }

    /// `player`'s pad as the chords ask about it. A shoulder is the bumper or
    /// the trigger beside it, as the clones were read: some pads' shoulders
    /// are triggers.
    pub fn input(&self, player: i32) -> Input {
        let down = self.down.get(&player);
        let has = |name: &str| down.is_some_and(|held| held.contains(name));
        Input {
            left: has("leftshoulder") || has("lefttrigger"),
            right: has("rightshoulder") || has("righttrigger"),
            start: has("start"),
            back: has("back"),
            a: has("a"),
        }
    }
}

impl crate::events::Listen for Native {
    fn apply(&mut self, event: &crate::events::Event) {
        Native::apply(self, event);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::events::Seat;

    fn press(player: i32, control: &str, down: bool) -> Event {
        Event::Native {
            player,
            control: control.into(),
            down,
        }
    }

    #[test]
    fn the_watch_is_opened_once_a_connection_and_again_on_the_next() {
        let mut native = Native::default();
        assert_eq!(native.wanted(), Some(r#"{"cmd":"native"}"#.to_owned()));
        assert_eq!(native.wanted(), None);
        assert!(native.active());
        native.lost();
        assert!(!native.active(), "no connection, no watch");
        assert!(native.wanted().is_some());
    }

    #[test]
    fn a_danstick_without_it_is_not_asked_again_and_the_clones_are_read() {
        let mut native = Native::default();
        native.wanted();
        native.apply(&Event::Error {
            message: "unknown command \"native\"".into(),
        });
        assert!(!native.active());
        native.lost();
        assert_eq!(native.wanted(), None);
    }

    #[test]
    fn the_bumpers_and_a_are_the_menu_chord_whatever_the_game_was_told() {
        let mut native = Native::default();
        native.wanted();
        for control in ["leftshoulder", "rightshoulder", "a"] {
            native.apply(&press(1, control, true));
        }
        let input = native.input(1);
        assert!(input.left && input.right && input.a && !input.start);
        assert_eq!(native.input(2), Input::default(), "only the pad that pressed");
        native.apply(&press(1, "a", false));
        assert!(!native.input(1).a);
    }

    #[test]
    fn a_seat_that_emptied_holds_nothing_down() {
        let mut native = Native::default();
        native.apply(&press(2, "leftshoulder", true));
        native.apply(&Event::State {
            seated: vec![Seat {
                node: "e1".into(),
                name: String::new(),
                player: 1,
                mapped: true,
            }],
            listening: Some(true),
            status: "idle".into(),
            slots: 4,
            ports_off: vec![],
        });
        assert_eq!(native.input(2), Input::default());
    }
}
