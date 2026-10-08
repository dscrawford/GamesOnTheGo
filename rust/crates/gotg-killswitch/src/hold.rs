//! Holding every seated pad back from the game while Steam's overlay is up.
//!
//! danstick keeps forwarding a pad to its clone while Steam's menus take the
//! same presses, so the game moves on under somebody choosing in Steam. The
//! `hold` command (docs/requests/hold-every-seat-while-the-steam-overlay-is-up.md)
//! rests every clone until it is closed. It is leased to the connection, so a
//! new connection is asked again if the overlay is still up, and a danstick
//! too old to know it is left alone: the game gets the presses, as before.

use serde_json::json;

use crate::events::Event;

#[derive(Debug, Clone, Default)]
pub struct Hold {
    /// Steam's overlay is up.
    up: bool,
    /// A hold is open on this connection.
    sent: bool,
    /// danstick does not know `hold`: not asked again.
    refused: bool,
    /// The refusal has not been said in the log yet.
    unsaid: bool,
}

impl Hold {
    /// Steam's overlay went up or down.
    pub fn want(&mut self, up: bool) {
        self.up = up;
    }

    /// The line to send now, if what danstick holds is not what is wanted.
    pub fn wanted(&mut self) -> Option<String> {
        if self.refused || self.up == self.sent {
            return None;
        }
        self.sent = self.up;
        Some(
            if self.up {
                json!({"cmd": "hold"})
            } else {
                json!({"cmd": "hold", "open": false})
            }
            .to_string(),
        )
    }

    /// The connection went, or a line did not: the hold went with it.
    pub fn lost(&mut self) {
        self.sent = false;
    }

    /// True once, the first time after danstick refused.
    pub fn take_refused(&mut self) -> bool {
        std::mem::take(&mut self.unsaid)
    }
}

impl crate::events::Listen for Hold {
    fn apply(&mut self, event: &Event) {
        if event.refuses("hold") && !self.refused {
            self.refused = true;
            self.unsaid = true;
            self.sent = false;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::events::Listen;

    fn refusal() -> Event {
        Event::Error {
            message: "unknown command \"hold\"".into(),
        }
    }

    #[test]
    fn up_opens_it_once_and_down_closes_it_once() {
        let mut hold = Hold::default();
        assert_eq!(hold.wanted(), None, "nothing asked while down");
        hold.want(true);
        assert_eq!(hold.wanted(), Some(r#"{"cmd":"hold"}"#.to_owned()));
        assert_eq!(hold.wanted(), None);
        hold.want(false);
        assert_eq!(hold.wanted(), Some(r#"{"cmd":"hold","open":false}"#.to_owned()));
        assert_eq!(hold.wanted(), None);
    }

    #[test]
    fn a_new_connection_is_asked_again_while_the_overlay_is_still_up() {
        let mut hold = Hold::default();
        hold.want(true);
        hold.wanted();
        hold.lost();
        assert!(hold.wanted().is_some(), "the hold was leased to the old one");
        hold.want(false);
        hold.wanted();
        hold.lost();
        assert_eq!(hold.wanted(), None, "nothing to hold on a new connection");
    }

    #[test]
    fn a_danstick_without_it_is_said_so_once_and_not_asked_again() {
        let mut hold = Hold::default();
        hold.want(true);
        hold.wanted();
        hold.apply(&refusal());
        assert!(hold.take_refused());
        assert!(!hold.take_refused(), "once");
        hold.lost();
        assert_eq!(hold.wanted(), None);
        hold.want(false);
        assert_eq!(hold.wanted(), None);
        hold.apply(&refusal());
        assert!(!hold.take_refused(), "not said again");
    }

    #[test]
    fn other_errors_are_not_a_refusal() {
        let mut hold = Hold::default();
        hold.want(true);
        hold.apply(&Event::Error {
            message: "hold: a session is open".into(),
        });
        assert!(!hold.take_refused());
        hold.lost();
        assert!(hold.wanted().is_some());
    }
}
