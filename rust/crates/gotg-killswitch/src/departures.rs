//! A controller that goes away gives up its seat.
//!
//! danstick keeps a seat for a pad that disappears -- switched off, a flat
//! battery, out of Bluetooth range -- so that it walks back into the same one.
//! Over a game that read as a controller that had died and was still there:
//! its icon in the menu, its seat taken, and a menu it had opened left down
//! with nobody able to close it. So the seat and the pad go together: when
//! danstick says a seated pad was removed, the overlay asks for its seat to be
//! dropped (`unseat`), and a pad that comes back takes a seat the way any other
//! does, by being held.
//!
//! danstick says `removed` for an `unseat` too (`reason: "unseated"`); that one
//! is the seat already gone, and is not asked about again.

use std::collections::BTreeSet;

use serde_json::json;

use crate::events::Event;

#[derive(Debug, Clone, Default)]
pub struct Departures {
    /// Seats to drop, in the order their pads went.
    due: Vec<i32>,
    /// Seats whose pad has gone and not come back.
    gone: BTreeSet<i32>,
}

impl Departures {
    /// The next `unseat` to send, if a pad has gone.
    pub fn wanted(&mut self) -> Option<String> {
        if self.due.is_empty() {
            return None;
        }
        let player = self.due.remove(0);
        Some(json!({"cmd": "unseat", "player": player}).to_string())
    }

    /// Whether seat `player`'s pad has gone.
    pub fn gone(&self, player: i32) -> bool {
        self.gone.contains(&player)
    }
}

impl crate::events::Listen for Departures {
    fn apply(&mut self, event: &Event) {
        match event {
            Event::Controller {
                player,
                removed: true,
                unseated: false,
            } if *player > 0 => {
                self.gone.insert(*player);
                if !self.due.contains(player) {
                    self.due.push(*player);
                }
            }
            Event::Controller {
                player,
                removed: false,
                ..
            } => {
                self.gone.remove(player);
                self.due.retain(|due| due != player);
            }
            Event::State { seated, .. } => {
                let still_seated = |player: &i32| seated.iter().any(|seat| seat.player == *player);
                self.gone.retain(still_seated);
                self.due.retain(still_seated);
            }
            _ => {}
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::events::Listen;
    use crate::events::Seat;

    fn went(player: i32, unseated: bool) -> Event {
        Event::Controller {
            player,
            removed: true,
            unseated,
        }
    }

    fn state(players: &[i32]) -> Event {
        Event::State {
            seated: players
                .iter()
                .map(|&player| Seat {
                    node: format!("e{player}"),
                    name: String::new(),
                    player,
                    mapped: true,
                })
                .collect(),
            listening: Some(true),
            status: "idle".into(),
            slots: 4,
            ports_off: vec![],
        }
    }

    #[test]
    fn a_pad_that_goes_gives_up_its_seat() {
        let mut departures = Departures::default();
        departures.apply(&went(2, false));
        assert!(departures.gone(2));
        assert_eq!(
            departures.wanted(),
            Some(r#"{"cmd":"unseat","player":2}"#.to_owned())
        );
        assert_eq!(departures.wanted(), None, "asked once");
        departures.apply(&state(&[1]));
        assert!(!departures.gone(2), "the seat is gone with it");
    }

    #[test]
    fn a_seat_dropped_by_unseat_is_not_dropped_again() {
        let mut departures = Departures::default();
        departures.apply(&went(3, true));
        assert_eq!(departures.wanted(), None);
        assert!(!departures.gone(3));
    }

    #[test]
    fn a_pad_back_before_it_was_asked_about_keeps_its_seat() {
        let mut departures = Departures::default();
        departures.apply(&went(1, false));
        departures.apply(&Event::Controller {
            player: 1,
            removed: false,
            unseated: false,
        });
        assert_eq!(departures.wanted(), None);
        assert!(!departures.gone(1));
    }
}
