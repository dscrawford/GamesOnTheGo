//! gotg-pads — what SDL actually sees, as JSON.
//!
//! Everything downstream needs to agree with the emulators about which
//! physical controller is which, and the only way to be sure of that is to ask
//! the same library they ask. sdl-jstest is not a substitute: it links SDL2, so
//! it cannot see hidapi-only devices at all, and its output is nobody's
//! stability contract.
//!
//! A dumper, not a library. It prints and exits. The decisions in it -- ares'
//! identity string, its slot numbering -- are plain functions with tests; the
//! rest is asking SDL.

use std::ffi::{CStr, c_char, c_int};

use serde_json::{Map, Value, json};

mod sdl;

/// The GUID SDL reports when it has nothing better to say.
const EMPTY_GUID: &str = "00000000000000000000000000000000";

/// The string ares identifies a controller by, built exactly as
/// ruby/input/joypad/sdl.cpp does — the SDL GUID, or a VID/PID pair when the
/// GUID comes back all zeros. It matters that this matches character for
/// character: ares looks a binding up by comparing this against the
/// identifier stored in settings.bml, so a difference of any kind is a
/// controller that silently has no buttons.
pub fn ares_identity(guid: &str, vid: u16, pid: u16) -> String {
    if !guid.is_empty() && guid != EMPTY_GUID {
        return guid.to_owned();
    }
    // ares substitutes its generic ids for a missing vendor or product, and
    // formats both in decimal: HID::Joypad::GenericVendorID is 0 and
    // GenericProductID is 3.
    let pid = if pid == 0 { 0x0003 } else { pid };
    format!("VID:{vid}|PID:{pid}")
}

/// ares' slot for each device: how many devices with the same *identity* came
/// before it in SDL's enumeration order. Two identical pads are told apart by
/// nothing else, so this has to match ruby/input/joypad/sdl.cpp exactly —
/// getting it wrong silently swaps player one and player two.
pub fn slots(identities: &[String]) -> Vec<usize> {
    identities
        .iter()
        .enumerate()
        .map(|(i, identity)| identities[..i].iter().filter(|other| *other == identity).count())
        .collect()
}

/// A Steam Virtual Gamepad is Steam Input presenting something else. Its
/// player index is the seat Steam has already assigned, which is used instead
/// of matching the device itself.
pub fn is_steam_virtual(vid: u16, pid: u16) -> bool {
    vid == 0x28de && pid == 0x11ff
}

/// Only an evdev node can be bound by anything that speaks evdev, and
/// downstream has to be able to tell a hidapi-only pad apart.
pub fn evdev_of(path: Option<&str>) -> Option<&str> {
    path.filter(|path| path.starts_with("/dev/input/event"))
}

/// One device as SDL enumerated it, before it is written out.
#[derive(Debug, Clone, PartialEq)]
pub struct Pad {
    pub instance: u32,
    pub name: Option<String>,
    pub guid: String,
    pub vid: u16,
    pub pid: u16,
    pub gamepad: bool,
    pub path: Option<String>,
    pub steam_slot: Option<i32>,
    pub motion: bool,
    /// The standard elements a person names, and the raw element behind each.
    pub map: Option<Map<String, Value>>,
}

/// The JSON array this program prints, keys in the order they always were.
pub fn report(pads: &[Pad]) -> Value {
    let identities: Vec<String> = pads
        .iter()
        .map(|pad| ares_identity(&pad.guid, pad.vid, pad.pid))
        .collect();
    let slots = slots(&identities);
    let entries = pads
        .iter()
        .zip(identities)
        .zip(slots)
        .map(|((pad, identity), slot)| {
            json!({
                "instance": pad.instance,
                "name": pad.name.as_deref().filter(|name| !name.is_empty()),
                "guid": pad.guid,
                // identity + slot is what an ares binding is keyed on, so it is
                // emitted ready to use rather than reassembled downstream.
                "identity": identity,
                "slot": slot,
                "vid": format!("{:04x}", pad.vid),
                "pid": format!("{:04x}", pad.pid),
                "gamepad": pad.gamepad,
                "path": pad.path.as_deref().filter(|path| !path.is_empty()),
                "evdev": evdev_of(pad.path.as_deref()),
                "steamSlot": pad.steam_slot,
                "motion": pad.motion,
                "map": pad.map,
            })
        })
        .collect();
    Value::Array(entries)
}

fn main() {
    match sdl::enumerate() {
        Ok(pads) => {
            // Pretty, as the C program printed it: people read this too.
            match serde_json::to_string_pretty(&report(&pads)) {
                Ok(text) => println!("{text}"),
                Err(error) => {
                    eprintln!("gotg-pads: {error}");
                    std::process::exit(1);
                }
            }
        }
        Err(error) => {
            eprintln!("gotg-pads: {error}");
            std::process::exit(1);
        }
    }
}

/// A C string SDL owns, copied; None for a null pointer.
///
/// # Safety
/// `text` is null or points at a NUL-terminated string that outlives the call.
unsafe fn owned(text: *const c_char) -> Option<String> {
    if text.is_null() {
        return None;
    }
    // SAFETY: non-null and NUL-terminated, per this function's contract.
    Some(unsafe { CStr::from_ptr(text) }.to_string_lossy().into_owned())
}

/// SDL's GUID as the 32 hex digits SDL_GUIDToString writes.
fn guid_text(guid: sdl3_sys::everything::SDL_GUID) -> String {
    let mut buffer = [0 as c_char; 33];
    // SAFETY: the buffer is the 33 bytes SDL documents for a GUID string.
    unsafe { sdl3_sys::everything::SDL_GUIDToString(guid, buffer.as_mut_ptr(), buffer.len() as c_int) };
    // SAFETY: SDL NUL-terminates within the length it was given.
    unsafe { owned(buffer.as_ptr()) }.unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pad(guid: &str, vid: u16, pid: u16) -> Pad {
        Pad {
            instance: 1,
            name: Some("Pad".into()),
            guid: guid.into(),
            vid,
            pid,
            gamepad: true,
            path: Some("/dev/input/event7".into()),
            steam_slot: None,
            motion: false,
            map: None,
        }
    }

    #[test]
    fn a_real_guid_is_the_identity() {
        let guid = "030000005e0400008e02000010010000";
        assert_eq!(ares_identity(guid, 0x045e, 0x028e), guid);
    }

    #[test]
    fn an_empty_guid_falls_back_to_ares_decimal_ids() {
        assert_eq!(ares_identity(EMPTY_GUID, 0x045e, 0x028e), "VID:1118|PID:654");
        assert_eq!(ares_identity("", 0x045e, 0x028e), "VID:1118|PID:654");
    }

    #[test]
    fn a_missing_product_is_ares_generic_three() {
        assert_eq!(ares_identity(EMPTY_GUID, 0, 0), "VID:0|PID:3");
    }

    #[test]
    fn identical_pads_count_up_and_different_ones_start_at_zero() {
        let ids: Vec<String> = ["a", "b", "a", "a", "b"].iter().map(|s| s.to_string()).collect();
        assert_eq!(slots(&ids), vec![0, 0, 1, 2, 1]);
    }

    #[test]
    fn only_an_event_node_is_evdev() {
        assert_eq!(evdev_of(Some("/dev/input/event3")), Some("/dev/input/event3"));
        assert_eq!(evdev_of(Some("/dev/hidraw2")), None);
        assert_eq!(evdev_of(None), None);
    }

    #[test]
    fn steam_input_is_recognised_by_its_ids() {
        assert!(is_steam_virtual(0x28de, 0x11ff));
        assert!(!is_steam_virtual(0x28de, 0x1205));
    }

    #[test]
    fn the_report_keeps_its_keys_and_their_order() {
        let mut hidapi = pad(EMPTY_GUID, 0x28de, 0x1205);
        hidapi.path = Some("/dev/hidraw4".into());
        hidapi.name = Some(String::new());
        let out = report(&[pad("g", 1, 2), pad("g", 1, 2), hidapi]);
        let first = &out[0];
        let keys: Vec<&str> = first
            .as_object()
            .map(|o| o.keys().map(String::as_str).collect())
            .unwrap_or_default();
        assert_eq!(
            keys,
            [
                "instance",
                "name",
                "guid",
                "identity",
                "slot",
                "vid",
                "pid",
                "gamepad",
                "path",
                "evdev",
                "steamSlot",
                "motion",
                "map"
            ]
        );
        assert_eq!(out[1]["slot"], 1, "the second identical pad is slot one");
        assert_eq!(out[0]["vid"], "0001");
        assert_eq!(out[2]["identity"], "VID:10462|PID:4613");
        assert_eq!(out[2]["evdev"], Value::Null, "a hidapi pad has no evdev node");
        assert_eq!(out[2]["name"], Value::Null, "an empty name is null, as it was");
    }
}
