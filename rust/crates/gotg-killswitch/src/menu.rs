//! The overlay's menu: the seated controllers, a rebind, the order, a held exit.
//!
//! Brought down by L + R + Select held for half a second on one pad, and driven by that
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
//!   presses show as they happen, the owner's included. A cursor stands on
//!   the whole drawing, or -- moved by the d-pad -- on one of its buttons.
//!   On the whole drawing a tap of A walks every button of the owner's pad
//!   again (`map`), A held for half a second starts a test, and X resets the
//!   whole walk (`forget`). On a button a tap of A walks that button alone
//!   (`bind`), Y adds a second input to it (`bind` with `add`), and X resets
//!   it. In a test everything the owner presses is only theirs to try, B
//!   included, until Select held for half a second hands the menu back.
//! - **Load a save**, for a game that keeps its saves to itself: A lists them
//!   (the client is asked, `gotg saves list --lines`), up and down choose, A
//!   asks whether to load the one chosen, and A held for half a second loads
//!   it -- the game stops, what was played is pushed, the save is put back and
//!   the game starts again with everybody still in their seats. B steps back
//!   out, one level at a time.
//! - **Save states**, for an emulator that has them: A lists the slots, A
//!   held saves into the one chosen, Y held loads it; the menu closes after.
//! - **Exit**, below that, which takes A held for half a second: the game is
//!   stopped, and its saves pushed on the way out.
//!
//! Down and up step between them. B held for half a second closes the
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

use crate::hover::{self, Hover, Target, Way};

/// Held this long on a seated controller, A picks it up rather than rebinding.
pub const GRAB_SECONDS: f64 = 0.5;
/// Held this long on the game's controller, A starts a test rather than a walk.
pub const TRY_SECONDS: f64 = 0.5;
/// How long the bar says a reset needs a newer danstick.
pub const NOTICE_SECONDS: f64 = 2.0;
/// Held this long on Exit, A stops the game.
pub const EXIT_SECONDS: f64 = 0.5;
/// Held this long on the prompt, A loads the save chosen.
pub const LOAD_SECONDS: f64 = 0.5;
/// Held this long on a slot, A saves a state into it and Y loads it.
pub const STATE_SECONDS: f64 = 0.5;
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
    /// One control of `player`'s pad walked again; `add` keeps what it had
    /// and takes the press as a second input.
    Bind {
        player: i32,
        control: usize,
        add: bool,
    },
    /// `player`'s binding for this control, or the whole walk, dropped.
    Forget {
        player: i32,
        control: Option<usize>,
    },
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
    /// The saves are wanted: the loop asks the client and says how many with
    /// `saves_listed`.
    ListSaves,
    /// Load the save at this place in the list.
    Load(usize),
    /// The slots are wanted: the loop asks the client and answers with
    /// `states_listed`.
    ListStates,
    /// A state saved into, or loaded from, the slot at this place in the list.
    State {
        at: usize,
        save: bool,
    },
}

/// The saves list -- or the save-state slots -- while it is open.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Browse {
    pub listed: Listed,
    pub selected: usize,
    /// The prompt is up: A held loads, B steps back to the list.
    pub confirming: bool,
    /// The slots rather than the saves.
    pub states: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Listed {
    Loading,
    /// The client could not say.
    Failed,
    Ready(usize),
}

/// Where the cursor is, top to bottom.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Stop {
    Line,
    Tester,
    Saves,
    States,
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
    /// Y held on a slot, to load it.
    y_since: Option<f64>,
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
    /// The game keeps its saves to itself, so a save can be loaded.
    offer_saves: bool,
    offer_states: bool,
    /// A bit per listed slot that holds a state: what Y can load.
    loadable_slots: u64,
    browse: Option<Browse>,
    /// The game's controller as drawn: which controls can be hovered, and
    /// where the cursor is among them while the row is the tester.
    targets: Vec<Target>,
    hover: Hover,
    /// Y went down on a hovered control; it fires when let go, so the press
    /// is over before danstick listens for the one to add.
    y_armed: bool,
    /// Until when the bar says a reset needs a newer danstick.
    notice_until: Option<f64>,
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
    /// There is a saves row, between the tester and Exit.
    pub saves_row: bool,
    /// And a save-states row, after it.
    pub states_row: bool,
    /// The saves list or the slots, while one is open.
    pub saves: Option<Browse>,
    /// The control under the cursor on the game's controller; None is the
    /// whole drawing.
    pub hover: Option<usize>,
    /// The bar is saying a reset needs a newer danstick.
    pub notice: bool,
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
            y_since: None,
            carried: None,
            a_used: false,
            owner_seen: false,
            owner_gone: false,
            done: false,
            offer_saves: false,
            offer_states: false,
            loadable_slots: 0,
            browse: None,
            targets: Vec::new(),
            hover: Hover::Whole,
            y_armed: false,
            notice_until: None,
        }
        .with_owner_checked()
    }

    /// With a saves row: for a game whose saves are its own.
    pub fn with_saves(mut self) -> Self {
        self.offer_saves = true;
        self
    }

    pub fn with_states(mut self) -> Self {
        self.offer_states = true;
        self
    }

    /// With the controls the drawing places, for the cursor to walk.
    pub fn with_targets(mut self, targets: Vec<Target>) -> Self {
        self.targets = targets;
        self
    }

    /// danstick does not know `forget`: said on the bar for a while.
    pub fn refused_reset(&mut self, now: f64) {
        self.notice_until = Some(now + NOTICE_SECONDS);
    }

    /// What the client said: how many saves, or None when it could not say.
    pub fn saves_listed(&mut self, count: Option<usize>) {
        if let Some(browse) = self.browse.as_mut().filter(|b| !b.states) {
            browse.listed = count.map_or(Listed::Failed, Listed::Ready);
            browse.selected = 0;
            browse.confirming = false;
        }
    }

    /// What the client said of the slots: how many, and a bit per slot that
    /// holds a state; None when it could not say.
    pub fn states_listed(&mut self, listed: Option<(usize, u64)>) {
        if let Some(browse) = self.browse.as_mut().filter(|b| b.states) {
            browse.listed = listed.map_or(Listed::Failed, |(count, _)| Listed::Ready(count));
            browse.selected = 0;
            self.loadable_slots = listed.map_or(0, |(_, loadable)| loadable);
        }
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
            Stop::Saves => self.saves_row(),
            Stop::States => self.states_row(),
            Stop::Exit => self.exit_row(),
        }
    }

    fn saves_row(&self) -> usize {
        self.rows.len() + 1
    }

    fn states_row(&self) -> usize {
        self.rows.len() + 1 + usize::from(self.offer_saves)
    }

    /// The stops from the line down, as offered.
    fn stops(&self) -> Vec<Stop> {
        let mut stops = vec![Stop::Line, Stop::Tester];
        if self.offer_saves {
            stops.push(Stop::Saves);
        }
        if self.offer_states {
            stops.push(Stop::States);
        }
        stops.push(Stop::Exit);
        stops
    }

    /// The stop `by` steps down (up, negative) from this one, no further
    /// than the ends.
    fn step_stop(&self, from: Stop, by: isize) -> Stop {
        let stops = self.stops();
        let at = stops.iter().position(|&s| s == from).unwrap_or(0);
        stops[at.saturating_add_signed(by).min(stops.len() - 1)]
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
        self.rows.len() + 1 + usize::from(self.offer_saves) + usize::from(self.offer_states)
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
        if self.browse.is_some() {
            return self.browse_tick(down, now);
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
            let was = (self.focus(), self.hover);
            if step != 0 && self.stop == Stop::Line && !self.rows.is_empty() {
                let last = self.rows.len() as i32 - 1;
                self.along = (self.focus() as i32 + step).clamp(0, last) as usize;
            }
            // On the tester the cursor walks the drawing's controls; up and
            // down leave the row only from the whole drawing.
            let on_tester = self.stop == Stop::Tester;
            let way = if on_tester && left {
                Some(Way::Left)
            } else if on_tester && right {
                Some(Way::Right)
            } else if on_tester && up && self.hover != Hover::Whole {
                Some(Way::Up)
            } else if on_tester && down_pressed && self.hover != Hover::Whole {
                Some(Way::Down)
            } else {
                None
            };
            if let Some(way) = way {
                self.hover = hover::step(&self.targets, self.hover, way);
            } else if down_pressed {
                self.stop = self.step_stop(self.stop, 1);
            } else if up {
                self.stop = self.step_stop(self.stop, -1);
            }
            if self.stop != Stop::Tester {
                self.hover = Hover::Whole;
            }
            // A hold that was under the old place is not under the new one.
            if (self.focus(), self.hover) != was {
                self.a_since = self.a_since.map(|_| now);
                self.y_armed = false;
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
        // On the game's controller: X resets what is hovered, Y -- once let
        // go -- adds an input to a hovered button.
        let on_tester = self.stop == Stop::Tester && self.carried.is_none();
        if on_tester && x_pressed && action.is_none() {
            action = Some(Action::Forget {
                player: self.owner,
                control: self.hover.control(),
            });
        }
        let y_down = live.iter().any(|c| *c == "y");
        if on_tester && y_pressed && self.hover != Hover::Whole {
            self.y_armed = true;
        } else if self.y_armed && !y_down {
            self.y_armed = false;
            if let (true, Some(control)) = (on_tester && action.is_none(), self.hover.control()) {
                self.done = true;
                return Some(Action::Bind {
                    player: self.owner,
                    control,
                    add: true,
                });
            }
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
                if self.stop == Stop::Tester && self.hover == Hover::Whole && held >= TRY_SECONDS {
                    self.a_used = true;
                    self.a_since = None;
                    self.start_test(down);
                    return action;
                }
            }
            (false, Some(_)) => {
                if !self.a_used && self.stop == Stop::Tester {
                    self.a_since = None;
                    if action.is_some() {
                        return action;
                    }
                    self.done = true;
                    return Some(match self.hover {
                        Hover::Whole => Action::Rebind(self.owner),
                        Hover::Control(control) => Action::Bind {
                            player: self.owner,
                            control,
                            add: false,
                        },
                    });
                }
                if !self.a_used && matches!(self.stop, Stop::Saves | Stop::States) {
                    self.a_since = None;
                    self.b_since = None;
                    let states = self.stop == Stop::States;
                    self.browse = Some(Browse {
                        listed: Listed::Loading,
                        selected: 0,
                        confirming: false,
                        states,
                    });
                    return Some(if states {
                        Action::ListStates
                    } else {
                        Action::ListSaves
                    });
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

    /// The saves list: up and down choose, A asks, A held loads, B steps
    /// back. Nothing else is read, B's hold to close included -- a tap of B
    /// here is a step back, and must not also be the start of a close.
    fn browse_tick(&mut self, down: &BTreeSet<String>, now: f64) -> Option<Action> {
        let live: BTreeSet<&String> = down.iter().filter(|c| !self.ignored.contains(*c)).collect();
        let pressed = |names: &[&str], last: &BTreeSet<String>| {
            names
                .iter()
                .any(|name| live.iter().any(|c| c == name) && !last.contains(*name))
        };
        let up = pressed(&UP, &self.last);
        let down_pressed = pressed(&DOWN, &self.last);
        let b = pressed(&["b"], &self.last);
        let a_pressed = pressed(&["a"], &self.last);
        let a_down = live.iter().any(|c| *c == "a");
        let y_down = live.iter().any(|c| *c == "y");
        self.last = down.clone();
        let loadable_slots = self.loadable_slots;
        let browse = self.browse.as_mut()?;

        if b {
            self.a_since = None;
            self.y_since = None;
            if browse.confirming {
                browse.confirming = false;
            } else {
                self.browse = None;
            }
            return None;
        }
        let Listed::Ready(count) = browse.listed else {
            self.a_since = None;
            self.y_since = None;
            return None;
        };
        if count == 0 {
            return None;
        }
        if browse.states {
            // No prompt: the hold is the asking, as it is on Exit. A hold
            // carried onto another slot starts over there.
            let was = browse.selected;
            if down_pressed {
                browse.selected = (browse.selected + 1).min(count - 1);
            } else if up {
                browse.selected = browse.selected.saturating_sub(1);
            }
            if browse.selected != was {
                self.a_since = None;
                self.y_since = None;
            }
            let at = browse.selected;
            let loadable = loadable_slots & (1 << at.min(63)) != 0;
            if held_for(a_down, &mut self.a_since, now, STATE_SECONDS) {
                self.done = true;
                return Some(Action::State { at, save: true });
            }
            if held_for(y_down && loadable, &mut self.y_since, now, STATE_SECONDS) {
                self.done = true;
                return Some(Action::State { at, save: false });
            }
            return None;
        }
        if !browse.confirming {
            if down_pressed {
                browse.selected = (browse.selected + 1).min(count - 1);
            } else if up {
                browse.selected = browse.selected.saturating_sub(1);
            } else if a_pressed {
                browse.confirming = true;
                // This press opened the prompt; the load takes a press of its own.
                self.ignored.insert("a".to_owned());
            }
            return None;
        }
        if held_for(a_down, &mut self.a_since, now, LOAD_SECONDS) {
            self.done = true;
            return Some(Action::Load(browse.selected));
        }
        None
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
        if let Some(browse) = self.browse {
            let seconds = if browse.states {
                Some(STATE_SECONDS)
            } else if browse.confirming {
                Some(LOAD_SECONDS)
            } else {
                None
            };
            let a_fill = match (self.a_since.or(self.y_since), seconds) {
                (Some(since), Some(seconds)) => ((now - since) / seconds).clamp(0.0, 1.0) as f32,
                _ => 0.0,
            };
            return self.view_with(a_fill, now);
        }
        let a_fill = match self.a_since {
            Some(since) if !self.a_used => {
                let span = if self.focus() == self.exit_row() {
                    EXIT_SECONDS
                } else if self.seated(self.focus()) {
                    GRAB_SECONDS
                } else if self.stop == Stop::Tester && self.hover == Hover::Whole {
                    TRY_SECONDS
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
            saves_row: self.offer_saves,
            states_row: self.offer_states,
            saves: self.browse,
            hover: self.hover.control(),
            notice: self.notice_until.is_some_and(|until| now < until),
        }
    }
}

/// A hold timed on `since`: true once it has lasted `seconds`.
fn held_for(held: bool, since: &mut Option<f64>, now: f64, seconds: f64) -> bool {
    match (held, *since) {
        (true, None) => {
            *since = Some(now);
            false
        }
        (true, Some(from)) => now - from >= seconds,
        (false, _) => {
            *since = None;
            false
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
        if event.refuses("focus") {
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
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::events::Listen;

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

    // --- loading a save ------------------------------------------------------

    fn with_saves(owner: i32) -> Menu {
        let mut menu =
            Menu::open(owner, room(2, 4), &held(&["a", "leftshoulder", "rightshoulder"])).with_saves();
        assert_eq!(menu.tick(&held(&[]), 0.0), None);
        menu
    }

    /// Down from the line to the saves row: the tester, then the saves.
    fn to_saves(menu: &mut Menu) {
        tap(menu, "dpdown", 0.1);
        tap(menu, "dpdown", 0.2);
    }

    #[test]
    fn the_saves_row_is_between_the_tester_and_exit_only_when_offered() {
        let mut menu = with_saves(1);
        to_saves(&mut menu);
        let view = menu.view(0.3);
        assert!(view.saves_row);
        assert_eq!(view.focus, 3, "two seats, the tester, then the saves");
        tap(&mut menu, "dpdown", 0.4);
        assert_eq!(menu.view(0.5).focus, 4, "and Exit under it");

        let mut plain = opened(1);
        to_saves(&mut plain);
        assert!(!plain.view(0.3).saves_row);
        assert_eq!(
            plain.view(0.3).focus,
            3,
            "no saves row: the second step down is Exit"
        );
    }

    #[test]
    fn a_on_the_saves_row_asks_for_the_list_and_shows_it_loading() {
        let mut menu = with_saves(1);
        to_saves(&mut menu);
        menu.tick(&held(&["a"]), 0.3);
        assert_eq!(menu.tick(&held(&[]), 0.32), Some(Action::ListSaves));
        assert_eq!(
            menu.view(0.4).saves,
            Some(Browse {
                listed: Listed::Loading,
                selected: 0,
                confirming: false,
                states: false,
            })
        );
    }

    fn browsing(count: usize) -> Menu {
        let mut menu = with_saves(1);
        to_saves(&mut menu);
        tap(&mut menu, "a", 0.3);
        menu.saves_listed(Some(count));
        menu
    }

    #[test]
    fn up_and_down_walk_the_saves_and_stop_at_the_ends() {
        let mut menu = browsing(3);
        tap(&mut menu, "dpdown", 0.5);
        tap(&mut menu, "dpdown", 0.6);
        tap(&mut menu, "dpdown", 0.7);
        assert_eq!(menu.view(0.8).saves.expect("the list is open").selected, 2);
        tap(&mut menu, "dpup", 0.9);
        assert_eq!(menu.view(1.0).saves.expect("the list is open").selected, 1);
    }

    #[test]
    fn a_asks_first_and_a_held_half_a_second_loads() {
        let mut menu = browsing(3);
        tap(&mut menu, "dpdown", 0.5);
        tap(&mut menu, "a", 0.6);
        assert!(
            menu.view(0.7).saves.expect("the list is open").confirming,
            "the prompt, not the load"
        );
        let fired = hold(&mut menu, &["a"], 0.8, 1.0);
        assert!(fired.is_empty(), "under half a second it is not yet");
        assert!(menu.view(1.0).a_fill > 0.0, "the ring fills while it is held");
        let fired = hold(&mut menu, &["a"], 1.05, 1.4);
        assert_eq!(fired, [Action::Load(1)], "the save under the cursor, once");
    }

    #[test]
    fn b_steps_back_from_the_prompt_then_from_the_list() {
        let mut menu = browsing(2);
        tap(&mut menu, "a", 0.5);
        tap(&mut menu, "b", 0.6);
        let saves = menu.view(0.7).saves.expect("the list is open");
        assert!(!saves.confirming, "out of the prompt, still in the list");
        tap(&mut menu, "b", 0.8);
        assert_eq!(menu.view(0.9).saves, None, "out of the list, the menu still down");
        assert_eq!(menu.view(0.9).focus, 3, "on the saves row it was opened from");
        assert!(
            hold(&mut menu, &["b"], 1.0, 1.2).is_empty(),
            "a tap to step back is not a hold to close"
        );
    }

    fn with_states(owner: i32) -> Menu {
        let mut menu = Menu::open(owner, room(2, 4), &held(&["a", "leftshoulder", "rightshoulder"]))
            .with_saves()
            .with_states();
        assert_eq!(menu.tick(&held(&[]), 0.0), None);
        menu
    }

    fn to_states(menu: &mut Menu) {
        tap(menu, "dpdown", 0.1);
        tap(menu, "dpdown", 0.2);
        tap(menu, "dpdown", 0.3);
    }

    fn slots_listed(menu: &mut Menu, count: usize, present: u64) {
        to_states(menu);
        assert_eq!(menu.tick(&held(&["a"]), 0.4), None);
        assert_eq!(menu.tick(&held(&[]), 0.42), Some(Action::ListStates));
        menu.states_listed(Some((count, present)));
    }

    #[test]
    fn the_states_row_follows_the_saves_row_and_comes_before_exit() {
        let mut menu = with_states(1);
        to_states(&mut menu);
        let view = menu.view(0.4);
        assert!(view.saves_row && view.states_row);
        assert_eq!(view.focus, 4, "two seats, the tester, the saves, then the slots");
        tap(&mut menu, "dpdown", 0.5);
        assert_eq!(menu.view(0.6).focus, 5, "Exit");
        tap(&mut menu, "dpup", 0.7);
        assert_eq!(menu.view(0.8).focus, 4);

        let mut alone =
            Menu::open(1, room(2, 4), &held(&["a", "leftshoulder", "rightshoulder"])).with_states();
        alone.tick(&held(&[]), 0.0);
        to_saves(&mut alone);
        let view = alone.view(0.3);
        assert!(view.states_row && !view.saves_row);
        assert_eq!(view.focus, 3);
        tap(&mut alone, "dpdown", 0.4);
        assert_eq!(alone.view(0.5).focus, 4, "Exit");
    }

    #[test]
    fn a_on_the_states_row_asks_for_the_slots() {
        let mut menu = with_states(1);
        to_states(&mut menu);
        assert_eq!(menu.tick(&held(&["a"]), 0.4), None);
        assert_eq!(menu.tick(&held(&[]), 0.42), Some(Action::ListStates));
        let browse = menu.view(0.5).saves.expect("the slots are open");
        assert!(browse.states && browse.listed == Listed::Loading);
        menu.states_listed(Some((3, 0b101)));
        assert_eq!(menu.view(0.6).saves.map(|b| b.listed), Some(Listed::Ready(3)));
    }

    #[test]
    fn a_held_on_a_slot_saves_into_it_and_closes_the_menu() {
        let mut menu = with_states(1);
        slots_listed(&mut menu, 3, 0);
        tap(&mut menu, "dpdown", 0.5);
        let fired = hold(&mut menu, &["a"], 0.6, 0.6 + STATE_SECONDS + 0.1);
        assert_eq!(fired, [Action::State { at: 1, save: true }]);
        assert_eq!(menu.tick(&held(&[]), 1.5), None, "the menu is done");
    }

    #[test]
    fn y_held_loads_a_slot_that_holds_a_state_and_nothing_from_an_empty_one() {
        let mut menu = with_states(1);
        slots_listed(&mut menu, 2, 0b10);
        assert!(hold(&mut menu, &["y"], 0.5, 1.5).is_empty(), "slot 1 is empty");
        tap(&mut menu, "dpdown", 1.6);
        let fired = hold(&mut menu, &["y"], 1.7, 1.7 + STATE_SECONDS + 0.1);
        assert_eq!(fired, [Action::State { at: 1, save: false }]);
    }

    #[test]
    fn a_hold_carried_onto_another_slot_starts_over_there() {
        for key in ["a", "y"] {
            let mut menu = with_states(1);
            slots_listed(&mut menu, 3, 0b111);
            assert!(hold(&mut menu, &[key], 0.5, 0.8).is_empty());
            menu.tick(&held(&[key, "dpdown"]), 0.85);
            assert!(
                hold(&mut menu, &[key], 0.9, 1.3).is_empty(),
                "{key}: a hold begun on slot 1 must not fire on slot 2"
            );
            assert_eq!(
                hold(&mut menu, &[key], 1.35, 1.45),
                [Action::State {
                    at: 1,
                    save: key == "a"
                }],
                "{key}: half a second on slot 2 does"
            );
        }
    }

    #[test]
    fn a_and_y_together_are_one_save_and_the_cursor_stops_at_the_ends() {
        let mut menu = with_states(1);
        slots_listed(&mut menu, 3, 0b111);
        tap(&mut menu, "dpup", 0.5);
        for at in [0.6, 0.7, 0.8, 0.9] {
            tap(&mut menu, "dpdown", at);
        }
        assert_eq!(menu.view(1.0).saves.map(|b| b.selected), Some(2));
        let fired = hold(&mut menu, &["a", "y"], 1.1, 1.1 + STATE_SECONDS + 0.1);
        assert_eq!(fired, [Action::State { at: 2, save: true }]);
    }

    #[test]
    fn up_from_exit_walks_back_through_the_slots_the_saves_and_the_tester_to_the_line() {
        let mut menu = with_states(1);
        for at in [0.1, 0.2, 0.3, 0.4, 0.5] {
            tap(&mut menu, "dpdown", at);
        }
        assert_eq!(menu.view(0.6).focus, 5, "Exit, and no further");
        let mut seen = Vec::new();
        for at in [0.7, 0.8, 0.9, 1.0, 1.1] {
            tap(&mut menu, "dpup", at);
            seen.push(menu.view(at + 0.05).focus);
        }
        assert_eq!(
            seen,
            [4, 3, 2, 0, 0],
            "the slots, the saves, the tester, the line"
        );
    }

    #[test]
    fn an_answer_for_the_other_list_is_ignored() {
        let mut menu = with_states(1);
        to_states(&mut menu);
        assert_eq!(menu.tick(&held(&["a"]), 0.4), None);
        assert_eq!(menu.tick(&held(&[]), 0.42), Some(Action::ListStates));
        menu.saves_listed(Some(5));
        assert_eq!(menu.view(0.5).saves.map(|b| b.listed), Some(Listed::Loading));
        menu.states_listed(Some((2, 0b01)));
        assert_eq!(menu.view(0.6).saves.map(|b| b.listed), Some(Listed::Ready(2)));
    }

    #[test]
    fn the_hold_on_a_slot_fills_the_ring_and_b_steps_back_out() {
        let mut menu = with_states(1);
        slots_listed(&mut menu, 2, 0b11);
        menu.tick(&held(&["y"]), 0.5);
        menu.tick(&held(&["y"]), 0.75);
        let fill = menu.view(0.75).a_fill;
        assert!((0.4..=0.6).contains(&fill), "{fill}");
        tap(&mut menu, "b", 0.8);
        assert_eq!(
            menu.view(0.9).saves,
            None,
            "out of the slots, the menu still down"
        );
        assert_eq!(menu.view(0.9).focus, 4, "on the states row it was opened from");
    }

    #[test]
    fn nothing_to_load_while_the_list_is_loading_failed_or_empty() {
        for listed in [None, Some(0)] {
            let mut menu = with_saves(1);
            to_saves(&mut menu);
            tap(&mut menu, "a", 0.3);
            menu.saves_listed(listed);
            tap(&mut menu, "a", 0.5);
            assert!(hold(&mut menu, &["a"], 0.6, 1.5).is_empty());
            assert!(!menu.view(1.5).saves.expect("the list is open").confirming);
        }
        let mut loading = with_saves(1);
        to_saves(&mut loading);
        tap(&mut loading, "a", 0.3);
        tap(&mut loading, "a", 0.5);
        assert!(hold(&mut loading, &["a"], 0.6, 1.5).is_empty());
    }

    #[test]
    fn the_list_shows_failed_when_the_client_could_not_say() {
        let mut menu = with_saves(1);
        to_saves(&mut menu);
        tap(&mut menu, "a", 0.3);
        menu.saves_listed(None);
        assert_eq!(
            menu.view(0.4).saves.expect("the list is open").listed,
            Listed::Failed
        );
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

    /// A face of four buttons and a d-pad, as a drawing places them.
    fn face() -> Vec<Target> {
        [
            (0, 0.8, 0.6),
            (1, 0.9, 0.5),
            (2, 0.2, 0.4),
            (3, 0.2, 0.6),
            (4, 0.1, 0.5),
            (5, 0.3, 0.5),
        ]
        .into_iter()
        .map(|(control, x, y)| Target { control, at: (x, y) })
        .collect()
    }

    fn on_tester(owner: i32) -> Menu {
        let mut menu = opened(owner);
        menu = menu.with_targets(face());
        tap(&mut menu, "dpdown", 0.1);
        menu
    }

    #[test]
    fn the_controller_row_starts_with_the_whole_controller_hovered() {
        let menu = on_tester(1);
        let view = menu.view(0.2);
        assert_eq!(view.focus, 2);
        assert_eq!(view.hover, None);
    }

    #[test]
    fn on_the_whole_controller_a_tap_walks_every_button_of_the_owners_pad() {
        let mut menu = on_tester(2);
        assert_eq!(menu.tick(&held(&["a"]), 0.2), None);
        assert_eq!(menu.tick(&held(&[]), 0.3), Some(Action::Rebind(2)));
        assert_eq!(menu.tick(&held(&["a"]), 0.4), None, "and the menu is done");
    }

    #[test]
    fn on_the_whole_controller_a_held_half_a_second_starts_trying_buttons() {
        let mut menu = on_tester(1);
        assert!(hold(&mut menu, &["a"], 0.2, 0.75).is_empty());
        assert!(menu.view(0.8).testing);
        assert_eq!(menu.tick(&held(&[]), 0.9), None, "letting go of A is not a walk");
        assert!(menu.view(1.0).testing);
    }

    #[test]
    fn on_the_whole_controller_x_resets_the_whole_walk_and_the_menu_stays_down() {
        let mut menu = on_tester(1);
        assert_eq!(
            menu.tick(&held(&["x"]), 0.2),
            Some(Action::Forget {
                player: 1,
                control: None
            })
        );
        menu.tick(&held(&[]), 0.3);
        assert_eq!(menu.view(0.4).focus, 2, "still on the controller");
        assert_eq!(menu.tick(&held(&["dpup"]), 0.5), None);
        menu.tick(&held(&[]), 0.6);
        assert_eq!(menu.view(0.7).focus, 0, "and still driven");
    }

    #[test]
    fn a_button_hovered_takes_a_tap_of_a_as_a_rebind_of_that_button_only() {
        let mut menu = on_tester(1);
        tap(&mut menu, "dpright", 0.2);
        assert_eq!(menu.view(0.3).hover, Some(0));
        menu.tick(&held(&["a"]), 0.4);
        assert_eq!(
            menu.tick(&held(&[]), 0.5),
            Some(Action::Bind {
                player: 1,
                control: 0,
                add: false
            })
        );
    }

    #[test]
    fn y_on_a_hovered_button_adds_an_input_to_it_once_let_go() {
        let mut menu = on_tester(1);
        tap(&mut menu, "dpleft", 0.2);
        assert_eq!(menu.view(0.3).hover, Some(5));
        assert_eq!(menu.tick(&held(&["y"]), 0.4), None, "not while Y is still down");
        assert_eq!(
            menu.tick(&held(&[]), 0.5),
            Some(Action::Bind {
                player: 1,
                control: 5,
                add: true
            })
        );
        // On the whole controller Y is nothing.
        let mut menu = on_tester(1);
        assert_eq!(menu.tick(&held(&["y"]), 0.2), None);
        assert_eq!(menu.tick(&held(&[]), 0.3), None);
    }

    #[test]
    fn x_on_a_hovered_button_resets_that_control_only() {
        let mut menu = on_tester(1);
        tap(&mut menu, "dpright", 0.2);
        assert_eq!(
            menu.tick(&held(&["x"]), 0.3),
            Some(Action::Forget {
                player: 1,
                control: Some(0)
            })
        );
    }

    #[test]
    fn up_and_down_on_the_whole_controller_leave_the_row_as_before() {
        let mut menu = on_tester(1);
        tap(&mut menu, "dpdown", 0.2);
        assert_eq!(menu.view(0.3).focus, 3, "Exit");
        tap(&mut menu, "dpup", 0.4);
        tap(&mut menu, "dpup", 0.5);
        assert_eq!(menu.view(0.6).focus, 0, "the line");
    }

    #[test]
    fn up_and_down_on_a_button_move_between_buttons_and_off_the_end_return_to_the_whole_controller() {
        let mut menu = on_tester(1);
        tap(&mut menu, "dpleft", 0.2);
        assert_eq!(
            menu.view(0.3).hover,
            Some(5),
            "dpright, nearest left of the middle"
        );
        tap(&mut menu, "dpup", 0.4);
        assert_eq!(menu.view(0.5).hover, Some(2), "dpup, above it");
        tap(&mut menu, "dpup", 0.6);
        assert_eq!(menu.view(0.7).hover, None, "nothing above: the whole controller");
        assert_eq!(menu.view(0.7).focus, 2, "and still on the row");
        tap(&mut menu, "dpup", 0.8);
        assert_eq!(menu.view(0.9).focus, 0, "a second press leaves it");
    }

    #[test]
    fn coming_back_to_the_row_hovers_the_whole_controller_again() {
        let mut menu = on_tester(1);
        tap(&mut menu, "dpright", 0.2);
        assert_eq!(menu.view(0.3).hover, Some(0));
        tap(&mut menu, "dpright", 0.4);
        tap(&mut menu, "dpright", 0.5);
        assert_eq!(menu.view(0.6).hover, None, "off the right edge");
        tap(&mut menu, "dpright", 0.7);
        assert_eq!(menu.view(0.75).hover, Some(0), "and round again");
        tap(&mut menu, "dpup", 0.8);
        assert_eq!(menu.view(0.85).hover, Some(1), "B, up and to the right");
        tap(&mut menu, "dpup", 0.9);
        assert_eq!(menu.view(0.95).hover, None, "nothing above B");
        tap(&mut menu, "dpup", 1.0);
        assert_eq!(
            menu.view(1.05).focus,
            0,
            "up from the whole controller is the line"
        );
        tap(&mut menu, "dpdown", 1.1);
        assert_eq!(menu.view(1.2).focus, 2);
        assert_eq!(menu.view(1.2).hover, None);
    }

    #[test]
    fn a_hold_under_one_hover_is_not_carried_to_the_next() {
        let mut menu = on_tester(1);
        assert!(hold(&mut menu, &["a"], 0.2, 0.4).is_empty());
        assert!(menu.view(0.4).a_fill > 0.3);
        menu.tick(&held(&["a", "dpright"]), 0.45);
        assert!(menu.view(0.46).a_fill < 0.1, "the hold starts over on the button");
        assert!(!menu.view(0.46).testing);
        assert!(
            hold(&mut menu, &["a"], 0.5, 1.2).is_empty(),
            "no test from a button"
        );
        assert!(!menu.view(1.2).testing);
    }

    #[test]
    fn a_refused_reset_is_said_for_two_seconds() {
        let mut menu = on_tester(1);
        assert!(!menu.view(0.2).notice);
        menu.refused_reset(0.3);
        assert!(menu.view(0.4).notice);
        assert!(menu.view(0.3 + NOTICE_SECONDS - 0.1).notice);
        assert!(!menu.view(0.3 + NOTICE_SECONDS + 0.1).notice);
    }

    #[test]
    fn a_on_the_controller_tests_it_until_select_is_held_half_a_second() {
        let mut menu = opened(1);
        tap(&mut menu, "dpdown", 0.1);
        hold(&mut menu, &["a"], 0.2, 0.75);
        assert!(menu.view(0.8).testing);
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
        assert!(
            matches!(menu.tick(&held(&["x"]), 0.3), Some(Action::Forget { .. })),
            "on the game's controller X is a reset"
        );
        menu.tick(&held(&[]), 0.35);
        tap(&mut menu, "dpdown", 0.4);
        assert_eq!(menu.tick(&held(&["x"]), 0.5), None, "Exit is nobody's seat");
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
