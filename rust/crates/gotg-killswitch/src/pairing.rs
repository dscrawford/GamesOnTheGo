//! Who is holding a button to join, in the order they started -- over a game.
//!
//! The picker draws this in its seat strip (gotg_ui/joining.py); a game had no
//! way to say it at all, so a controller picked up mid-level took a seat with
//! nothing on screen to show the hold was counting. The overlay draws it now,
//! from the same padmap events and by the same rules, which were measured
//! before they were written down:
//!
//!   - A named reading's release is said out loud (`frac: 0`), so silence is
//!     only a safety net for it. padmap sends progress from the loop that
//!     also rescans every device, and on a desktop with many of them readings
//!     arrive ~58 ms apart: taking 50 ms of quiet as a release dropped a
//!     steady press between every reading.
//!   - A nameless reading (an older daemon) has no release to say, so three
//!     frames of silence still ends it.
//!   - A reading smaller than the last one is a new press, at the back.
//!   - Between readings the fill carries on at the hold's own rate, and never
//!     steps backwards when the next reading lands a hair behind it.
//!
//! No clock of its own: the loop passes the time, so a test can.

/// Holds drawn at once. Eight people holding buttons is a party this screen
/// has no room to draw anyway; a ninth pushes out the oldest.
pub const HOLDS_MAX: usize = 8;
/// Seats shown as just taken at once.
pub const JOINED_MAX: usize = 4;
/// A key longer than this is cut: two keys sharing their first 95 bytes are
/// two device paths nobody has.
pub const KEY_MAX: usize = 95;

/// Silence that ends a nameless hold.
pub const STALE_ANONYMOUS: f64 = 0.05;
/// Silence that ends a named one: only a daemon gone away mid-press.
pub const STALE_NAMED: f64 = 0.5;
/// How long a seat just taken stays on screen, ticked, before the bar goes up.
pub const JOINED_SHOWN: f64 = 1.5;

/// One pad's hold, as drawn.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Hold {
    /// The node, else the name, else "" for a nameless reading.
    pub key: String,
    /// The last reading; in what [`Pairing::now`] returns, carried to now.
    pub fraction: f64,
    /// When this press began, for the order.
    pub started: f64,
    pub(crate) seen: f64,
    pub(crate) drawn: f64,
    /// The seat it is filling towards, 0 when unsaid.
    pub player: i32,
}

#[derive(Debug, Clone, Copy)]
struct Joined {
    player: i32,
    at: f64,
}

/// The picture of who is joining.
#[derive(Debug, Clone)]
pub struct Pairing {
    holds: Vec<Hold>,
    joined: Vec<Joined>,
    /// The length padmap was asked for, to carry fills between readings.
    hold_seconds: f64,
}

/// A key cut to [`KEY_MAX`] bytes, on a character boundary.
fn clip(text: &str) -> &str {
    if text.len() <= KEY_MAX {
        return text;
    }
    let mut end = KEY_MAX;
    while !text.is_char_boundary(end) {
        end -= 1;
    }
    &text[..end]
}

fn key_of<'a>(node: &'a str, name: &'a str) -> &'a str {
    clip(if !node.is_empty() { node } else { name })
}

impl Pairing {
    pub fn new(hold_seconds: f64) -> Self {
        Self {
            holds: Vec::with_capacity(HOLDS_MAX),
            joined: Vec::with_capacity(JOINED_MAX),
            hold_seconds,
        }
    }

    /// One `progress` event. `node` and `name` may be empty; `player` is 0
    /// when the event did not say.
    pub fn progress(&mut self, node: &str, name: &str, frac: f64, player: i32, now: f64) {
        let key = key_of(node, name);
        let found = self.holds.iter().position(|hold| hold.key == key);
        if frac <= 0.0 {
            // A release said out loud -- the one thing silence cannot tell
            // apart when two pads are holding.
            if let Some(at) = found {
                self.holds.remove(at);
            }
            return;
        }
        match found {
            Some(at) if frac >= self.holds[at].fraction - 1e-6 => {
                let hold = &mut self.holds[at];
                hold.fraction = frac;
                hold.seen = now;
                if player > 0 {
                    hold.player = player;
                }
            }
            _ => {
                // A new hold, or the same pad starting again: padmap's
                // fraction only climbs within one press, so a smaller one is a
                // different press, and it goes to the back however small the gap.
                let fresh = Hold {
                    key: key.to_owned(),
                    fraction: frac,
                    started: now,
                    seen: now,
                    drawn: 0.0,
                    player,
                };
                match found {
                    Some(at) => self.holds[at] = fresh,
                    None if self.holds.len() < HOLDS_MAX => self.holds.push(fresh),
                    None => {
                        let oldest = oldest_by(&self.holds, |hold| hold.started);
                        self.holds[oldest] = fresh;
                    }
                }
            }
        }
    }

    /// A seat was taken: that pad's hold is over -- everybody else's is not --
    /// and the new seat shows as joined for a moment.
    ///
    /// Only the pad that took the seat stops filling. Two people holding A a
    /// moment apart are two seats, and clearing every hold on the first claim
    /// drew the second one's ring empty while they were still holding. A claim
    /// that names nobody cannot say whose fill it ended, so it ends them all.
    pub fn claim(&mut self, node: &str, name: &str, player: i32, now: f64) {
        if key_of(node, name).is_empty() {
            self.holds.clear();
        } else {
            self.seated(node, name, player);
        }
        if player <= 0 {
            return;
        }
        if let Some(joined) = self.joined.iter_mut().find(|joined| joined.player == player) {
            joined.at = now;
        } else if self.joined.len() < JOINED_MAX {
            self.joined.push(Joined { player, at: now });
        } else {
            let oldest = oldest_by(&self.joined, |joined| joined.at);
            self.joined[oldest] = Joined { player, at: now };
        }
    }

    /// A `state` says this pad is seated: its hold is finished, nobody
    /// else's. By identity, not by seat number -- a reading's number is the
    /// seat it is filling towards, and it is stale for a tick after somebody
    /// else's claim.
    pub fn seated(&mut self, node: &str, name: &str, player: i32) {
        let (node, name) = (clip(node), clip(name));
        self.holds.retain(|hold| {
            let gone = if hold.key.is_empty() {
                // Nameless: the seat number is all there is.
                player > 0 && hold.player == player
            } else {
                (!node.is_empty() && hold.key == node) || (!name.is_empty() && hold.key == name)
            };
            !gone
        });
    }

    fn expire(&mut self, now: f64) {
        self.holds.retain(|hold| {
            let stale = if hold.key.is_empty() {
                STALE_ANONYMOUS
            } else {
                STALE_NAMED
            };
            now - hold.seen <= stale
        });
        self.joined.retain(|joined| now - joined.at <= JOINED_SHOWN);
    }

    /// The holds to draw, oldest press first, carried forward to `now`.
    /// Drops the ones whose readings stopped.
    pub fn now(&mut self, now: f64) -> Vec<Hold> {
        self.expire(now);
        let rate = self.hold_seconds;
        let mut out: Vec<Hold> = self
            .holds
            .iter_mut()
            .map(|hold| {
                let mut carried = hold.fraction;
                if rate > 0.0 && now > hold.seen {
                    carried += (now - hold.seen) / rate;
                }
                let carried = carried.min(1.0).max(hold.drawn);
                hold.drawn = carried;
                Hold {
                    fraction: carried,
                    ..hold.clone()
                }
            })
            .collect();
        // Stable, so two presses in the same instant keep their order.
        out.sort_by(|a, b| a.started.total_cmp(&b.started));
        out
    }

    /// The seats that joined in the last [`JOINED_SHOWN`] seconds, oldest first.
    pub fn joined(&mut self, now: f64) -> Vec<i32> {
        self.expire(now);
        let mut joined = self.joined.clone();
        joined.sort_by(|a, b| a.at.total_cmp(&b.at));
        joined.into_iter().map(|joined| joined.player).collect()
    }

    /// Whether there is anything to show.
    pub fn busy(&mut self, now: f64) -> bool {
        self.expire(now);
        !self.holds.is_empty() || !self.joined.is_empty()
    }
}

/// The index of the smallest `key`: the entry that gives way when full.
fn oldest_by<T>(items: &[T], key: impl Fn(&T) -> f64) -> usize {
    items
        .iter()
        .enumerate()
        .min_by(|a, b| key(a.1).total_cmp(&key(b.1)))
        .map_or(0, |(at, _)| at)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn near(a: f64, b: f64, tolerance: f64) -> bool {
        (a - b).abs() <= tolerance
    }

    fn fresh() -> Pairing {
        Pairing::new(1.5)
    }

    #[test]
    fn a_named_hold_survives_the_daemon_pausing_to_rescan() {
        let mut p = fresh();
        p.progress("/dev/input/event9", "Pad", 0.30, 1, 10.0);
        assert!(
            (1..=7).all(|frame| p.now(10.0 + f64::from(frame) * 0.016).len() == 1),
            "drawn through 120 ms"
        );
        let out = p.now(10.12);
        assert!(
            near(out[0].fraction, 0.30 + 0.12 / 1.5, 1e-6),
            "carried at the hold's own rate"
        );
    }

    #[test]
    fn a_named_release_is_immediate() {
        let mut p = fresh();
        p.progress("/dev/input/event9", "", 0.5, 1, 10.0);
        p.progress("/dev/input/event9", "", 0.0, 0, 10.02);
        assert!(p.now(10.02).is_empty());
    }

    #[test]
    fn a_daemon_that_dies_mid_hold_empties_the_bar_eventually() {
        let mut p = fresh();
        p.progress("/dev/input/event9", "", 0.5, 1, 10.0);
        assert_eq!(
            p.now(10.0 + STALE_NAMED - 0.01).len(),
            1,
            "still drawn inside the safety net"
        );
        assert!(
            p.now(10.0 + STALE_NAMED + 0.01).is_empty(),
            "and gone just past it"
        );
    }

    #[test]
    fn a_nameless_reading_still_ends_with_silence() {
        let mut p = fresh();
        p.progress("", "", 0.5, 0, 10.0);
        assert!(p.now(10.0 + STALE_ANONYMOUS + 0.01).is_empty());
    }

    #[test]
    fn the_first_press_is_drawn_first() {
        let mut p = fresh();
        p.progress("/dev/input/event12", "", 0.2, 2, 10.00);
        p.progress("/dev/input/event9", "", 0.1, 1, 10.30);
        let out = p.now(10.31);
        assert_eq!(out.len(), 2);
        assert_eq!(out[0].key, "/dev/input/event12");
    }

    #[test]
    fn a_fill_that_goes_backwards_is_a_new_press_at_the_back() {
        let mut p = fresh();
        p.progress("/dev/input/event9", "", 0.6, 1, 10.00);
        p.progress("/dev/input/event12", "", 0.3, 2, 10.10);
        p.progress("/dev/input/event9", "", 0.05, 2, 10.20);
        let out = p.now(10.21);
        assert_eq!(out.len(), 2);
        assert_eq!(
            out[1].key, "/dev/input/event9",
            "let go and pressed again goes behind"
        );
    }

    #[test]
    fn the_sweep_never_steps_backwards() {
        let mut p = fresh();
        p.progress("/dev/input/event9", "", 0.30, 1, 10.0);
        let ahead = p.now(10.10)[0].fraction;
        p.progress("/dev/input/event9", "", 0.36, 1, 10.10);
        assert!(p.now(10.10)[0].fraction >= ahead);
    }

    #[test]
    fn a_claim_ends_that_hold_and_shows_the_seat_for_a_moment() {
        let mut p = fresh();
        p.progress("/dev/input/event9", "", 0.99, 2, 10.0);
        p.claim("/dev/input/event9", "", 2, 10.01);
        assert!(p.now(10.02).is_empty(), "the fill that took the seat is over");
        assert_eq!(p.joined(10.02), vec![2], "and player two shows as joined");
        assert!(p.busy(10.02 + JOINED_SHOWN - 0.01), "for a moment");
        assert!(
            !p.busy(10.02 + JOINED_SHOWN + 0.01),
            "and then nothing is left to show"
        );
    }

    #[test]
    fn a_claim_leaves_the_other_holds_filling() {
        let mut p = fresh();
        p.progress("/dev/input/event9", "", 0.99, 1, 10.0);
        p.progress("/dev/input/event12", "", 0.40, 2, 10.0);
        p.claim("/dev/input/event9", "Pad", 1, 10.01);
        let out = p.now(10.02);
        assert_eq!(
            out.len(),
            1,
            "somebody else's claim does not empty a second person's ring"
        );
        assert_eq!(out[0].key, "/dev/input/event12");
        assert!(out[0].fraction >= 0.40);
    }

    #[test]
    fn a_state_drops_only_the_hold_that_became_a_seat() {
        let mut p = fresh();
        p.progress("/dev/input/event9", "", 0.5, 1, 10.0);
        p.progress("/dev/input/event12", "", 0.2, 2, 10.0);
        // The second pad's reading still says seat one: stale by a tick after
        // somebody else's claim. Identity settles it, not the number.
        p.seated("/dev/input/event3", "Other", 1);
        assert_eq!(p.now(10.01).len(), 2, "a stale seat number drops nobody");
        p.seated("/dev/input/event9", "Pad", 1);
        let out = p.now(10.01);
        assert_eq!(out.len(), 1);
        assert_eq!(
            out[0].key, "/dev/input/event12",
            "the pad now seated is dropped by its node"
        );
    }

    #[test]
    fn nothing_to_show_is_not_busy() {
        assert!(!fresh().busy(10.0));
    }

    #[test]
    fn a_ninth_hold_evicts_the_oldest() {
        let mut p = fresh();
        for i in 0..HOLDS_MAX {
            p.progress(
                &format!("/dev/input/event{i}"),
                "",
                0.1,
                i as i32 + 1,
                10.0 + i as f64 * 0.01,
            );
        }
        let at = 10.0 + HOLDS_MAX as f64 * 0.01;
        assert_eq!(p.now(at).len(), HOLDS_MAX);
        p.progress("/dev/input/eventNEW", "", 0.1, 9, at);
        let keys: Vec<String> = p.now(at).into_iter().map(|hold| hold.key).collect();
        assert_eq!(keys.len(), HOLDS_MAX);
        assert!(
            !keys.contains(&"/dev/input/event0".into()),
            "the oldest gives way"
        );
        assert!(keys.contains(&"/dev/input/eventNEW".into()), "not the newest");
    }

    #[test]
    fn a_fifth_joined_seat_evicts_the_oldest() {
        let mut p = fresh();
        for player in 1..=JOINED_MAX as i32 {
            p.claim("", "", player, 10.0 + f64::from(player) * 0.1);
        }
        let at = 10.0 + JOINED_MAX as f64 * 0.1 + 0.1;
        p.claim("", "", 9, at);
        let joined = p.joined(at);
        assert_eq!(joined.len(), JOINED_MAX);
        assert!(joined.contains(&9) && !joined.contains(&1));
    }

    #[test]
    fn a_repeated_claim_refreshes_rather_than_duplicates() {
        let mut p = fresh();
        p.claim("", "", 3, 10.0);
        p.claim("", "", 3, 10.0 + JOINED_SHOWN - 0.1);
        assert_eq!(p.joined(10.0 + JOINED_SHOWN + 0.05).len(), 1);
    }

    #[test]
    fn a_claim_with_no_player_ends_the_holds_but_seats_nobody() {
        let mut p = fresh();
        p.progress("/dev/input/event9", "", 0.5, 1, 10.0);
        p.claim("", "", 0, 10.0);
        assert!(p.now(10.0).is_empty() && p.joined(10.0).is_empty());
    }

    #[test]
    fn an_unsaid_seated_player_clears_no_anonymous_hold() {
        let mut p = fresh();
        p.progress("", "", 0.5, 0, 10.0);
        p.seated("", "", 0);
        assert_eq!(p.now(10.0).len(), 1);
    }

    #[test]
    fn a_name_keyed_hold_is_cleared_by_name() {
        let mut p = fresh();
        p.progress("", "Xbox Pad", 0.5, 1, 10.0);
        p.seated("/dev/input/event9", "Xbox Pad", 1);
        assert!(p.now(10.0).is_empty());
    }

    #[test]
    fn a_later_reading_with_no_player_keeps_the_known_seat() {
        let mut p = fresh();
        p.progress("/dev/input/event9", "", 0.2, 2, 10.0);
        p.progress("/dev/input/event9", "", 0.3, 0, 10.05);
        assert_eq!(p.now(10.05)[0].player, 2);
    }

    #[test]
    fn an_oversize_key_is_cut_on_a_character_boundary() {
        let long = "é".repeat(80); // 160 bytes, two per character
        assert_eq!(
            clip(&long).len(),
            94,
            "cut to the last whole character under the limit"
        );
        let mut p = fresh();
        p.progress(&long, "", 0.5, 1, 10.0);
        p.seated(&long, "", 1);
        assert!(p.now(10.0).is_empty(), "and matched the same way it was stored");
    }

    #[test]
    fn two_nameless_holds_at_once_share_one_ring() {
        // An older daemon gives no way to tell two nameless pads apart, so a
        // second nameless press reads as the first one's, not a second ring.
        let mut p = fresh();
        p.progress("", "", 0.2, 1, 10.0);
        p.progress("", "", 0.3, 2, 10.01);
        assert_eq!(p.now(10.01).len(), 1);
    }

    #[test]
    fn a_reading_past_full_is_drawn_full() {
        let mut p = fresh();
        p.progress("/dev/input/event9", "", 1.5, 1, 10.0);
        assert_eq!(p.now(10.0)[0].fraction, 1.0);
    }

    #[test]
    fn presses_in_the_same_instant_keep_their_order() {
        let mut p = fresh();
        p.progress("/dev/input/event9", "", 0.1, 1, 10.0);
        p.progress("/dev/input/event12", "", 0.1, 2, 10.0);
        let keys: Vec<String> = p.now(10.0).into_iter().map(|hold| hold.key).collect();
        assert_eq!(keys, ["/dev/input/event9", "/dev/input/event12"]);
        p.claim("", "", 1, 10.0);
        p.claim("", "", 2, 10.0);
        assert_eq!(p.joined(10.0), [1, 2]);
    }

    #[test]
    fn a_key_at_the_limit_is_kept_whole_and_one_over_is_cut() {
        assert_eq!(clip(&"a".repeat(KEY_MAX)).len(), KEY_MAX);
        assert_eq!(clip(&"a".repeat(KEY_MAX + 1)).len(), KEY_MAX);
    }
}
