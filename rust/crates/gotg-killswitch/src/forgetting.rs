//! Resetting a binding from the menu: one control's, or a whole scope's
//! walk, dropped so the pad falls through to what it had before. danstick's
//! `forget` (docs/requests/forget-one-control-or-one-scope.md); a daemon too
//! old to know it says so once, and the menu offers no reset until one does.

use serde_json::json;

use crate::events::Event;

/// The line that drops `control`'s binding under `scope`, or the whole of
/// the pad's walk there with no control named.
pub fn line(player: i32, scope: &str, control: Option<&str>) -> String {
    let mut line = json!({"cmd": "forget", "player": player, "scope": scope});
    if let Some(control) = control {
        line["control"] = json!(control);
    }
    line.to_string()
}

#[derive(Debug, Clone, Default)]
pub struct Forgetting {
    /// Whether danstick knows `forget`: None until one has been answered.
    known: Option<bool>,
    /// One is out, unanswered.
    asked: bool,
    /// A refusal not yet taken.
    unsaid: bool,
}

impl Forgetting {
    pub fn sent(&mut self) {
        self.asked = true;
    }

    /// True once, the first time after danstick refused.
    pub fn take_refused(&mut self) -> bool {
        std::mem::take(&mut self.unsaid)
    }

    /// danstick has answered a reset: the menu can offer one.
    pub fn can(&self) -> bool {
        self.known == Some(true)
    }
}

impl crate::events::Listen for Forgetting {
    fn apply(&mut self, event: &Event) {
        if event.refuses("forget") {
            self.known = Some(false);
            self.unsaid = true;
            self.asked = false;
        } else if self.asked {
            match event {
                Event::State { .. } => {
                    self.known = Some(true);
                    self.asked = false;
                }
                // Some other error while one is out: not known to be the answer.
                Event::Error { .. } => self.asked = false,
                _ => {}
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::events::Listen;

    fn state() -> Event {
        Event::State {
            seated: Vec::new(),
            listening: None,
            status: "ready".into(),
            slots: 0,
            ports_off: Vec::new(),
        }
    }

    #[test]
    fn a_reset_of_one_control_asks_danstick_to_forget_only_it() {
        let sent: serde_json::Value = serde_json::from_str(&line(2, "console:n64", Some("a"))).expect("json");
        assert_eq!(
            sent,
            json!({"cmd": "forget", "player": 2, "scope": "console:n64", "control": "a"})
        );
    }

    #[test]
    fn a_reset_of_the_whole_walk_names_no_control() {
        let sent: serde_json::Value =
            serde_json::from_str(&line(1, "game:env-n64-dk64", None)).expect("json");
        assert_eq!(
            sent,
            json!({"cmd": "forget", "player": 1, "scope": "game:env-n64-dk64"})
        );
    }

    #[test]
    fn reset_stays_dim_until_danstick_has_answered_one() {
        let mut forgetting = Forgetting::default();
        assert!(!forgetting.can());
        forgetting.apply(&state());
        assert!(!forgetting.can(), "a state nobody asked for says nothing");
        forgetting.sent();
        forgetting.apply(&state());
        assert!(forgetting.can());
    }

    #[test]
    fn an_old_daemon_refusing_forget_is_said_once() {
        let mut forgetting = Forgetting::default();
        forgetting.sent();
        forgetting.apply(&Event::Error {
            message: "unknown command \"forget\"".into(),
        });
        assert!(!forgetting.can());
        assert!(forgetting.take_refused());
        assert!(!forgetting.take_refused());
        forgetting.apply(&state());
        assert!(!forgetting.can(), "a later state is not an answer");
    }

    #[test]
    fn another_commands_refusal_is_not_a_refused_reset() {
        let mut forgetting = Forgetting::default();
        forgetting.sent();
        forgetting.apply(&Event::Error {
            message: "unknown command \"hold\"".into(),
        });
        assert!(!forgetting.take_refused());
        assert!(!forgetting.can());
    }
}
