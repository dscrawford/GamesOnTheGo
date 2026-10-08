//! The menu as the loop drives it: whether one is open, whose, what its
//! actions turn into, and the state around it that lives and dies with it --
//! the Exit that is waiting for the saves to go up, the client listing saves
//! into the session, the game being started again on a picked one. What is
//! here decides and returns a `Step` for the loop to carry out (a line on
//! danstick's socket, the bar going up, a process started), so the decisions
//! are tested with no socket and no SDL.
//!
//! The menu itself (`menu::Menu`) is the model of the screen; this is the
//! model of the loop's dealings with it.

use std::collections::BTreeSet;

use crate::frame::Saying;
use crate::loading::{self, Listing, SaveLine, Session};
use crate::menu::{Action, Menu, Row, View};

/// The longest the bar says a save is loading: the wrapper pushes, restores
/// and starts the game again, and a game slow to start is not waited on.
pub const RELOAD_SECONDS: f64 = 120.0;

/// What the loop is to do about an action of the menu.
#[derive(Debug, Clone, PartialEq)]
pub enum Step {
    /// Let go of `owner`'s pad, and bring the bar up over the close time.
    Close { owner: i32 },
    /// Let go of `owner`'s pad; the game is to be stopped and its saves sent.
    Exit { owner: i32 },
    /// Let go of `owner`'s pad and walk `player`'s buttons.
    Rebind { owner: i32, player: i32 },
    /// A line for danstick. Seat changes are leased to this connection:
    /// whatever a port switched off comes back on if the overlay goes.
    Send(String),
    /// Let go of `owner`'s pad and start the game again on this save (None:
    /// the list had no such place, and the bar comes up).
    Load { owner: i32, line: Option<SaveLine> },
}

/// What asking for the menu came to.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Opening {
    /// Down for this player.
    Opened(i32),
    /// Asked on a pad danstick has not seated: nothing to show.
    Unseated,
}

#[derive(Debug, Default)]
pub struct MenuDriver {
    menu: Option<Menu>,
    exiting: bool,
    /// This play's session, if `gotg play` made one. See loading.rs.
    session: Option<Session>,
    /// The client listing the saves into the session.
    listing: Option<Listing>,
    save_lines: Vec<SaveLine>,
    /// The game being started again on a save: since when, and the process
    /// it was. The bar says so until the wrapper has.
    reloading: Option<(f64, i32)>,
}

impl MenuDriver {
    pub fn new(session: Option<Session>) -> Self {
        Self {
            session,
            ..Self::default()
        }
    }

    pub fn is_open(&self) -> bool {
        self.menu.is_some()
    }

    pub fn session(&self) -> Option<&Session> {
        self.session.as_ref()
    }

    /// The menu as the bar draws it.
    pub fn view(&self, now: f64) -> Option<View> {
        self.menu.as_ref().map(|open| open.view(now))
    }

    /// Whether the menu's Exit has been taken.
    pub fn exiting(&self) -> bool {
        self.exiting
    }

    /// Whether a save is being loaded.
    pub fn reloading(&self) -> bool {
        self.reloading.is_some()
    }

    /// Whether the menu has a reason to keep the bar down.
    pub fn wants_bar(&self) -> bool {
        self.is_open() || self.reloading() || self.exiting
    }

    /// What the bar says in words, Exit before a load.
    pub fn saying(&self) -> Saying {
        if self.exiting {
            Saying::Saving
        } else if self.reloading() {
            Saying::Loading
        } else {
            Saying::Nothing
        }
    }

    /// Only where the game can be started again: a session, a wrapper that
    /// said which process is the game, a game of its own saves, and a client
    /// to ask.
    fn can_load(&self, client: &str, saves: &str) -> bool {
        !client.is_empty()
            && loading::dedicated(saves)
            && self.session.as_ref().and_then(Session::game_pid).is_some()
    }

    /// `who` held the menu chord with `down` pressed. `rows` is asked for
    /// only when there is a seat to open for.
    pub fn open(
        &mut self,
        who: Option<i32>,
        down: &BTreeSet<String>,
        rows: impl FnOnce() -> Vec<Row>,
        client: &str,
        saves: &str,
    ) -> Opening {
        let Some(owner) = who else {
            return Opening::Unseated;
        };
        let opened = Menu::open(owner, rows(), down);
        self.menu = Some(if self.can_load(client, saves) {
            opened.with_saves()
        } else {
            opened
        });
        Opening::Opened(owner)
    }

    /// One frame of the open menu, driven by its owner alone: `down` is what
    /// that player has down, from danstick's word while it holds them
    /// (`focus`) and the clone's through SDL besides -- which is all there is
    /// from a danstick that cannot hold them, and nothing once one does.
    pub fn drive(
        &mut self,
        rows: impl FnOnce() -> Vec<Row>,
        owner_gone: impl FnOnce(i32) -> bool,
        down: impl FnOnce(i32) -> BTreeSet<String>,
        client: &str,
        saves: &str,
        now: f64,
    ) -> Option<Step> {
        let open = self.menu.as_mut()?;
        open.seats(rows());
        let owner = open.owner;
        if owner_gone(owner) {
            open.pad_gone(owner);
        }
        let action = open.tick(&down(owner), now)?;
        self.apply(action, owner, client, saves, now)
    }

    /// What an action of the menu does to the state here, and what is left
    /// for the loop.
    pub fn apply(&mut self, action: Action, owner: i32, client: &str, saves: &str, now: f64) -> Option<Step> {
        match action {
            Action::Close => {
                self.menu = None;
                Some(Step::Close { owner })
            }
            Action::Exit => {
                self.menu = None;
                self.exiting = true;
                Some(Step::Exit { owner })
            }
            Action::Rebind(player) => {
                self.menu = None;
                Some(Step::Rebind { owner, player })
            }
            Action::Remove(player) => Some(Step::Send(
                serde_json::json!({"cmd": "unseat", "player": player}).to_string(),
            )),
            Action::Move { player, to } => Some(Step::Send(
                serde_json::json!({"cmd": "move", "player": player, "to": to}).to_string(),
            )),
            Action::Port { player, open } => Some(Step::Send(
                serde_json::json!({"cmd": "port", "player": player, "open": open}).to_string(),
            )),
            Action::ListSaves => {
                if let Some(session) = &self.session {
                    self.listing = Some(Listing::start(client, saves, session, now));
                }
                None
            }
            Action::Load(at) => {
                self.menu = None;
                self.listing = None;
                Some(Step::Load {
                    owner,
                    line: self.save_lines.get(at).cloned(),
                })
            }
        }
    }

    /// What `load_save` came to: the game being stopped (since when, which
    /// process), or None when there was nothing to do it with. True when it
    /// is on its way.
    pub fn reload_started(&mut self, started: Option<(f64, i32)>) -> bool {
        self.reloading = started;
        started.is_some()
    }

    /// The client's listing, once it has said.
    pub fn poll_listing(&mut self, now: f64) {
        let (Some(running), Some(session)) = (self.listing.as_mut(), self.session.as_ref()) else {
            return;
        };
        let Some(listed) = running.poll(session, now) else {
            return;
        };
        self.listing = None;
        self.save_lines = session.lines();
        let count = self.save_lines.len();
        if let Some(open) = self.menu.as_mut() {
            open.saves_listed(listed.map(|_| count));
        }
    }

    /// Started again once the wrapper says a different process is the game,
    /// or when it has taken too long to.
    pub fn poll_reload(&mut self, now: f64, game_alive: impl FnOnce(i32) -> bool) {
        let Some((since, was)) = self.reloading else {
            return;
        };
        let moved = || {
            self.session
                .as_ref()
                .and_then(Session::game_pid)
                .filter(|&pid| pid != was)
        };
        if now - since > RELOAD_SECONDS || moved().is_some_and(game_alive) {
            self.reloading = None;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn seated(count: usize) -> Vec<Row> {
        (0..count)
            .map(|at| Row {
                icon: Some(at as u8),
                off: false,
            })
            .collect()
    }

    fn opened(owner: i32) -> MenuDriver {
        let mut driver = MenuDriver::new(None);
        assert_eq!(
            driver.open(Some(owner), &BTreeSet::new(), || seated(2), "", ""),
            Opening::Opened(owner)
        );
        driver
    }

    #[test]
    fn a_menu_held_on_an_unseated_pad_opens_nothing_and_asks_for_no_rows() {
        let mut driver = MenuDriver::new(None);
        let opening = driver.open(None, &BTreeSet::new(), || panic!("no rows for nobody"), "", "");
        assert_eq!(opening, Opening::Unseated);
        assert!(!driver.is_open() && !driver.wants_bar());
    }

    #[test]
    fn an_open_menu_keeps_the_bar_down() {
        let driver = opened(1);
        assert!(driver.is_open() && driver.wants_bar());
        assert_eq!(driver.view(0.0).map(|v| v.owner), Some(1));
    }

    #[test]
    fn there_is_no_saves_row_without_a_session_to_load_through() {
        let driver = opened(1);
        assert!(!driver.can_load("/bin/gotg", "env-n64-donkey_kong_64"));
        assert!(driver.view(0.0).is_some_and(|v| !v.saves_row));
    }

    #[test]
    fn close_and_rebind_put_the_menu_away_and_say_whose_pad_to_let_go_of() {
        let mut driver = opened(2);
        assert_eq!(
            driver.apply(Action::Close, 2, "", "", 0.0),
            Some(Step::Close { owner: 2 })
        );
        assert!(!driver.is_open());
        let mut driver = opened(2);
        assert_eq!(
            driver.apply(Action::Rebind(1), 2, "", "", 0.0),
            Some(Step::Rebind { owner: 2, player: 1 })
        );
        assert!(!driver.is_open() && !driver.exiting());
    }

    #[test]
    fn exit_closes_the_menu_and_the_bar_says_saving() {
        let mut driver = opened(1);
        assert_eq!(
            driver.apply(Action::Exit, 1, "", "", 0.0),
            Some(Step::Exit { owner: 1 })
        );
        assert!(!driver.is_open());
        assert!(driver.exiting() && driver.wants_bar());
        assert_eq!(driver.saying(), Saying::Saving);
    }

    #[test]
    fn seat_changes_are_lines_for_danstick_and_leave_the_menu_open() {
        let mut driver = opened(1);
        let mut line = |action| match driver.apply(action, 1, "", "", 0.0) {
            Some(Step::Send(line)) => serde_json::from_str::<serde_json::Value>(&line).expect("json"),
            other => panic!("expected a line, got {other:?}"),
        };
        assert_eq!(
            line(Action::Remove(2)),
            serde_json::json!({"cmd": "unseat", "player": 2})
        );
        assert_eq!(
            line(Action::Move { player: 1, to: 3 }),
            serde_json::json!({"cmd": "move", "player": 1, "to": 3})
        );
        assert_eq!(
            line(Action::Port {
                player: 2,
                open: false
            }),
            serde_json::json!({"cmd": "port", "player": 2, "open": false})
        );
        assert!(driver.is_open());
    }

    #[test]
    fn asking_for_saves_with_no_session_does_nothing() {
        let mut driver = opened(1);
        assert_eq!(
            driver.apply(Action::ListSaves, 1, "/bin/gotg", "env-x-y", 0.0),
            None
        );
        assert!(driver.is_open());
    }

    #[test]
    fn a_load_of_a_save_that_is_not_listed_closes_the_menu_and_names_no_line() {
        let mut driver = opened(1);
        assert_eq!(
            driver.apply(Action::Load(3), 1, "", "", 0.0),
            Some(Step::Load { owner: 1, line: None })
        );
        assert!(!driver.is_open());
        assert!(
            !driver.reload_started(None),
            "nothing started, so the bar comes up"
        );
        assert_eq!(driver.saying(), Saying::Nothing);
    }

    #[test]
    fn a_load_in_flight_says_loading_until_the_time_is_up() {
        let mut driver = MenuDriver::new(None);
        assert!(driver.reload_started(Some((10.0, 42))));
        assert!(driver.wants_bar());
        assert_eq!(driver.saying(), Saying::Loading);
        driver.poll_reload(10.0 + RELOAD_SECONDS, |_| true);
        assert!(driver.reloading(), "not yet: the limit is a limit, not a minimum");
        driver.poll_reload(10.1 + RELOAD_SECONDS, |_| true);
        assert!(!driver.reloading() && !driver.wants_bar());
    }

    #[test]
    fn exit_is_said_before_a_load() {
        let mut driver = opened(1);
        driver.reload_started(Some((0.0, 7)));
        driver.apply(Action::Exit, 1, "", "", 0.0);
        assert_eq!(driver.saying(), Saying::Saving);
    }

    #[test]
    fn driving_a_closed_menu_asks_for_nothing() {
        let mut driver = MenuDriver::new(None);
        let step = driver.drive(
            || panic!("no rows for a menu that is not there"),
            |_| panic!("no owner"),
            |_| panic!("nothing down"),
            "",
            "",
            0.0,
        );
        assert_eq!(step, None);
    }
}
