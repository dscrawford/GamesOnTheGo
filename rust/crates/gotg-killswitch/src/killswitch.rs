//! The kill switch's decision, with no SDL and no clock of its own.
//!
//! Split out so it can be tested: "held for three seconds" is the whole
//! feature, and the only way to check it without a controller, a game and
//! three seconds of real time is to feed a made-up clock made-up inputs.

/// One controller, reduced to the four things the chords ask about.
///
/// Left and right are "that shoulder is down" *or* "that trigger is pulled"
/// on purpose. Which one a pad reports is a property of the pad, not of the
/// player's intent: a Switch Pro reports L and R as buttons where a GameCube
/// adapter reports them as axes, and somebody squeezing both and holding
/// Start means the same thing on either.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Input {
    pub left: bool,
    pub right: bool,
    pub start: bool,
    /// Select, Back, Minus, View: the button left of centre.
    pub back: bool,
}

/// Which of the two holds a [`Pad`] is timing.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Chord {
    /// Both shoulders and Start: the game stops.
    Exit,
    /// Both shoulders and Select: the pad's buttons are walked again in
    /// danstick, over the game, for this pad alone.
    Rebind,
}

impl Input {
    /// Both shoulders and the chord's own button, and not the other one's.
    /// Two shoulders is a grip somebody could stumble into; two shoulders
    /// and Start, held past three seconds, is not -- which is the entire
    /// brief for a control that must never fire by accident and must exist
    /// on every pad. With Start *and* Select down it is neither: holding
    /// everything is not a choice between quitting and rebinding.
    fn chord(self, chord: Chord) -> bool {
        let (want, other) = match chord {
            Chord::Exit => (self.start, self.back),
            Chord::Rebind => (self.back, self.start),
        };
        self.left && self.right && want && !other
    }
}

/// One pad's hold.
#[derive(Debug, Clone)]
pub struct Pad {
    chord: Chord,
    hold_ms: u64,
    since_ms: u64,
    holding: bool,
    // One shot per hold, so a held chord fires once.
    fired: bool,
}

impl Pad {
    /// The exit hold.
    pub fn new(hold_ms: u64) -> Self {
        Self::timing(Chord::Exit, hold_ms)
    }

    pub fn timing(chord: Chord, hold_ms: u64) -> Self {
        Self {
            chord,
            hold_ms,
            since_ms: 0,
            holding: false,
            fired: false,
        }
    }

    /// One sample. True exactly once per hold, on the first sample at or
    /// past the hold's length.
    pub fn step(&mut self, input: Input, now_ms: u64) -> bool {
        if !input.chord(self.chord) {
            // Letting go of any one of them starts the hold over. A switch
            // that counted cumulative time would fire on a long session of
            // ordinary shoulder-button play.
            self.holding = false;
            self.fired = false;
            return false;
        }
        if !self.holding {
            self.holding = true;
            self.since_ms = now_ms;
            self.fired = false;
        } else if now_ms < self.since_ms {
            // A clock that went backwards. Not expected from a monotonic
            // source, but the alternative is an enormous elapsed time and an
            // instant kill.
            self.since_ms = now_ms;
        }
        if self.fired || now_ms - self.since_ms < self.hold_ms {
            return false;
        }
        self.fired = true;
        true
    }

    /// Whether the chord is down now.
    pub fn holding(&self) -> bool {
        self.holding
    }

    /// How long the current hold has run; zero when nothing is held.
    pub fn held_ms(&self, now_ms: u64) -> u64 {
        if !self.holding {
            return 0;
        }
        now_ms.saturating_sub(self.since_ms)
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
    };
    const CHORD: Input = Input {
        left: true,
        right: true,
        start: true,
        back: false,
    };
    const REBIND: Input = Input {
        left: true,
        right: true,
        start: false,
        back: true,
    };

    /// Holding from t=0, sampled every `step` ms like the real loop: when it
    /// fired, if it did.
    fn fires_at(input: Input, hold_ms: u64, step: u64, until: u64) -> Option<u64> {
        let mut pad = Pad::new(hold_ms);
        (0..=until)
            .step_by(step as usize)
            .find(|&now| pad.step(input, now))
    }

    #[test]
    fn a_held_chord_fires_once_at_three_seconds() {
        let mut pad = Pad::new(3000);
        let fired = (0..=6000).step_by(50).filter(|&now| pad.step(CHORD, now)).count();
        assert_eq!(fired, 1, "a chord held for six seconds fires exactly once");
        assert_eq!(
            fires_at(CHORD, 3000, 50, 6000),
            Some(3000),
            "and at three seconds, not before"
        );
    }

    #[test]
    fn it_does_not_fire_early() {
        assert_eq!(fires_at(CHORD, 3000, 50, 2950), None);
    }

    #[test]
    fn releasing_starts_the_three_seconds_over() {
        let mut pad = Pad::new(3000);
        assert!(
            (0..2500).step_by(50).all(|now| !pad.step(CHORD, now)),
            "quiet through 2.5 s of holding"
        );
        assert!(!pad.step(NONE, 2500), "and quiet on release");
        let fired = (2600..=6000).step_by(50).find(|&now| pad.step(CHORD, now));
        assert_eq!(fired, Some(5600), "the second hold is timed from its own start");
    }

    #[test]
    fn a_shoulder_short_of_the_chord_never_fires() {
        let partials = [
            Input {
                left: true,
                right: true,
                start: false,
                back: false,
            }, // the resting grip
            Input {
                left: true,
                right: false,
                start: true,
                back: false,
            },
            Input {
                left: false,
                right: true,
                start: true,
                back: false,
            },
            Input {
                left: false,
                right: false,
                start: true,
                back: false,
            }, // Start alone pauses half these games
        ];
        for partial in partials {
            assert_eq!(fires_at(partial, 3000, 50, 30000), None, "{partial:?} fired");
        }
    }

    #[test]
    fn a_slow_sample_still_fires() {
        // Three seconds means "three seconds have passed", not "sixty samples".
        assert_eq!(fires_at(CHORD, 3000, 500, 6000), Some(3000));
        assert_eq!(
            fires_at(CHORD, 3000, 1100, 6000),
            Some(3300),
            "late rather than never"
        );
    }

    #[test]
    fn a_second_hold_can_fire_again() {
        let mut pad = Pad::new(3000);
        for now in (0..=3000).step_by(50) {
            pad.step(CHORD, now);
        }
        pad.step(NONE, 3050);
        let fired = (3100..=7000)
            .step_by(50)
            .filter(|&now| pad.step(CHORD, now))
            .count();
        assert_eq!(fired, 1);
    }

    #[test]
    fn a_clock_that_goes_backwards_does_not_fire() {
        let mut pad = Pad::new(3000);
        assert!(!pad.step(CHORD, 10000), "the hold begins");
        assert!(
            !pad.step(CHORD, 9000),
            "a clock that jumped back is not three seconds of holding"
        );
        assert!(
            !pad.step(CHORD, 11000),
            "and the hold is re-timed from where it landed"
        );
        assert!(pad.step(CHORD, 12000), "firing three seconds after that");
    }

    #[test]
    fn held_ms_reports_the_hold() {
        let mut pad = Pad::new(3000);
        assert_eq!(pad.held_ms(1000), 0);
        pad.step(CHORD, 1000);
        assert_eq!(pad.held_ms(2500), 1500);
        pad.step(NONE, 2600);
        assert_eq!(pad.held_ms(2700), 0);
    }

    #[test]
    fn select_in_place_of_start_is_the_rebind_hold_not_the_exit() {
        let mut exit = Pad::new(3000);
        let mut rebind = Pad::timing(Chord::Rebind, 3000);
        let exited = (0..=6000).step_by(50).any(|now| exit.step(REBIND, now));
        let asked = (0..=6000)
            .step_by(50)
            .filter(|&now| rebind.step(REBIND, now))
            .count();
        assert!(!exited, "Select is not Start: the game keeps running");
        assert_eq!(asked, 1, "the rebind fires once, at three seconds");
        let mut rebind = Pad::timing(Chord::Rebind, 3000);
        assert!(
            !(0..=6000).step_by(50).any(|now| rebind.step(CHORD, now)),
            "and Start is not Select"
        );
    }

    #[test]
    fn holding_start_and_select_together_is_neither() {
        let everything = Input {
            start: true,
            ..REBIND
        };
        for chord in [Chord::Exit, Chord::Rebind] {
            let mut pad = Pad::timing(chord, 3000);
            assert!(
                !(0..=6000).step_by(50).any(|now| pad.step(everything, now)),
                "{chord:?} fired with both buttons down"
            );
        }
    }

    #[test]
    fn a_zero_hold_fires_at_once_rather_than_never() {
        let mut pad = Pad::new(0);
        assert!(pad.step(CHORD, 1000), "a zero hold fires on the first sample");
        assert!(!pad.step(CHORD, 1050), "and only once, like any other hold");
    }
}
