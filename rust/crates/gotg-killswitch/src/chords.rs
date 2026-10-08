//! The exit chord and the menu chord as the loop keeps them: each pad's two
//! timed holds, what one sample of them says, and what a whole round of
//! samples across every pad adds up to. The decisions -- the furthest hold wins
//! the bar, a fired exit beats progress, the last pad to ask for the menu is
//! the one that gets it -- are tested without SDL; reading a pad and logging
//! stay in the loop.

use std::collections::{BTreeSet, HashMap};

use crate::killswitch::{Chord, Input, Pad};

/// How long the menu chord is held before it asks for the menu.
pub const MENU_HOLD_MS: u64 = 500;

/// One pad's two holds -- the exit's and the menu's -- timed on its controls.
#[derive(Debug, Clone)]
pub struct Holds {
    exit: Pad,
    menu: Pad,
    /// Whether this hold has been mentioned in the log.
    announced: bool,
    hold_ms: u64,
}

/// One sample of one pad's holds.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Sample {
    /// The exit fired on this sample.
    pub fired: bool,
    /// How far through the exit hold, 0..1.
    pub progress: f64,
    /// The menu chord fired on this sample.
    pub menu_fired: bool,
    /// The exit hold began on this sample: the loop says so once in the log,
    /// so the log of a session that ended this way says why.
    pub started: bool,
}

impl Holds {
    pub fn new(hold_ms: u64) -> Self {
        Self {
            exit: Pad::new(hold_ms),
            menu: Pad::timing(Chord::Menu, MENU_HOLD_MS),
            announced: false,
            hold_ms,
        }
    }

    /// The same for a pad read through SDL and a seated player read through
    /// danstick's `native`.
    pub fn sample(&mut self, input: Input, now: u64) -> Sample {
        let fired = self.exit.step(input, now);
        let menu_fired = self.menu.step(input, now);
        let started = self.exit.holding() && !self.announced;
        if started {
            self.announced = true;
        }
        if !self.exit.holding() {
            self.announced = false;
        }
        let progress = if self.hold_ms > 0 {
            self.exit.held_ms(now) as f64 / self.hold_ms as f64
        } else if self.exit.holding() {
            1.0
        } else {
            0.0
        };
        Sample {
            fired,
            progress,
            menu_fired,
            started,
        }
    }
}

/// Who asked for the menu this round, and what they had down (the chord's
/// own buttons, which the menu ignores until let go).
pub type Asked = (Option<i32>, BTreeSet<String>);

/// What every pad's sample added up to in one pass of the loop.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Round {
    fire: bool,
    /// The furthest along any one pad is, since the picture is of a hold
    /// rather than of a controller.
    progress: f64,
    asked: Option<Asked>,
}

impl Round {
    /// Fold one pad in. `asked` is only called when the menu chord fired, so
    /// a pad is not read for its buttons otherwise; the last to ask wins.
    pub fn add(&mut self, sample: Sample, asked: impl FnOnce() -> Asked) {
        self.fire |= sample.fired;
        self.progress = self.progress.max(sample.progress);
        if sample.menu_fired {
            self.asked = Some(asked());
        }
    }

    /// Whether any exit hold completed.
    pub fn fire(&self) -> bool {
        self.fire
    }

    /// What the bar shows of the exit: full once it has fired.
    pub fn exit_progress(&self) -> f64 {
        if self.fire { 1.0 } else { self.progress }
    }

    pub fn take_asked(&mut self) -> Option<Asked> {
        self.asked.take()
    }
}

/// The holds of seated players read through danstick's `native`, by seat.
#[derive(Debug, Default)]
pub struct NativeHolds(HashMap<i32, Holds>);

impl NativeHolds {
    /// Forget a player who is no longer seated, so a hold does not outlive
    /// the seat it was timed for.
    pub fn retain_seated(&mut self, seated: &[i32]) {
        self.0.retain(|player, _| seated.contains(player));
    }

    pub fn of(&mut self, player: i32, hold_ms: u64) -> &mut Holds {
        self.0.entry(player).or_insert_with(|| Holds::new(hold_ms))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const NONE: Input = Input {
        left: false,
        right: false,
        start: false,
        back: false,
        a: false,
    };
    const EXIT: Input = Input {
        left: true,
        right: true,
        start: true,
        ..NONE
    };
    const MENU: Input = Input {
        left: true,
        right: true,
        back: true,
        ..NONE
    };

    #[test]
    fn an_exit_hold_is_announced_once_and_fires_once() {
        let mut holds = Holds::new(1000);
        let first = holds.sample(EXIT, 0);
        assert!(first.started && !first.fired);
        let mid = holds.sample(EXIT, 500);
        assert!(!mid.started, "said once per hold");
        assert_eq!(mid.progress, 0.5);
        let done = holds.sample(EXIT, 1000);
        assert!(done.fired && done.progress == 1.0);
        assert!(!holds.sample(EXIT, 1100).fired, "one shot per hold");
        holds.sample(NONE, 1200);
        assert!(holds.sample(EXIT, 1300).started, "a new hold is said again");
    }

    #[test]
    fn the_menu_chord_fires_on_its_own_timer_and_does_not_move_the_bar() {
        let mut holds = Holds::new(3000);
        holds.sample(MENU, 0);
        let sample = holds.sample(MENU, MENU_HOLD_MS);
        assert!(sample.menu_fired && !sample.fired);
        assert_eq!(sample.progress, 0.0);
    }

    #[test]
    fn a_zero_hold_is_full_the_moment_it_is_down() {
        let mut holds = Holds::new(0);
        assert_eq!(holds.sample(NONE, 5).progress, 0.0);
        let down = holds.sample(EXIT, 10);
        assert!(down.fired && down.progress == 1.0);
    }

    fn sample(progress: f64, fired: bool, menu_fired: bool) -> Sample {
        Sample {
            fired,
            progress,
            menu_fired,
            started: false,
        }
    }

    #[test]
    fn a_round_shows_the_furthest_hold_and_a_fire_fills_it() {
        let mut round = Round::default();
        round.add(sample(0.25, false, false), || unreachable!("no menu asked"));
        round.add(sample(0.5, false, false), || unreachable!("no menu asked"));
        round.add(sample(0.125, false, false), || unreachable!("no menu asked"));
        assert_eq!((round.fire(), round.exit_progress()), (false, 0.5));
        round.add(sample(0.0, true, false), || unreachable!("no menu asked"));
        assert_eq!((round.fire(), round.exit_progress()), (true, 1.0));
    }

    #[test]
    fn the_last_pad_to_ask_for_the_menu_gets_it_and_it_is_taken_once() {
        let mut round = Round::default();
        round.add(sample(0.0, false, true), || {
            (Some(1), BTreeSet::from(["a".into()]))
        });
        round.add(sample(0.0, false, true), || (None, BTreeSet::new()));
        assert_eq!(round.take_asked(), Some((None, BTreeSet::new())));
        assert_eq!(round.take_asked(), None);
    }

    #[test]
    fn native_holds_follow_who_is_seated() {
        let mut native = NativeHolds::default();
        native.of(1, 1000).sample(EXIT, 0);
        native.of(2, 1000).sample(EXIT, 0);
        native.retain_seated(&[2]);
        assert!(
            native.of(1, 1000).sample(EXIT, 600).started,
            "player one's hold began again after they left"
        );
        assert!(
            !native.of(2, 1000).sample(EXIT, 600).started,
            "player two's carried on"
        );
    }
}
