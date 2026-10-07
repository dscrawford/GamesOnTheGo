//! How big the panel is, under gamescope -- which is not what X says.
//!
//! gamescope paints its external overlay with `NoScale` (steamcompmgr.cpp,
//! `paint_window`): the window's buffer lands on the panel one pixel for one,
//! from the top-left corner. Steam's X screen, which is where the window
//! lives, is whatever size Steam last asked for through
//! `GAMESCOPE_XWAYLAND_MODE_CONTROL`: on a Deck docked to a 4K television it
//! was 1920x1080. Sized to the X screen, the overlay was a quarter of the
//! panel in its top-left corner: small, and nowhere near the middle.
//!
//! The panel's size is the mode on the CRTC of the connector gamescope
//! drives, which KMS gives anybody holding the card node -- and logind gives
//! that to whoever has the seat. Which connector is gamescope's to say:
//! `gamescope_control`'s `active_display_info` names it (`DP-3`), and names
//! no connector at all when gamescope is nested (`SDLWindow`, `Wayland`,
//! `Headless`), where the X screen is the size to use after all.
//!
//! The first version guessed instead: KMS was asked only when the gamescope
//! owning Steam's display held a card node, read off /proc/<pid>/fd. SteamOS
//! gives gamescope CAP_SYS_NICE, which makes it undumpable, which makes its
//! fd directory root's: "Permission denied", no KMS, and the overlay stayed
//! a quarter of the television. Asking is cheaper than guessing: one
//! roundtrip on gamescope's socket and a handful of ioctls, once per painter.

use std::fs::File;
use std::os::fd::{AsFd, BorrowedFd};
use std::os::unix::net::UnixStream;
use std::path::PathBuf;
use std::time::Duration;

use drm::control::Device as _;
use wayland_client::globals::{GlobalListContents, registry_queue_init};
use wayland_client::protocol::wl_registry::WlRegistry;
use wayland_client::{Connection, Dispatch, Proxy, QueueHandle};

/// gamescope's private control protocol, generated from the copy of its XML
/// beside this crate (protocols/, from ValveSoftware/gamescope).
#[allow(missing_debug_implementations, clippy::all, unused_imports)]
pub mod gamescope_control {
    use wayland_client;
    use wayland_client::protocol::*;

    pub mod __interfaces {
        use wayland_client::protocol::__interfaces::*;
        wayland_scanner::generate_interfaces!("protocols/gamescope-control.xml");
    }
    use self::__interfaces::*;

    wayland_scanner::generate_client_code!("protocols/gamescope-control.xml");
}

use gamescope_control::gamescope_control::{Event as ControlEvent, GamescopeControl};

/// Width and height in pixels.
pub type Size = (u32, u32);

/// What gamescope's overlay is painted at: the mode on the connector
/// gamescope says it drives, else the X screen. A name KMS does not have --
/// a nested gamescope's `SDLWindow` -- or no answer at all, and the X screen
/// is all there is to go on.
pub fn painted(active: Option<&str>, lit: &[(String, Size)], x_screen: Size) -> Size {
    active
        .and_then(|name| lit.iter().find(|(connector, _)| connector == name))
        .map(|&(_, size)| size)
        .filter(|&(w, h)| w > 0 && h > 0)
        .unwrap_or(x_screen)
}

/// A connector's name as KMS and gamescope both spell it: libdrm's type
/// name and the type's own number, `DP-3`, `eDP-1`, `HDMI-A-1`.
pub fn connector_name(kind: &str, id: u32) -> String {
    format!("{kind}-{id}")
}

#[derive(Debug, Default)]
struct Asked {
    connector: Option<String>,
}

impl Dispatch<WlRegistry, GlobalListContents> for Asked {
    fn event(
        _: &mut Self,
        _: &WlRegistry,
        _: <WlRegistry as Proxy>::Event,
        _: &GlobalListContents,
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
    }
}

impl Dispatch<GamescopeControl, ()> for Asked {
    fn event(
        state: &mut Self,
        _: &GamescopeControl,
        event: ControlEvent,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        if let ControlEvent::ActiveDisplayInfo { connector_name, .. } = event {
            state.connector = Some(connector_name);
        }
    }
}

/// The connector gamescope drives, from `active_display_info`, which it
/// sends to every client that binds `gamescope_control` (version 2 and on).
/// None where there is no gamescope to ask, or it does not answer in half a
/// second -- a read timeout on the socket, so a gamescope that has wedged
/// costs the painter that and no more.
pub fn active_connector() -> Option<String> {
    let name = PathBuf::from(std::env::var_os("GAMESCOPE_WAYLAND_DISPLAY")?);
    let socket = if name.is_absolute() {
        name
    } else {
        PathBuf::from(std::env::var_os("XDG_RUNTIME_DIR")?).join(name)
    };
    let stream = UnixStream::connect(socket).ok()?;
    stream.set_read_timeout(Some(Duration::from_millis(500))).ok()?;
    let connection = Connection::from_socket(stream).ok()?;
    let (globals, mut queue) = registry_queue_init::<Asked>(&connection).ok()?;
    let _control: GamescopeControl = globals.bind(&queue.handle(), 2..=2, ()).ok()?;
    let mut asked = Asked::default();
    queue.roundtrip(&mut asked).ok()?;
    asked.connector
}

struct Card(File);

impl AsFd for Card {
    fn as_fd(&self) -> BorrowedFd<'_> {
        self.0.as_fd()
    }
}

impl drm::Device for Card {}
impl drm::control::Device for Card {}

/// Every lit connector of every card, by name, with the mode on its CRTC.
/// Reading resources, connectors, encoders and CRTCs needs neither DRM
/// master nor authentication, only the node.
pub fn lit_connectors() -> Vec<(String, Size)> {
    let Ok(nodes) = std::fs::read_dir("/dev/dri") else {
        return Vec::new();
    };
    let cards: Vec<PathBuf> = nodes
        .flatten()
        .map(|node| node.path())
        .filter(|path| {
            path.file_name()
                .and_then(|name| name.to_str())
                .is_some_and(|name| name.starts_with("card"))
        })
        .collect();
    cards
        .iter()
        .filter_map(|path| File::open(path).ok().map(Card))
        .flat_map(|card| {
            let connectors = card
                .resource_handles()
                .map(|resources| resources.connectors().to_vec())
                .unwrap_or_default();
            connectors
                .into_iter()
                .filter_map(|handle| {
                    let connector = card.get_connector(handle, false).ok()?;
                    let crtc = card.get_encoder(connector.current_encoder()?).ok()?.crtc()?;
                    let (w, h) = card.get_crtc(crtc).ok()?.mode()?.size();
                    let name = connector_name(connector.interface().as_str(), connector.interface_id());
                    Some((name, (u32::from(w), u32::from(h))))
                })
                .collect::<Vec<_>>()
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    const STEAMS_SCREEN: Size = (1920, 1080);
    const TV: Size = (3840, 2160);
    const DECK: Size = (800, 1280);

    fn lit(connectors: &[(&str, Size)]) -> Vec<(String, Size)> {
        connectors
            .iter()
            .map(|&(name, size)| (name.to_owned(), size))
            .collect()
    }

    #[test]
    fn a_docked_deck_paints_at_the_televisions_size_not_steams_screen() {
        // What the Deck reported on the television: DP-3 at 3840x2160 and a
        // 1920x1080 X screen.
        assert_eq!(painted(Some("DP-3"), &lit(&[("DP-3", TV)]), STEAMS_SCREEN), TV);
    }

    #[test]
    fn with_both_screens_lit_the_one_gamescope_names_is_where_it_goes() {
        let both = lit(&[("eDP-1", DECK), ("DP-3", TV)]);
        assert_eq!(painted(Some("DP-3"), &both, STEAMS_SCREEN), TV);
        assert_eq!(painted(Some("eDP-1"), &both, STEAMS_SCREEN), DECK);
    }

    #[test]
    fn nested_gamescope_keeps_the_x_screen() {
        // Its connector is its window, which KMS has never heard of; the
        // desktop's own monitors are lit, and not gamescope's.
        let desktop = lit(&[("DP-1", TV)]);
        for nested in ["SDLWindow", "Wayland", "Headless"] {
            assert_eq!(
                painted(Some(nested), &desktop, STEAMS_SCREEN),
                STEAMS_SCREEN,
                "{nested}"
            );
        }
    }

    #[test]
    fn no_answer_or_nonsense_falls_back_to_the_x_screen() {
        assert_eq!(painted(None, &lit(&[("DP-3", TV)]), STEAMS_SCREEN), STEAMS_SCREEN);
        assert_eq!(painted(Some("DP-3"), &[], STEAMS_SCREEN), STEAMS_SCREEN);
        assert_eq!(
            painted(Some("DP-3"), &lit(&[("DP-3", (0, 0))]), STEAMS_SCREEN),
            STEAMS_SCREEN
        );
    }

    #[test]
    fn connectors_are_named_as_gamescope_names_them() {
        // DRMBackend.cpp: "%s-%d" of drmModeGetConnectorTypeName and the id.
        assert_eq!(connector_name("DP", 3), "DP-3");
        assert_eq!(connector_name("eDP", 1), "eDP-1");
        assert_eq!(connector_name("HDMI-A", 1), "HDMI-A-1");
    }

    /// This machine's lit connectors, to see the KMS read work on real
    /// hardware: `cargo test -p gotg-killswitch -- --ignored --nocapture lit_`.
    #[test]
    #[ignore = "reads this machine's /dev/dri"]
    fn lit_connectors_on_this_machine() {
        let lit = lit_connectors();
        eprintln!("lit: {lit:?}");
        assert!(!lit.is_empty());
    }
}
