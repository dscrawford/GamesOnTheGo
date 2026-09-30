//! The overlay's menu: the seated controllers, a rebind, the order, a held exit.
//!
//! Brought down by L + R + A held for half a second on one pad, and driven by that
//! pad alone -- the player who asked. Two people fighting over one cursor is a
//! problem for later; for now anybody else's presses are not read at all.
//!
//! What it offers:
//!
//! - **The seats**, a line of controllers: where one stands in the line is its
//!   player number, so there is no "Player 1" to read. The line ends at the
//!   last seated controller; an empty seat before it keeps its place. Left and
//!   right move along it. A tap of A on a controller walks its buttons again
//!   (`map`). A held A picks it up; left and right carry it to another seat --
//!   danstick swaps it with whoever is there (`move`) -- and letting go puts
//!   it down.
//!   X takes a controller out of its seat (danstick's `unseat`): the pad is
//!   let go, and held again to come back.
//!   Y switches that seat's game port off or on (danstick's `port`): off, the
//!   game hears nothing from that pad while its player keeps their seat and
//!   can still open this menu.
//! - **The game's controller**, below the line, where every seated player's
//!   presses show as they happen, the owner's included. The owner's presses
//!   also drive this menu; A on the controller starts a test, and from then
//!   everything they press is only theirs to try, B included, until Select
//!   held for half a second hands the menu back.
//! - **Exit**, below that, which takes A held for half a second: the game is
//!   stopped, and its saves pushed on the way out.
//!
//! Down and up step between the three. B held for half a second closes the
//! menu, and the bar slides away over a second.
//!
//! A button already down when the menu opened -- the A of the chord that
//! opened it -- is not a press until it has been let go, or the chord's own A
//! would land on whatever the cursor started on.
//!
//! Pure: it is fed the owner's controls that are down, and a clock, and says
//! what to do. Where those controls come from (danstick's `focus` events, or
//! the pad's clone through SDL) is the loop's business.

use std::collections::BTreeSet;

/// Held this long on a seated controller, A picks it up rather than rebinding.
pub const GRAB_SECONDS: f64 = 0.5;
/// Held this long on Exit, A stops the game.
pub const EXIT_SECONDS: f64 = 0.5;
/// Held this long anywhere, B closes the menu.
pub const CLOSE_SECONDS: f64 = 0.5;
/// Held this long while testing, Select gives the menu back.
pub const TEST_EXIT_SECONDS: f64 = 0.5;
/// Seats listed at most: a frame's worth.
pub const SEATS_MAX: usize = 8;

const UP: [&str; 2] = ["dpup", "leftstick_up"];
const DOWN: [&str; 2] = ["dpdown", "leftstick_down"];
const LEFT: [&str; 2] = ["dpleft", "leftstick_left"];
const RIGHT: [&str; 2] = ["dpright", "leftstick_right"];

/// What the menu asks the loop to do.
#[derive(Debug, Clone, PartialEq)]
pub enum Action {
    Close,
    Exit,
    Rebind(i32),
    /// The controller in seat `player` goes to seat `to` (swapping).
    Move {
        player: i32,
        to: i32,
    },
    /// The controller in seat `player` gives up its seat.
    Remove(i32),
    /// Seat `player`'s game port on (`open`) or off.
    Port {
        player: i32,
        open: bool,
    },
}

/// Where the cursor is, top to bottom.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Stop {
    Line,
    Tester,
    Exit,
}

/// One seat of the line, and the drawing of the pad in it (None empty).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Row {
    pub icon: Option<u8>,
    /// The game does not hear this seat (its port is off).
    pub off: bool,
}

#[derive(Debug, Clone)]
pub struct Menu {
    /// The player who opened it, and whose presses drive it.
    pub owner: i32,
    /// The line, seat 1 first, up to the last seated controller.
    rows: Vec<Row>,
    /// The seat the cursor is on in the line (0-based), kept while it is
    /// below so up comes back to it.
    along: usize,
    stop: Stop,
    /// The owner is trying their buttons: none of them drive the menu.
    testing: bool,
    back_since: Option<f64>,
    /// Down when the menu opened, and not yet let go.
    ignored: BTreeSet<String>,
    last: BTreeSet<String>,
    a_since: Option<f64>,
    b_since: Option<f64>,
    /// The seat (1-based) of the controller being carried.
    carried: Option<i32>,
    /// A said what it was for this press: a pick-up or an exit fired.
    a_used: bool,
    /// The owner's pad has been seen in its seat, and then the seat emptied:
    /// the pad was switched off or went out of range. Nobody can close the
    /// menu from a pad that is gone, and while it is open danstick may be
    /// holding that player -- so it closes itself.
    owner_seen: bool,
    owner_gone: bool,
    done: bool,
}

/// What the painter needs of the menu.
#[derive(Debug, Clone, PartialEq)]
pub struct View {
    pub owner: i32,
    pub rows: Vec<Row>,
    pub focus: usize,
    pub carried: Option<i32>,
    /// 0..1 through the hold under the cursor: picking up on a seat, or
    /// the exit.
    pub a_fill: f32,
    /// 0..1 through the hold that closes -- or, while testing, through the
    /// Select hold that stops the test.
    pub b_fill: f32,
    pub testing: bool,
}

impl Menu {
    /// Opened by `owner`, whose controls down right now are `down` (the
    /// chord's own), over the seats in `rows`. The cursor starts on the
    /// owner's own seat.
    pub fn open(owner: i32, rows: Vec<Row>, down: &BTreeSet<String>) -> Self {
        Self {
            owner,
            rows: line(rows),
            along: usize::try_from(owner - 1).unwrap_or(0),
            stop: Stop::Line,
            testing: false,
            back_since: None,
            ignored: down.clone(),
            last: down.clone(),
            a_since: None,
            b_since: None,
            carried: None,
            a_used: false,
            owner_seen: false,
            owner_gone: false,
            done: false,
        }
        .with_owner_checked()
    }

    fn with_owner_checked(mut self) -> Self {
        self.check_owner();
        self
    }

    fn check_owner(&mut self) {
        let seated = usize::try_from(self.owner - 1)
            .ok()
            .and_then(|at| self.rows.get(at))
            .is_some_and(|row| row.icon.is_some());
        if seated {
            self.owner_seen = true;
        } else if self.owner_seen {
            self.owner_gone = true;
        }
    }

    /// The seats changed (a join, a leave, a move landing): the line follows,
    /// the cursor stays on the same seat.
    pub fn seats(&mut self, rows: Vec<Row>) {
        self.rows = line(rows);
        self.check_owner();
    }

    /// Where the cursor is: a seat of the line, `rows.len()` for the game's
    /// controller -- which is also where it is while nobody has been reported
    /// seated -- or one past that for Exit.
    fn focus(&self) -> usize {
        match self.stop {
            Stop::Line if !self.rows.is_empty() => self.along.min(self.rows.len() - 1),
            Stop::Line | Stop::Tester => self.tester_row(),
            Stop::Exit => self.exit_row(),
        }
    }

    fn tester_row(&self) -> usize {
        self.rows.len()
    }

    /// danstick says the owner's pad went away, which may be before its seat
    /// empties -- or never, if the seat is kept for the pad's return.
    pub fn pad_gone(&mut self, player: i32) {
        if player == self.owner {
            self.owner_gone = true;
        }
    }

    fn exit_row(&self) -> usize {
        self.rows.len() + 1
    }

    fn seated(&self, at: usize) -> bool {
        self.rows.get(at).is_some_and(|row| row.icon.is_some())
    }

    /// One frame of the owner's controls. At most one action a frame.
    pub fn tick(&mut self, down: &BTreeSet<String>, now: f64) -> Option<Action> {
        if self.done {
            return None;
        }
        if self.owner_gone {
            self.done = true;
            return Some(Action::Close);
        }
        // The chord's own buttons count once they have been let go.
        self.ignored.retain(|control| down.contains(control));
        if self.testing {
            self.test(down, now);
            return None;
        }
        let live: BTreeSet<&String> = down.iter().filter(|c| !self.ignored.contains(*c)).collect();
        let pressed = |names: &[&str], last: &BTreeSet<String>| {
            names
                .iter()
                .any(|name| live.iter().any(|c| c == name) && !last.contains(*name))
        };
        let up = pressed(&UP, &self.last);
        let down_pressed = pressed(&DOWN, &self.last);
        let left = pressed(&LEFT, &self.last);
        let right = pressed(&RIGHT, &self.last);
        let a_down = live.iter().any(|c| *c == "a");
        let b_down = live.iter().any(|c| *c == "b");
        let y_pressed = pressed(&["y"], &self.last);
        let x_pressed = pressed(&["x"], &self.last);
        self.last = down.clone();

        let mut action = None;
        // B: held, it closes.
        match (b_down, self.b_since) {
            (true, None) => self.b_since = Some(now),
            (true, Some(since)) if now - since >= CLOSE_SECONDS => {
                self.done = true;
                return Some(Action::Close);
            }
            (false, _) => self.b_since = None,
            _ => {}
        }
        // Left and right: along the line, or the controller being carried.
        let step: i32 = if left {
            -1
        } else if right {
            1
        } else {
            0
        };
        if let Some(seat) = self.carried.filter(|_| step != 0) {
            let to = seat + step;
            if to >= 1 && to as usize <= self.rows.len() {
                self.carried = Some(to);
                self.along = (to - 1) as usize;
                action = Some(Action::Move { player: seat, to });
            }
        } else if self.carried.is_none() {
            let was = self.focus();
            if step != 0 && self.stop == Stop::Line && !self.rows.is_empty() {
                let last = self.rows.len() as i32 - 1;
                self.along = (self.focus() as i32 + step).clamp(0, last) as usize;
            }
            if down_pressed {
                self.stop = match self.stop {
                    Stop::Line => Stop::Tester,
                    Stop::Tester | Stop::Exit => Stop::Exit,
                };
            } else if up {
                self.stop = match self.stop {
                    Stop::Exit => Stop::Tester,
                    Stop::Tester | Stop::Line => Stop::Line,
                };
            }
            // A hold that was under the old place is not under the new one.
            if self.focus() != was {
                self.a_since = self.a_since.map(|_| now);
            }
        }
        // Y: the seat's game port, off or on.
        if y_pressed && self.carried.is_none() && action.is_none() && self.seated(self.focus()) {
            let off = self.rows[self.focus()].off;
            action = Some(Action::Port {
                player: self.focus() as i32 + 1,
                open: off,
            });
        }
        if x_pressed && self.carried.is_none() && action.is_none() && self.seated(self.focus()) {
            action = Some(Action::Remove(self.focus() as i32 + 1));
        }
        // A: a tap rebinds, a hold picks up (on a seat) or exits (on Exit).
        match (a_down, self.a_since) {
            (true, None) => {
                self.a_since = Some(now);
                self.a_used = false;
            }
            (true, Some(since)) if !self.a_used => {
                let held = now - since;
                if self.focus() == self.exit_row() && held >= EXIT_SECONDS {
                    self.a_used = true;
                    self.done = true;
                    return Some(Action::Exit);
                }
                if self.seated(self.focus()) && self.carried.is_none() && held >= GRAB_SECONDS {
                    self.a_used = true;
                    self.carried = Some(self.focus() as i32 + 1);
                }
            }
            (false, Some(_)) => {
                if !self.a_used && self.focus() == self.tester_row() {
                    self.a_since = None;
                    self.start_test(down);
                    return action;
                }
                let tapped = !self.a_used && self.carried.is_none() && self.seated(self.focus());
                self.a_since = None;
                self.carried = None;
                if tapped && action.is_none() {
                    self.done = true;
                    action = Some(Action::Rebind(self.focus() as i32 + 1));
                }
            }
            _ => {}
        }
        action
    }

    /// The owner's buttons are theirs to try: nothing is read but Select,
    /// held to stop.
    fn start_test(&mut self, down: &BTreeSet<String>) {
        self.testing = true;
        self.b_since = None;
        self.back_since = None;
        self.ignored = down.clone();
    }

    fn test(&mut self, down: &BTreeSet<String>, now: f64) {
        let back = down.contains("back") && !self.ignored.contains("back");
        match (back, self.back_since) {
            (true, None) => self.back_since = Some(now),
            (true, Some(since)) if now - since >= TEST_EXIT_SECONDS => {
                self.testing = false;
                self.back_since = None;
                // Nothing held through the test presses anything after it.
                self.ignored = down.clone();
            }
            (false, _) => self.back_since = None,
            _ => {}
        }
        self.last = down.clone();
    }

    pub fn view(&self, now: f64) -> View {
        let a_fill = match self.a_since {
            Some(since) if !self.a_used => {
                let span = if self.focus() == self.exit_row() {
                    EXIT_SECONDS
                } else if self.seated(self.focus()) {
                    GRAB_SECONDS
                } else {
                    return self.view_with(0.0, now);
                };
                ((now - since) / span).clamp(0.0, 1.0) as f32
            }
            _ => 0.0,
        };
        self.view_with(a_fill, now)
    }

    fn view_with(&self, a_fill: f32, now: f64) -> View {
        View {
            owner: self.owner,
            rows: self.rows.clone(),
            focus: self.focus(),
            carried: self.carried,
            a_fill: if self.testing { 0.0 } else { a_fill },
            b_fill: if self.testing {
                self.back_since.map_or(0.0, |since| {
                    ((now - since) / TEST_EXIT_SECONDS).clamp(0.0, 1.0) as f32
                })
            } else {
                self.b_since.map_or(0.0, |since| {
                    ((now - since) / CLOSE_SECONDS).clamp(0.0, 1.0) as f32
                })
            },
            testing: self.testing,
        }
    }
}

/// The seats as a line: at most `SEATS_MAX`, ending at the last seated
/// controller, so where each stands is its player number.
fn line(rows: Vec<Row>) -> Vec<Row> {
    let mut rows: Vec<Row> = rows.into_iter().take(SEATS_MAX).collect();
    while rows.last().is_some_and(|row| row.icon.is_none()) {
        rows.pop();
    }
    rows
}

/// The controls danstick reports for a held player (its `focus` events), for
/// the menu to read instead of the clone the game is also reading. Empty until
/// danstick says anything -- an older daemon never will, and the loop reads
/// the clone as well, so the menu works either way.
#[derive(Debug, Clone, Default)]
pub struct Focused {
    pub player: i32,
    pub down: BTreeSet<String>,
    /// Left stick x, y, right stick x, y: -1..1, y down.
    pub sticks: [f32; 4],
    /// danstick said it does not know `focus`: it is not asked again.
    pub refused: bool,
}

impl Focused {
    pub fn apply(&mut self, event: &crate::events::Event) {
        if let crate::events::Event::Error { message } = event
            && message == "unknown command \"focus\""
        {
            self.refused = true;
        }
        // A stick is said as its axes; the menu steps on its directions, as
        // a clone's stick reads (pressing.rs's AXIS_ON).
        if let crate::events::Event::FocusStick { player, stick, x, y } = event {
            if *player != self.player {
                self.player = *player;
                self.clear();
            }
            let at = if stick == "right" { 2 } else { 0 };
            self.sticks[at] = *x;
            self.sticks[at + 1] = *y;
            let on = crate::pressing::AXIS_ON;
            let ways = [
                ("left", *x <= -on),
                ("right", *x >= on),
                ("up", *y <= -on),
                ("down", *y >= on),
            ];
            for (way, pushed) in ways {
                let control = format!("{stick}stick_{way}");
                if pushed {
                    self.down.insert(control);
                } else {
                    self.down.remove(&control);
                }
            }
        }
        if let crate::events::Event::Focus {
            player,
            control,
            down,
        } = event
        {
            if *player != self.player {
                self.player = *player;
                self.clear();
            }
            if *down {
                self.down.insert(control.clone());
            } else {
                self.down.remove(control);
            }
        }
    }

    /// What `owner` has down, as danstick says; nothing for anybody else.
    pub fn of(&self, owner: i32) -> BTreeSet<String> {
        if self.player == owner {
            self.down.clone()
        } else {
            BTreeSet::new()
        }
    }

    /// `owner`'s pad as the controller test draws it.
    pub fn held(&self, owner: i32) -> crate::pressing::Held {
        crate::pressing::Held {
            controls: self.of(owner),
            sticks: if self.player == owner {
                self.sticks
            } else {
                [0.0; 4]
            },
        }
    }

    pub fn clear(&mut self) {
        self.down.clear();
        self.sticks = [0.0; 4];
    }
}

impl crate::events::Listen for Focused {
    fn apply(&mut self, event: &crate::events::Event) {
        Focused::apply(self, event);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn held(names: &[&str]) -> BTreeSet<String> {
        names.iter().map(|n| n.to_string()).collect()
    }

    fn room(seated: usize, slots: usize) -> Vec<Row> {
        (0..slots)
            .map(|at| Row {
                icon: (at < seated).then_some(at as u8),
                off: false,
            })
            .collect()
    }

    /// Feed `names` held from `from` to `to` in 50 ms steps; the actions.
    fn hold(menu: &mut Menu, names: &[&str], from: f64, to: f64) -> Vec<Action> {
        let mut out = Vec::new();
        let mut t = from;
        while t <= to + 1e-9 {
            out.extend(menu.tick(&held(names), t));
            t += 0.05;
        }
        out
    }

    /// A press and its release: one step.
    fn tap(menu: &mut Menu, name: &str, at: f64) {
        menu.tick(&held(&[name]), at);
        menu.tick(&held(&[]), at + 0.02);
    }

    fn opened(owner: i32) -> Menu {
        let mut menu = Menu::open(owner, room(2, 4), &held(&["a", "leftshoulder", "rightshoulder"]));
        // The chord is let go.
        assert_eq!(menu.tick(&held(&[]), 0.0), None);
        menu
    }

    #[test]
    fn the_chords_own_a_is_not_a_press_on_the_row_it_opened_on() {
        let mut menu = Menu::open(1, room(2, 4), &held(&["a", "leftshoulder", "rightshoulder"]));
        assert!(
            hold(&mut menu, &["a"], 0.0, 2.0).is_empty(),
            "still the chord's A: no rebind, no pick-up"
        );
        assert_eq!(menu.tick(&held(&[]), 2.1), None, "and letting it go is not a tap");
        assert_eq!(menu.view(2.1).focus, 0, "the cursor starts on the owner's seat");
    }

    #[test]
    fn a_tap_on_a_seated_controller_rebinds_it() {
        let mut menu = opened(2);
        assert_eq!(menu.view(0.0).focus, 1, "player two's own seat");
        hold(&mut menu, &["a"], 0.1, 0.2);
        assert_eq!(menu.tick(&held(&[]), 0.25), Some(Action::Rebind(2)));
    }

    #[test]
    fn the_line_ends_at_the_last_seated_controller() {
        // Four seats, two taken: the line is two controllers long.
        assert_eq!(opened(1).view(0.0).rows.len(), 2);
        // A gap keeps its place, or the third controller would read as second.
        let mut rows = room(3, 4);
        rows[1].icon = None;
        let menu = Menu::open(1, rows, &held(&[]));
        let icons: Vec<_> = menu.view(0.0).rows.iter().map(|row| row.icon).collect();
        assert_eq!(icons, [Some(0), None, Some(2)]);
    }

    #[test]
    fn a_hold_picks_a_controller_up_and_right_carries_it_to_the_next_seat() {
        let mut menu = Menu::open(1, room(3, 4), &held(&[]));
        assert!(hold(&mut menu, &["a"], 0.1, 0.7).is_empty());
        assert_eq!(menu.view(0.7).carried, Some(1), "picked up after half a second");
        assert_eq!(
            menu.tick(&held(&["a", "dpright"]), 0.75),
            Some(Action::Move { player: 1, to: 2 })
        );
        assert_eq!(
            menu.tick(&held(&["a"]), 0.8),
            None,
            "a direction is a press, not a repeat"
        );
        assert_eq!(
            menu.tick(&held(&["a", "dpright"]), 0.85),
            Some(Action::Move { player: 2, to: 3 })
        );
        assert_eq!(menu.view(0.85).focus, 2, "the cursor goes with it");
        assert_eq!(
            menu.tick(&held(&[]), 0.9),
            None,
            "letting go puts it down, and is not a tap"
        );
        assert_eq!(menu.view(0.9).carried, None);
        assert_eq!(
            menu.tick(&held(&["dpleft"]), 1.0),
            None,
            "a plain left moves only the cursor"
        );
        assert_eq!(menu.view(1.0).focus, 1);
    }

    #[test]
    fn a_carried_controller_stops_at_the_end_of_the_line() {
        let mut menu = opened(1);
        hold(&mut menu, &["a"], 0.1, 0.7);
        let mut moves = Vec::new();
        for (i, t) in [0.75, 0.85, 0.95, 1.05, 1.15].iter().enumerate() {
            let way = if i % 2 == 0 {
                &["a", "dpright"][..]
            } else {
                &["a"][..]
            };
            moves.extend(menu.tick(&held(way), *t));
        }
        assert_eq!(moves, [Action::Move { player: 1, to: 2 }]);
        assert_eq!(
            menu.view(1.2).carried,
            Some(2),
            "two controllers, nowhere past the second"
        );
    }

    #[test]
    fn down_is_the_controller_then_exit_and_up_is_back_to_the_same_seat() {
        let mut menu = opened(1);
        tap(&mut menu, "dpright", 0.1);
        tap(&mut menu, "dpdown", 0.2);
        assert_eq!(
            menu.view(0.3).focus,
            2,
            "past the two seats: the game's controller"
        );
        tap(&mut menu, "dpdown", 0.3);
        assert_eq!(menu.view(0.4).focus, 3, "and Exit under it");
        tap(&mut menu, "dpleft", 0.4);
        assert_eq!(menu.view(0.5).focus, 3, "left and right are the line's");
        tap(&mut menu, "dpup", 0.5);
        tap(&mut menu, "dpup", 0.6);
        assert_eq!(menu.view(0.7).focus, 1, "back on player two");
    }

    #[test]
    fn a_on_the_controller_tests_it_until_select_is_held_half_a_second() {
        let mut menu = opened(1);
        tap(&mut menu, "dpdown", 0.1);
        tap(&mut menu, "a", 0.2);
        assert!(menu.view(0.3).testing);
        // Everything is the owner's to try: B held does not close, arrows do
        // not move, A does not rebind.
        assert!(hold(&mut menu, &["b"], 0.3, 1.5).is_empty());
        tap(&mut menu, "dpdown", 1.6);
        tap(&mut menu, "a", 1.7);
        assert_eq!(menu.view(1.8).focus, 2);
        assert!(hold(&mut menu, &["back"], 2.0, 2.3).is_empty());
        menu.tick(&held(&[]), 2.35);
        assert!(menu.view(2.4).testing, "a short Select is a button to try");
        hold(&mut menu, &["back", "b"], 2.5, 3.1);
        assert!(!menu.view(3.1).testing, "held half a second, the menu is back");
        assert!(
            hold(&mut menu, &["back", "b"], 3.2, 4.5).is_empty(),
            "a B held through the end of the test is not a close"
        );
    }

    #[test]
    fn exit_takes_a_held_for_half_a_second_and_a_tap_does_nothing() {
        let mut menu = opened(1);
        tap(&mut menu, "dpdown", 0.1);
        tap(&mut menu, "dpdown", 0.15);
        hold(&mut menu, &["a"], 0.7, 0.8);
        assert_eq!(menu.tick(&held(&[]), 0.85), None, "a tap on Exit is nothing");
        let fired = hold(&mut menu, &["a"], 1.0, 2.1);
        assert_eq!(fired, [Action::Exit], "held half a second, it exits once");
    }

    #[test]
    fn b_held_for_half_a_second_closes_and_a_tap_does_not() {
        let mut menu = opened(1);
        hold(&mut menu, &["b"], 0.1, 0.3);
        menu.tick(&held(&[]), 0.35);
        assert!(menu.view(0.35).b_fill == 0.0);
        let closed = hold(&mut menu, &["b"], 0.5, 1.6);
        assert_eq!(closed, [Action::Close]);
        assert!(hold(&mut menu, &["a"], 2.0, 3.0).is_empty(), "closed is closed");
    }

    #[test]
    fn an_empty_seat_neither_rebinds_nor_picks_up() {
        let mut rows = room(3, 4);
        rows[1].icon = None;
        let mut menu = Menu::open(1, rows, &held(&[]));
        tap(&mut menu, "dpright", 0.1);
        assert_eq!(menu.view(0.3).focus, 1, "seat two is empty");
        assert!(hold(&mut menu, &["a"], 0.3, 1.5).is_empty());
        assert_eq!(menu.tick(&held(&[]), 1.6), None);
    }

    #[test]
    fn a_menu_whose_pad_went_away_closes_itself() {
        let mut menu = opened(1);
        assert_eq!(menu.tick(&held(&[]), 0.1), None);
        // Player one's seat empties: the pad was switched off.
        menu.seats(room(0, 4));
        assert_eq!(menu.tick(&held(&[]), 0.2), Some(Action::Close));
        assert_eq!(menu.tick(&held(&["a"]), 0.3), None, "and stays closed");
    }

    #[test]
    fn a_menu_closes_when_danstick_says_its_pad_went_though_the_seat_is_kept() {
        let mut menu = opened(2);
        menu.pad_gone(1);
        assert_eq!(menu.tick(&held(&[]), 0.1), None, "somebody else's pad");
        menu.pad_gone(2);
        assert_eq!(menu.tick(&held(&[]), 0.2), Some(Action::Close));
    }

    #[test]
    fn x_takes_a_controller_out_of_its_seat() {
        let mut menu = opened(1);
        assert_eq!(menu.tick(&held(&["x"]), 0.1), Some(Action::Remove(1)));
        assert_eq!(menu.tick(&held(&["x"]), 0.15), None, "a press, not a repeat");
        tap(&mut menu, "dpdown", 0.2);
        assert_eq!(menu.tick(&held(&["x"]), 0.3), None, "Exit is nobody's seat");
    }

    #[test]
    fn a_seat_not_yet_reported_is_not_a_pad_gone() {
        // Opened before danstick's first `state` says who is seated: nothing
        // was ever seen there, so nothing has gone.
        let mut menu = Menu::open(1, room(0, 4), &held(&[]));
        assert_eq!(menu.tick(&held(&[]), 0.1), None);
        menu.seats(room(1, 4));
        menu.seats(room(1, 4));
        assert_eq!(menu.tick(&held(&[]), 0.2), None);
    }

    #[test]
    fn danstick_saying_a_stick_as_axes_is_a_direction_the_menu_steps_on() {
        use crate::events::Event;
        let mut focused = Focused::default();
        let stick = |y: f32| Event::FocusStick {
            player: 1,
            stick: "left".into(),
            x: 0.0,
            y,
        };
        focused.apply(&stick(0.9));
        assert!(focused.of(1).contains("leftstick_down"));
        assert_eq!(
            focused.held(1).sticks,
            [0.0, 0.9, 0.0, 0.0],
            "and where, for the test's dot"
        );
        assert!(focused.of(2).is_empty(), "only the player it is about");
        focused.apply(&stick(0.1));
        assert!(focused.of(1).is_empty(), "back in the middle is let go");
    }

    #[test]
    fn y_switches_a_seats_game_port_off_and_back_on() {
        let mut menu = opened(1);
        assert_eq!(
            menu.tick(&held(&["y"]), 0.1),
            Some(Action::Port {
                player: 1,
                open: false
            })
        );
        assert_eq!(menu.tick(&held(&["y"]), 0.15), None, "a press, not a repeat");
        menu.tick(&held(&[]), 0.2);
        let mut rows = room(2, 4);
        rows[0].off = true;
        menu.seats(rows);
        assert_eq!(
            menu.tick(&held(&["y"]), 0.3),
            Some(Action::Port {
                player: 1,
                open: true
            }),
            "an off seat is switched back on"
        );
        menu.tick(&held(&[]), 0.35);
        tap(&mut menu, "dpdown", 0.4);
        assert_eq!(menu.tick(&held(&["y"]), 0.6), None, "Exit has no port to switch");
    }

    #[test]
    fn the_fill_under_the_cursor_is_the_hold_it_is_timing() {
        let mut menu = opened(1);
        hold(&mut menu, &["a"], 0.1, 0.35);
        let fill = menu.view(0.35).a_fill;
        assert!((fill - 0.5).abs() < 0.11, "{fill}: half way to a pick-up");
    }
}
