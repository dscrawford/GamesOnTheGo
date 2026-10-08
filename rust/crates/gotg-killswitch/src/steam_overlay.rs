//! Whether Steam's overlay is up over the game, from what gamescope says of
//! the windows. The reading of X is `steam_overlay_x11`; what the properties
//! add up to, and when that changes, is here and tested with no display.

use std::collections::HashMap;

/// What one top-level window carries that matters here.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Props {
    /// `STEAM_OVERLAY` is set: this window is Steam's overlay.
    pub overlay: bool,
    /// `STEAM_INPUT_FOCUS` is set: the overlay has taken input.
    pub input_focus: bool,
    /// `_NET_WM_WINDOW_OPACITY`; nothing drawn at zero.
    pub opacity: u32,
    pub mapped: bool,
}

#[derive(Debug, Default)]
pub struct Windows(HashMap<u32, Props>);

impl Windows {
    /// What `id` carries now, replacing what was known of it.
    pub fn note(&mut self, id: u32, props: Props) {
        self.0.insert(id, props);
    }

    /// `id` was destroyed (or could no longer be read).
    pub fn gone(&mut self, id: u32) {
        self.0.remove(&id);
    }

    /// Some mapped overlay window is drawn or has the input.
    pub fn up(&self) -> bool {
        self.0
            .values()
            .any(|w| w.mapped && w.overlay && (w.input_focus || w.opacity != 0))
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Transition {
    Up,
    Down,
    None,
}

/// Turns the level `up()` into one edge each way.
#[derive(Debug, Default)]
pub struct Seen {
    was: bool,
}

impl Seen {
    pub fn edge(&mut self, up: bool) -> Transition {
        let was = std::mem::replace(&mut self.was, up);
        match (was, up) {
            (false, true) => Transition::Up,
            (true, false) => Transition::Down,
            _ => Transition::None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const SHOWN: Props = Props {
        overlay: true,
        input_focus: true,
        opacity: 0,
        mapped: true,
    };

    #[test]
    fn the_overlay_window_mapped_with_input_focus_is_up() {
        let mut windows = Windows::default();
        assert!(!windows.up(), "no windows, no overlay");
        windows.note(7, SHOWN);
        assert!(windows.up());
    }

    #[test]
    fn opacity_alone_is_up_and_nothing_drawn_or_taken_is_not() {
        let mut windows = Windows::default();
        windows.note(
            7,
            Props {
                input_focus: false,
                opacity: 0xffff_ffff,
                ..SHOWN
            },
        );
        assert!(windows.up());
        windows.note(
            7,
            Props {
                input_focus: false,
                ..SHOWN
            },
        );
        assert!(!windows.up(), "mapped but invisible and without the input");
    }

    #[test]
    fn an_unmapped_overlay_is_down() {
        let mut windows = Windows::default();
        windows.note(
            7,
            Props {
                mapped: false,
                ..SHOWN
            },
        );
        assert!(!windows.up());
    }

    #[test]
    fn destroying_the_window_brings_it_down() {
        let mut windows = Windows::default();
        windows.note(7, SHOWN);
        windows.gone(7);
        assert!(!windows.up());
        windows.gone(7);
    }

    #[test]
    fn it_stays_up_until_every_overlay_window_is_down() {
        let mut windows = Windows::default();
        windows.note(7, SHOWN);
        windows.note(9, SHOWN);
        windows.gone(7);
        assert!(windows.up(), "the other is still there");
        windows.note(
            9,
            Props {
                input_focus: false,
                ..SHOWN
            },
        );
        assert!(!windows.up());
    }

    #[test]
    fn a_window_that_is_not_the_overlay_is_nothing_whatever_it_has() {
        let mut windows = Windows::default();
        windows.note(
            3,
            Props {
                overlay: false,
                opacity: 0xffff_ffff,
                ..SHOWN
            },
        );
        assert!(!windows.up());
    }

    #[test]
    fn each_edge_is_said_once() {
        let mut seen = Seen::default();
        assert_eq!(seen.edge(false), Transition::None, "starts down");
        assert_eq!(seen.edge(true), Transition::Up);
        assert_eq!(seen.edge(true), Transition::None);
        assert_eq!(seen.edge(false), Transition::Down);
        assert_eq!(seen.edge(false), Transition::None);
    }
}
