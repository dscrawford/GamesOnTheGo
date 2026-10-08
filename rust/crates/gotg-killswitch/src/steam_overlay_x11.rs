//! Seeing Steam's overlay from gamescope's X server: the socket half of
//! `steam_overlay`, which says what the properties add up to. Untested --
//! it needs gamescope -- so it only reads and feeds `Windows`.
//!
//! Never blocks the chord's loop on anything but the server's own replies to
//! a few property reads, and every failure is one stderr line and no watcher:
//! a kill switch that stalls on a display call is the bug the painter process
//! exists to avoid.
//!
//! `GOTG_OVERLAY_STEAM_FILE=<path>` stands in for all of it: the file's
//! existence is the overlay's state, so the e2e can touch and remove it.

use std::path::PathBuf;

use x11rb::connection::Connection as _;
use x11rb::protocol::Event;
use x11rb::protocol::xproto::{
    Atom, AtomEnum, ChangeWindowAttributesAux, ConnectionExt as _, EventMask, MapState, Window,
};
use x11rb::rust_connection::RustConnection;

use crate::steam_overlay::{Props, Windows};

#[derive(Debug)]
pub enum Watcher {
    File(PathBuf),
    X(Box<XWatcher>),
}

impl Watcher {
    /// The file if one is named, else the X display under gamescope, else
    /// nothing watched.
    pub fn from_env() -> Option<Self> {
        if let Some(path) = std::env::var_os("GOTG_OVERLAY_STEAM_FILE").filter(|p| !p.is_empty()) {
            return Some(Self::File(path.into()));
        }
        std::env::var_os("GAMESCOPE_WAYLAND_DISPLAY")?;
        match XWatcher::open() {
            Ok(watcher) => Some(Self::X(Box::new(watcher))),
            Err(error) => {
                eprintln!("gotg-killswitch: not watching Steam's overlay: {error}");
                None
            }
        }
    }

    /// Whether Steam's overlay is up, after taking in whatever X has said.
    pub fn up(&mut self) -> bool {
        match self {
            Self::File(path) => path.exists(),
            Self::X(watcher) => watcher.up(),
        }
    }
}

impl std::fmt::Debug for XWatcher {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("XWatcher")
            .field("windows", &self.windows)
            .finish_non_exhaustive()
    }
}

type Failure = Box<dyn std::error::Error>;

pub struct XWatcher {
    connection: RustConnection,
    atoms: [Atom; 3],
    windows: Windows,
    /// The server went away: not read again.
    dead: bool,
}

/// `STEAM_OVERLAY`, `STEAM_INPUT_FOCUS`, `_NET_WM_WINDOW_OPACITY`.
const NAMES: [&[u8]; 3] = [b"STEAM_OVERLAY", b"STEAM_INPUT_FOCUS", b"_NET_WM_WINDOW_OPACITY"];

impl XWatcher {
    fn open() -> Result<Self, Failure> {
        let (connection, screen) = RustConnection::connect(None)?;
        let root = connection.setup().roots[screen].root;
        let mut atoms = [0; 3];
        for (slot, name) in atoms.iter_mut().zip(NAMES) {
            *slot = connection.intern_atom(false, name)?.reply()?.atom;
        }
        connection.change_window_attributes(
            root,
            &ChangeWindowAttributesAux::new().event_mask(EventMask::SUBSTRUCTURE_NOTIFY),
        )?;
        let mut watcher = Self {
            connection,
            atoms,
            windows: Windows::default(),
            dead: false,
        };
        let children = watcher.connection.query_tree(root)?.reply()?.children;
        for child in children {
            watcher.watch(child);
        }
        watcher.connection.flush()?;
        Ok(watcher)
    }

    /// Property changes on `id` from now on, and what it carries now.
    fn watch(&mut self, id: Window) {
        let _ = self.connection.change_window_attributes(
            id,
            &ChangeWindowAttributesAux::new().event_mask(EventMask::PROPERTY_CHANGE),
        );
        self.read(id);
    }

    fn read(&mut self, id: Window) {
        match self.props(id) {
            Ok(props) => self.windows.note(id, props),
            // Gone between the event and the read.
            Err(_) => self.windows.gone(id),
        }
    }

    fn props(&self, id: Window) -> Result<Props, Failure> {
        let attributes = self.connection.get_window_attributes(id)?;
        let cardinals = self.atoms.map(|atom| {
            self.connection
                .get_property(false, id, atom, AtomEnum::CARDINAL, 0, 1)
        });
        // A window with no opacity property is opaque to gamescope; Steam hides
        // its overlay by setting 0, so missing must not read as hidden.
        let mut value = [0u32, 0, u32::MAX];
        for (slot, cookie) in value.iter_mut().zip(cardinals) {
            if let Some(found) = cookie?.reply()?.value32().and_then(|mut v| v.next()) {
                *slot = found;
            }
        }
        Ok(Props {
            overlay: value[0] != 0,
            input_focus: value[1] != 0,
            opacity: value[2],
            mapped: attributes.reply()?.map_state == MapState::VIEWABLE,
        })
    }

    fn up(&mut self) -> bool {
        while !self.dead {
            match self.connection.poll_for_event() {
                Ok(Some(event)) => self.take(event),
                Ok(None) => break,
                Err(error) => {
                    eprintln!("gotg-killswitch: no longer watching Steam's overlay: {error}");
                    self.dead = true;
                    self.windows = Windows::default();
                }
            }
        }
        self.windows.up()
    }

    fn take(&mut self, event: Event) {
        match event {
            Event::CreateNotify(e) => self.watch(e.window),
            Event::DestroyNotify(e) => self.windows.gone(e.window),
            Event::MapNotify(e) => self.read(e.window),
            Event::UnmapNotify(e) => self.read(e.window),
            Event::PropertyNotify(e) if self.atoms.contains(&e.atom) => self.read(e.window),
            _ => {}
        }
    }
}
