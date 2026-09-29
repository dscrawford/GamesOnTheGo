//! The top bar's motion: down when there is something to show, up when not.
//!
//! "Slides down in a quarter of a second, and turning round half way does not
//! jump" is a thing a test can hold with a made-up clock, and not with a
//! compositor. A reversal starts from wherever the bar is, and takes the time
//! the rest of that distance is worth -- a bar called back up an inch into its
//! descent is back in an instant, not a full slide later.

#[derive(Debug, Clone)]
pub struct Bar {
    /// A full slide, top to bottom, as the bar usually moves.
    slide_seconds: f64,
    /// This slide's: the usual one, or a slower one asked for by `want_over`.
    this_slide: f64,
    /// When the direction last changed, and where it was then.
    changed: f64,
    from: f64,
    down: bool,
}

impl Bar {
    pub fn new(slide_seconds: f64) -> Self {
        Self {
            slide_seconds: slide_seconds.max(0.0),
            this_slide: slide_seconds.max(0.0),
            changed: 0.0,
            from: 0.0,
            down: false,
        }
    }

    fn target(&self) -> f64 {
        if self.down { 1.0 } else { 0.0 }
    }

    /// 0..1 through the current slide.
    fn progress(&self, now: f64) -> f64 {
        // A slide's worth of the distance left.
        let span = self.this_slide * (self.target() - self.from).abs();
        if span <= 0.0 {
            return 1.0;
        }
        ((now - self.changed) / span).clamp(0.0, 1.0)
    }

    /// Where the bar should be going. Changing direction mid-slide keeps its
    /// place.
    pub fn want(&mut self, down: bool, now: f64) {
        let usual = self.slide_seconds;
        self.want_over(down, now, usual);
    }

    /// The same, with a full slide taking `seconds` this once: the menu goes
    /// away slowly, so closing it reads as closing rather than as vanishing.
    /// A slide already under way keeps the speed it started with.
    pub fn want_over(&mut self, down: bool, now: f64, seconds: f64) {
        if down == self.down {
            return;
        }
        self.from = self.position(now);
        self.down = down;
        self.changed = now;
        self.this_slide = seconds.max(0.0);
    }

    /// How far down it is: 0 out of sight, 1 all the way down. Smoothstep, so
    /// it slows into both ends rather than stopping dead.
    pub fn position(&self, now: f64) -> f64 {
        let t = self.progress(now);
        let eased = t * t * (3.0 - 2.0 * t);
        self.from + (self.target() - self.from) * eased
    }

    /// Up and finished moving: the window can go.
    pub fn gone(&self, now: f64) -> bool {
        !self.down && self.progress(now) >= 1.0
    }

    /// Still moving, so the loop works at a frame rate rather than idling.
    pub fn moving(&self, now: f64) -> bool {
        self.progress(now) < 1.0
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn near(a: f64, b: f64, tolerance: f64) -> bool {
        (a - b).abs() <= tolerance
    }

    #[test]
    fn a_slow_slide_up_takes_the_time_it_was_asked_for_and_the_next_is_usual_again() {
        let mut bar = Bar::new(0.2);
        bar.want(true, 0.0);
        assert!(near(bar.position(0.2), 1.0, 1e-9));
        bar.want_over(false, 1.0, 1.0);
        assert!(
            near(bar.position(1.5), 0.5, 0.01),
            "half way up after half a second"
        );
        assert!(!bar.gone(1.9) && bar.gone(2.0));
        bar.want(true, 3.0);
        assert!(
            near(bar.position(3.2), 1.0, 1e-9),
            "and the next slide is the usual one"
        );
    }

    #[test]
    fn the_bar_slides_down_and_back() {
        let mut bar = Bar::new(0.25);
        assert!(
            bar.position(1.0) == 0.0 && bar.gone(1.0),
            "it starts out of sight"
        );
        bar.want(true, 1.0);
        assert!(
            near(bar.position(1.125), 0.5, 1e-6),
            "half way down at half the slide"
        );
        assert!(
            bar.position(1.25) == 1.0 && !bar.moving(1.25),
            "all the way down at the end"
        );
        bar.want(false, 2.0);
        assert!(bar.gone(2.25), "and gone a slide after it is sent up");
    }

    #[test]
    fn turning_round_mid_slide_does_not_jump() {
        let mut bar = Bar::new(0.25);
        bar.want(true, 1.0);
        let at = bar.position(1.05);
        bar.want(false, 1.05);
        assert!(
            near(bar.position(1.05), at, 1e-9),
            "sent back up, it starts from where it was"
        );
        assert!(
            bar.gone(1.05 + 0.25 * at + 1e-6),
            "and the way back costs only the distance it came"
        );
    }

    #[test]
    fn a_zero_or_negative_slide_moves_instantly() {
        let mut bar = Bar::new(0.0);
        bar.want(true, 1.0);
        assert_eq!(bar.position(1.0), 1.0);
        bar.want(false, 1.0);
        assert!(bar.gone(1.0));
        let mut bar = Bar::new(-5.0);
        bar.want(true, 1.0);
        assert_eq!(bar.position(1.0), 1.0, "negative is instant, not undefined");
    }

    #[test]
    fn wanting_the_same_direction_again_does_not_restart_the_slide() {
        let mut bar = Bar::new(0.25);
        bar.want(true, 1.0);
        let halfway = bar.position(1.125);
        bar.want(true, 1.125);
        assert!(near(bar.position(1.125), halfway, 1e-9) && bar.position(1.25) == 1.0);
    }

    #[test]
    fn a_clock_before_the_change_stays_in_range() {
        let mut bar = Bar::new(0.25);
        bar.want(true, 5.0);
        assert!((0.0..=1.0).contains(&bar.position(4.9)));
    }

    #[test]
    fn a_double_reversal_still_settles() {
        let mut bar = Bar::new(0.25);
        bar.want(true, 1.0);
        bar.want(false, 1.05);
        bar.want(true, 1.06);
        assert!((0.0..=1.0).contains(&bar.position(1.06)));
        assert_eq!(bar.position(3.0), 1.0);
    }
}
