//! How big the panel is, under gamescope -- which is not what X says.
//!
//! gamescope paints its external overlay with `NoScale` (steamcompmgr.cpp,
//! `paint_window`): the window's buffer lands on the panel one pixel for one,
//! from the top-left corner. Steam's X screen, which is where the window
//! lives, is whatever size Steam last asked for through
//! `GAMESCOPE_XWAYLAND_MODE_CONTROL`, and on a Deck docked to a television
//! that stayed the Deck's own. Sized to the X screen, the overlay was a
//! 1280x800 picture in the top-left corner of a 4K panel: small, and nowhere
//! near the middle. mangoapp, the slot's other tenant, is sent the output's
//! size by gamescope over a message queue only it reads, and resizes itself.
//!
//! Nothing gamescope publishes to anybody else carries that size -- not its
//! control protocol, not the wl_output it gives each X server (that is the
//! nested size again). KMS does: on a Deck gamescope drives the panel itself,
//! and the mode on its CRTC is the output's. Read once per painter, which is
//! once each time the bar comes down: a handful of ioctls in the painter's
//! process, nowhere near the kill switch's loop.
//!
//! Nested -- gamescope in a window on somebody's desktop -- the CRTCs are the
//! desktop's, not gamescope's, so KMS is asked only when the gamescope that
//! owns Steam's display holds a card node open itself.

use std::fs::File;
use std::os::fd::{AsFd, BorrowedFd};
use std::path::{Path, PathBuf};

use drm::control::Device as _;

/// Width and height in pixels.
pub type Size = (u32, u32);

/// What gamescope's overlay is painted at: the largest lit CRTC when
/// gamescope drives the panels, else the X screen. Largest, because a docked
/// Deck that kept its own screen lit too paints the overlay on the
/// television, which is the bigger of the two. Nothing lit, or KMS not
/// gamescope's, and the X screen is all there is to go on.
pub fn painted(gamescope_drives_kms: bool, lit: &[Size], x_screen: Size) -> Size {
    if !gamescope_drives_kms {
        return x_screen;
    }
    lit.iter()
        .copied()
        .filter(|&(w, h)| w > 0 && h > 0)
        .max_by_key(|&(w, h)| u64::from(w) * u64::from(h))
        .unwrap_or(x_screen)
}

/// Whether one of these open files is a KMS card node. A render node
/// (`renderD128`) is what any Vulkan program holds, nested gamescope
/// included; only the compositor driving the panels holds `card0`.
pub fn holds_card<P: AsRef<Path>>(open: impl IntoIterator<Item = P>) -> bool {
    open.into_iter().any(|path| {
        path.as_ref()
            .strip_prefix("/dev/dri")
            .ok()
            .and_then(Path::to_str)
            .is_some_and(|name| name.starts_with("card"))
    })
}

/// Whether process `pid` has a card node open: what it has open, read off
/// /proc. Ours to read, since gamescope runs as the same user.
pub fn process_holds_card(pid: u32) -> bool {
    let Ok(fds) = std::fs::read_dir(format!("/proc/{pid}/fd")) else {
        return false;
    };
    holds_card(fds.flatten().filter_map(|fd| std::fs::read_link(fd.path()).ok()))
}

struct Card(File);

impl AsFd for Card {
    fn as_fd(&self) -> BorrowedFd<'_> {
        self.0.as_fd()
    }
}

impl drm::Device for Card {}
impl drm::control::Device for Card {}

/// The mode on every lit CRTC of every card. Reading resources and CRTCs
/// needs neither DRM master nor authentication, only the node, which logind
/// opens to whoever has the seat.
pub fn lit_modes() -> Vec<Size> {
    let Ok(nodes) = std::fs::read_dir("/dev/dri") else {
        return Vec::new();
    };
    let cards: Vec<PathBuf> = nodes
        .flatten()
        .map(|node| node.path())
        .filter(|path| holds_card([path]))
        .collect();
    cards
        .iter()
        .filter_map(|path| File::open(path).ok().map(Card))
        .flat_map(|card| {
            let crtcs = card
                .resource_handles()
                .map(|resources| resources.crtcs().to_vec())
                .unwrap_or_default();
            crtcs
                .into_iter()
                .filter_map(|crtc| card.get_crtc(crtc).ok()?.mode())
                .map(|mode| {
                    let (w, h) = mode.size();
                    (u32::from(w), u32::from(h))
                })
                .collect::<Vec<_>>()
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    const DECK: Size = (1280, 800);
    const TV: Size = (3840, 2160);

    #[test]
    fn a_docked_deck_paints_at_the_televisions_size_not_steams_screen() {
        assert_eq!(painted(true, &[TV], DECK), TV);
    }

    #[test]
    fn with_both_screens_lit_the_bigger_one_is_where_it_goes() {
        assert_eq!(painted(true, &[DECK, TV], DECK), TV);
        assert_eq!(painted(true, &[TV, DECK], DECK), TV);
    }

    #[test]
    fn nested_gamescope_keeps_the_x_screen() {
        assert_eq!(
            painted(false, &[TV], DECK),
            DECK,
            "the desktop's CRTCs are not gamescope's"
        );
    }

    #[test]
    fn nothing_lit_or_nonsense_falls_back_to_the_x_screen() {
        assert_eq!(painted(true, &[], DECK), DECK);
        assert_eq!(painted(true, &[(0, 0), (1920, 0)], DECK), DECK);
    }

    #[test]
    fn only_a_card_node_counts_as_driving_the_panels() {
        assert!(holds_card(["/dev/null", "/dev/dri/card0"]));
        assert!(holds_card(["/dev/dri/card1"]));
        assert!(
            !holds_card(["/dev/dri/renderD128"]),
            "every Vulkan program holds one"
        );
        assert!(!holds_card(["/dev/dri", "/home/deck/card0", "socket:[123]"]));
        assert!(!holds_card(Vec::<&str>::new()));
    }

    /// This machine's lit modes, to see the KMS read work on real hardware:
    /// `cargo test -p gotg-killswitch -- --ignored --nocapture lit_modes`.
    #[test]
    #[ignore = "reads this machine's /dev/dri"]
    fn lit_modes_on_this_machine() {
        let lit = lit_modes();
        eprintln!("lit: {lit:?}");
        assert!(!lit.is_empty());
    }
}
