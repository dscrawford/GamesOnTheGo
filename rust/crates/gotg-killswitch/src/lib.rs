//! gotg-killswitch's parts. Everything that decides something -- the chord,
//! /proc, who is joining, where the bar is, what a frame draws, what danstick's
//! lines say -- is here with no SDL in it and tested on a clock the tests
//! own. The SDL, Wayland and X11 halves are `overlay`, `painter::paint` and
//! the binary's loop.

pub mod bar;
pub mod clones;
pub mod consoles;
pub mod events;
pub mod frame;
pub mod icons;
pub mod killswitch;
pub mod overlay;
pub mod padlink;
pub mod painter;
pub mod pairing;
pub mod procstat;
pub mod rebind;
pub mod scene;
pub mod shapes;

/// config/theme.yaml's colours, generated at build time.
pub mod theme {
    include!(concat!(env!("OUT_DIR"), "/theme.rs"));
}
