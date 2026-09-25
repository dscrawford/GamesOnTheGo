//! Rebinding a controller in the middle of a game: both shoulders and Select,
//! held for three seconds, and padmap walks that pad's buttons again.
//!
//! The binding is padmap's, not the game's: the emulator reads a clone whose
//! buttons padmap decides, so remapping there changes every game at once and
//! touches no emulator's settings. padmap runs the wizard with no session --
//! it grabs only this seat's pad and holds its clone back, so the game sees
//! nothing while the buttons are walked, and everybody else keeps playing.
//!
//! This is the decision half, with no socket and no clock of its own: what
//! to ask padmap, and what the bar should show while it answers.

use serde_json::json;

use crate::events::Event;

/// How long padmap has to begin the walk before the bar gives up on it: a
/// daemon too old to know `map` with no session answers with an error, but
/// one that is not there answers nothing at all.
pub const ASK_SECONDS: f64 = 4.0;

/// How long the finished walk stays on the bar, so it reads as done rather
/// than as vanished.
pub const LINGER_SECONDS: f64 = 0.8;

#[derive(Debug, Clone, PartialEq)]
enum Phase {
    Idle,
    Asked { player: i32, since: f64 },
    Walking(View),
    Ended { view: View, until: f64 },
}

/// What the bar draws for a rebind.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct View {
    pub player: i32,
    /// padmap's control id being asked for; empty until the first step.
    pub control: String,
    /// 0-based step, and how many there are.
    pub index: i32,
    pub total: i32,
    /// 0..1 through the long hold that finishes early.
    pub finish: f64,
    /// Some once it has ended: whether padmap kept the new buttons.
    pub stored: Option<bool>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Rebind {
    phase: Phase,
}

impl Default for Rebind {
    fn default() -> Self {
        Self { phase: Phase::Idle }
    }
}

impl Rebind {
    /// The chord fired on `player`'s pad: the line to send padmap, or None
    /// while another rebind is on screen -- one at a time, as the bar has
    /// room for one controller.
    pub fn start(&mut self, player: i32, layout: &str, scope: &str, now: f64) -> Option<String> {
        self.expire(now);
        if self.phase != Phase::Idle || player < 1 {
            return None;
        }
        self.phase = Phase::Asked { player, since: now };
        Some(json!({"cmd": "map", "player": player, "layout": layout, "scope": scope}).to_string())
    }

    fn player(&self) -> Option<i32> {
        match &self.phase {
            Phase::Idle => None,
            Phase::Asked { player, .. } => Some(*player),
            Phase::Walking(view) | Phase::Ended { view, .. } => Some(view.player),
        }
    }

    /// One padmap event. Only this rebind's player's, and only while one is
    /// on: somebody else's wizard at the picker is not ours to draw.
    pub fn apply(&mut self, event: &Event, now: f64) {
        let Some(ours) = self.player() else { return };
        if matches!(self.phase, Phase::Ended { .. }) {
            return;
        }
        let mine = |player: i32| player == ours || player == 0;
        let current = match &self.phase {
            Phase::Walking(view) => view.clone(),
            _ => View {
                player: ours,
                ..View::default()
            },
        };
        match event {
            Event::Mapping {
                player,
                control,
                index,
                total,
                done,
                stored,
            } if mine(*player) => {
                if *done {
                    self.end(current, *stored, now);
                } else {
                    self.phase = Phase::Walking(View {
                        control: control.clone(),
                        index: *index,
                        total: *total,
                        finish: 0.0,
                        ..current
                    });
                }
            }
            Event::Finish { player, frac } if mine(*player) => {
                if let Phase::Walking(view) = &mut self.phase {
                    view.finish = frac.clamp(0.0, 1.0);
                }
            }
            // A refusal while asking is this rebind's: padmap too old, or the
            // seat gone. Once walking, an error is somebody else's command.
            Event::Error { .. } if matches!(self.phase, Phase::Asked { .. }) => {
                self.end(current, false, now);
            }
            _ => {}
        }
    }

    fn end(&mut self, view: View, stored: bool, now: f64) {
        self.phase = Phase::Ended {
            view: View {
                stored: Some(stored),
                ..view
            },
            until: now + LINGER_SECONDS,
        };
    }

    fn expire(&mut self, now: f64) {
        match &self.phase {
            Phase::Asked { since, .. } if now - since >= ASK_SECONDS => self.phase = Phase::Idle,
            Phase::Ended { until, .. } if now >= *until => self.phase = Phase::Idle,
            _ => {}
        }
    }

    /// What to draw now, or None when nothing is being rebound.
    pub fn view(&mut self, now: f64) -> Option<View> {
        self.expire(now);
        match &self.phase {
            Phase::Idle => None,
            Phase::Asked { player, .. } => Some(View {
                player: *player,
                ..View::default()
            }),
            Phase::Walking(view) | Phase::Ended { view, .. } => Some(view.clone()),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn step(player: i32, control: &str, index: i32, total: i32) -> Event {
        Event::Mapping {
            player,
            control: control.into(),
            index,
            total,
            done: false,
            stored: false,
        }
    }

    fn done(player: i32, stored: bool) -> Event {
        Event::Mapping {
            player,
            control: String::new(),
            index: 0,
            total: 0,
            done: true,
            stored,
        }
    }

    #[test]
    fn the_chord_asks_padmap_to_walk_that_seats_buttons() {
        let mut rebind = Rebind::default();
        let line = rebind.start(2, "n64", "console:n64", 10.0).expect("a command");
        let sent: serde_json::Value = serde_json::from_str(&line).expect("json");
        assert_eq!(
            sent,
            json!({"cmd": "map", "player": 2, "layout": "n64", "scope": "console:n64"})
        );
        assert_eq!(
            rebind.view(10.1).map(|v| v.player),
            Some(2),
            "the bar comes down at once"
        );
    }

    #[test]
    fn each_step_names_the_control_and_the_walk_ends_on_done() {
        let mut rebind = Rebind::default();
        rebind.start(1, "n64", "console:n64", 0.0);
        rebind.apply(&step(1, "a", 0, 14), 0.2);
        let view = rebind.view(0.2).expect("walking");
        assert_eq!((view.control.as_str(), view.index, view.total), ("a", 0, 14));
        rebind.apply(&step(1, "b", 1, 14), 1.0);
        assert_eq!(rebind.view(1.0).map(|v| v.control), Some("b".into()));
        rebind.apply(&done(1, true), 5.0);
        assert_eq!(
            rebind.view(5.1).and_then(|v| v.stored),
            Some(true),
            "said, briefly"
        );
        assert_eq!(
            rebind.view(5.0 + LINGER_SECONDS),
            None,
            "and then the bar goes up"
        );
    }

    #[test]
    fn the_long_hold_that_finishes_early_is_drawn_as_it_fills() {
        let mut rebind = Rebind::default();
        rebind.start(1, "n64", "console:n64", 0.0);
        rebind.apply(&step(1, "dpup", 4, 14), 0.5);
        rebind.apply(&Event::Finish { player: 1, frac: 0.6 }, 1.0);
        assert_eq!(rebind.view(1.0).map(|v| v.finish), Some(0.6));
        rebind.apply(&step(1, "dpdown", 5, 14), 1.5);
        assert_eq!(
            rebind.view(1.5).map(|v| v.finish),
            Some(0.0),
            "a new step starts empty"
        );
    }

    #[test]
    fn somebody_elses_wizard_is_not_this_ones() {
        let mut rebind = Rebind::default();
        rebind.start(1, "n64", "console:n64", 0.0);
        rebind.apply(&step(3, "a", 0, 14), 0.2);
        rebind.apply(&done(3, true), 0.3);
        let view = rebind.view(0.4).expect("still ours");
        assert_eq!((view.control.as_str(), view.stored), ("", None));
        let mut idle = Rebind::default();
        idle.apply(&step(1, "a", 0, 14), 0.0);
        assert_eq!(idle.view(0.0), None, "no rebind, nothing drawn");
    }

    #[test]
    fn one_rebind_at_a_time() {
        let mut rebind = Rebind::default();
        assert!(rebind.start(1, "n64", "console:n64", 0.0).is_some());
        assert!(rebind.start(2, "n64", "console:n64", 1.0).is_none());
        rebind.apply(&done(1, false), 2.0);
        assert!(
            rebind
                .start(2, "n64", "console:n64", 2.0 + LINGER_SECONDS)
                .is_some(),
            "free again once the last one has gone up"
        );
    }

    #[test]
    fn a_padmap_that_never_answers_or_refuses_does_not_hold_the_bar_down() {
        let mut silent = Rebind::default();
        silent.start(1, "n64", "console:n64", 0.0);
        assert!(silent.view(ASK_SECONDS - 0.1).is_some());
        assert_eq!(silent.view(ASK_SECONDS), None);
        let mut refused = Rebind::default();
        refused.start(1, "n64", "console:n64", 0.0);
        refused.apply(
            &Event::Error {
                message: "unknown command \"map\"".into(),
            },
            0.3,
        );
        assert_eq!(refused.view(0.4).and_then(|v| v.stored), Some(false));
        assert_eq!(refused.view(0.3 + LINGER_SECONDS), None);
    }

    #[test]
    fn a_seat_that_is_not_one_asks_nothing() {
        assert!(Rebind::default().start(0, "n64", "console:n64", 0.0).is_none());
    }
}
