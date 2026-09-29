//! Keeping danstick listening for somebody to join.
//!
//! A pad joins by holding a button while danstick's `seating` is open. The
//! picker used to keep it open (assign.Watch), and the launch gate before a
//! game; both are gone, and the overlay is the one thing on danstick's socket
//! in every session -- over the picker, over a game started from the picker,
//! from Steam or from a terminal. So the overlay asks.
//!
//! `seating` rather than a session: it grabs nothing and claims only free
//! seats, so a pad picked up mid-level takes the next seat without anybody
//! leaving the game. danstick does not acknowledge it, and a second `seating`
//! with a different hold drops every hold in flight, so it is sent only when
//! danstick could have stopped listening: on each connection, and when a
//! `state` says it is not listening while a seat is free and no session is
//! open. The same rule as the picker's, which found both of those the hard way.

use serde_json::json;

use crate::events::Event;

/// Seats asked for before danstick has said how many it has.
const SLOTS: i32 = 4;

#[derive(Debug, Clone, PartialEq)]
pub struct Seating {
    /// How long a hold takes, in seconds: the length the daemon was started
    /// with, so asking again never changes it.
    hold: f64,
    slots: i32,
    /// Asked since danstick last could have stopped listening.
    asked: bool,
    /// A daemon too old to know `seating`; it is not asked again.
    refused: bool,
}

impl Seating {
    pub fn new(hold: f64) -> Self {
        Self {
            hold,
            slots: SLOTS,
            asked: false,
            refused: false,
        }
    }

    /// The seats danstick has.
    pub fn slots(&self) -> i32 {
        self.slots
    }

    /// The connection went: whatever answers next remembers nothing.
    pub fn lost(&mut self) {
        self.asked = false;
    }

    /// One danstick event.
    pub fn apply(&mut self, event: &Event) {
        match event {
            Event::State {
                seated,
                listening,
                status,
                slots,
            } => {
                if *slots > 0 {
                    self.slots = *slots;
                }
                // Not listening, with a seat free and no session open: after a
                // session, or a full room that has a seat again. Full seats
                // and a session are the same shape of nothing to do.
                let free = (seated.len() as i32) < self.slots;
                if *listening == Some(false) && free && status != "assigning" {
                    self.asked = false;
                }
            }
            // Matched whole: other errors mention seating too.
            Event::Error { message } if message == "unknown command \"seating\"" => self.refused = true,
            _ => {}
        }
    }

    /// The line to send now, if danstick needs asking.
    pub fn wanted(&mut self) -> Option<String> {
        if self.refused || self.asked {
            return None;
        }
        self.asked = true;
        Some(json!({"cmd": "seating", "open": true, "players": self.slots, "hold": self.hold}).to_string())
    }
}

impl crate::events::Listen for Seating {
    fn apply(&mut self, event: &crate::events::Event) {
        Seating::apply(self, event);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::events::Seat;

    fn state(seated: usize, listening: Option<bool>, status: &str) -> Event {
        Event::State {
            seated: (1..=seated)
                .map(|n| Seat {
                    node: format!("e{n}"),
                    name: String::new(),
                    player: n as i32,
                    mapped: true,
                })
                .collect(),
            listening,
            status: status.into(),
            slots: 4,
        }
    }

    fn asked(line: Option<String>) -> serde_json::Value {
        serde_json::from_str(&line.expect("asked")).expect("json")
    }

    #[test]
    fn a_new_connection_is_asked_once_with_the_hold_it_was_started_with() {
        let mut seating = Seating::new(1.5);
        assert_eq!(
            asked(seating.wanted()),
            json!({"cmd": "seating", "open": true, "players": 4, "hold": 1.5})
        );
        assert_eq!(seating.wanted(), None, "not again while nothing changed");
        seating.apply(&state(1, Some(true), "idle"));
        assert_eq!(seating.wanted(), None, "a claim leaves it listening");
        seating.lost();
        assert!(seating.wanted().is_some(), "a daemon met again is asked again");
    }

    #[test]
    fn it_is_asked_again_only_when_it_stopped_listening_with_a_seat_free() {
        let mut seating = Seating::new(1.5);
        seating.wanted();
        seating.apply(&state(4, Some(false), "idle"));
        assert_eq!(seating.wanted(), None, "a full room is nothing to do");
        seating.apply(&state(2, Some(false), "assigning"));
        assert_eq!(seating.wanted(), None, "a session is someone else's");
        seating.apply(&state(3, Some(false), "idle"));
        assert!(seating.wanted().is_some(), "a seat free and nobody listening");
        seating.apply(&state(3, None, "idle"));
        assert_eq!(seating.wanted(), None, "a daemon that does not say is not nagged");
    }

    #[test]
    fn a_daemon_that_does_not_know_the_command_is_not_asked_again() {
        let mut seating = Seating::new(1.5);
        seating.wanted();
        seating.apply(&Event::Error {
            message: "unknown command \"seating\"".into(),
        });
        seating.lost();
        assert_eq!(seating.wanted(), None);
    }

    #[test]
    fn the_seats_asked_for_are_the_ones_danstick_has() {
        let mut seating = Seating::new(1.5);
        seating.apply(&Event::State {
            seated: vec![],
            listening: Some(false),
            status: "idle".into(),
            slots: 2,
        });
        assert_eq!(asked(seating.wanted())["players"], 2);
    }
}
