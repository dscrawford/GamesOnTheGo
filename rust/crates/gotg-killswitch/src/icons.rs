//! Which drawing stands for the controller somebody is holding -- the
//! picker's drawings, by the picker's rules (gotg_ui/icons.py, read from the
//! same config/icons.yaml at build time) -- and the drawing as pixels.
//!
//! The bar drew a plain ring for every pad, where the picker shows the pad:
//! a Steam Controller's outline filling in as it is held. The two now agree
//! on both the picture and which picture.

use std::path::Path;

use resvg::tiny_skia::{Pixmap, Transform};
use resvg::usvg::{Options, Tree};

mod table {
    include!(concat!(env!("OUT_DIR"), "/icons.rs"));
}

pub use table::{FALLBACK, NAMES};

/// The drawing for a pad: by `vendor:product` first, then by the first rule
/// whose words its name contains, then the fallback. `ids` wins where it is
/// known because a name is what a maker wrote and an id is what a device is:
/// a Deck calls itself "Valve Software Steam Controller", as a Puck does, and
/// only 28de:1205 says which of the two somebody is holding.
pub fn icon_for(name: &str, ids: Option<&str>) -> u8 {
    if let Some(ids) = ids {
        let wanted = ids.trim().to_lowercase();
        if let Some(&(_, icon)) = table::IDS.iter().find(|(needle, _)| *needle == wanted) {
            return icon;
        }
    }
    let lowered = name.to_lowercase();
    table::RULES
        .iter()
        .find(|(needle, _)| lowered.contains(needle))
        .map_or(FALLBACK, |&(_, icon)| icon)
}

/// `28de:1205` for the node danstick names, read from sysfs under `root`: an
/// evdev node's `id/vendor` and `id/product`, or a hidraw node's `HID_ID`
/// (a Deck and a Steam Controller have no joystick evdev node at all).
pub fn ids_for(node: &str, root: &Path) -> Option<String> {
    // The name comes off danstick's socket: only a device node's own name is
    // joined onto sysfs, never a `..` or anything else that is not one.
    let key = node.rsplit('/').next().filter(|key| {
        let digits = key.strip_prefix("event").or_else(|| key.strip_prefix("hidraw"));
        digits.is_some_and(|digits| !digits.is_empty() && digits.bytes().all(|b| b.is_ascii_digit()))
    })?;
    if key.starts_with("hidraw") {
        let uevent =
            std::fs::read_to_string(root.join("class/hidraw").join(key).join("device/uevent")).ok()?;
        let id = uevent.lines().find_map(|line| line.strip_prefix("HID_ID="))?;
        let mut parts = id
            .split(':')
            .skip(1)
            .map(|part| u32::from_str_radix(part, 16).ok());
        let (vendor, product) = (parts.next()??, parts.next()??);
        return Some(format!("{vendor:04x}:{product:04x}"));
    }
    let device = root.join("class/input").join(key).join("device/id");
    let read = |file: &str| {
        std::fs::read_to_string(device.join(file))
            .ok()
            .map(|text| text.trim().to_lowercase())
    };
    Some(format!("{}:{}", read("vendor")?, read("product")?))
}

/// The drawing for a pad danstick names, as the running machine describes it.
pub fn resolve(node: &str, name: &str) -> u8 {
    let ids = if node.is_empty() {
        None
    } else {
        ids_for(node, Path::new("/sys"))
    };
    icon_for(name, ids.as_deref())
}

/// A drawing `height` pixels tall as a white silhouette, row-major RGBA with
/// straight alpha, and its width. White so a tint is a multiply: SDL's colour
/// mod turns it into any seat's colour. A silhouette because not every drawing
/// is one colour -- one pad has coloured buttons -- and a row of seats should
/// look like one set, as the picker flattens them for the same reason.
pub fn silhouette(icon: u8, height: u32) -> Option<(u32, Vec<u8>)> {
    let svg = table::SVGS.get(usize::from(icon))?;
    let tree = Tree::from_data(svg, &Options::default()).ok()?;
    let size = tree.size();
    let scale = height as f32 / size.height();
    let width = (size.width() * scale).round().max(1.0) as u32;
    let mut pixmap = Pixmap::new(width, height.max(1))?;
    resvg::render(&tree, Transform::from_scale(scale, scale), &mut pixmap.as_mut());
    // Premultiplied RGBA in; only the coverage is kept.
    let pixels = pixmap
        .data()
        .chunks_exact(4)
        .flat_map(|px| [255, 255, 255, px[3]])
        .collect();
    Some((width, pixels))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn named(icon: u8) -> &'static str {
        NAMES[usize::from(icon)]
    }

    #[test]
    fn a_pad_is_drawn_by_the_first_rule_its_name_matches() {
        assert_eq!(named(icon_for("Xbox Wireless Controller", None)), "xbox");
        assert_eq!(
            named(icon_for("Valve Software Steam Controller Puck", None)),
            "steam"
        );
        // Steam's virtual pad is an Xbox pad wearing another hat, so "steam
        // virtual" is tried before "steam".
        assert_eq!(named(icon_for("Steam Virtual Gamepad", None)), "xbox");
        assert_eq!(
            named(icon_for("Keyboard", None)),
            "keyboard-mouse",
            "the keyboard seat is the desk"
        );
    }

    #[test]
    fn a_deck_is_known_by_its_ids_whatever_it_calls_itself() {
        assert_eq!(
            named(icon_for("Valve Software Steam Controller", Some("28de:1205"))),
            "steamdeck"
        );
        assert_eq!(
            named(icon_for("Valve Software Steam Controller", Some("28DE:1205 "))),
            "steamdeck"
        );
        assert_eq!(
            named(icon_for("Valve Software Steam Controller", Some("28de:1304"))),
            "steam"
        );
    }

    #[test]
    fn a_pad_nobody_has_a_rule_for_is_still_a_pad() {
        assert_eq!(icon_for("Mystery Box 3000", None), FALLBACK);
        assert_eq!(named(FALLBACK), "generic");
    }

    #[test]
    fn every_drawing_renders_as_a_silhouette_of_the_height_asked() {
        for (icon, name) in NAMES.iter().enumerate() {
            let (width, pixels) =
                silhouette(icon as u8, 40).unwrap_or_else(|| panic!("{name} did not render"));
            assert_eq!(pixels.len(), (width * 40 * 4) as usize, "{name}");
            assert!(
                pixels.chunks_exact(4).all(|px| px[..3] == [255, 255, 255]),
                "{name} is not white"
            );
            assert!(
                pixels.chunks_exact(4).any(|px| px[3] > 200),
                "{name} drew nothing"
            );
        }
    }

    #[test]
    fn ids_come_from_sysfs_for_either_kind_of_node() {
        let root = std::env::temp_dir().join(format!("gotg-icons-sysfs-{}", std::process::id()));
        let event = root.join("class/input/event9/device/id");
        let hidraw = root.join("class/hidraw/hidraw4/device");
        std::fs::create_dir_all(&event).expect("fixture");
        std::fs::create_dir_all(&hidraw).expect("fixture");
        std::fs::write(event.join("vendor"), "045e\n").expect("fixture");
        std::fs::write(event.join("product"), "028E\n").expect("fixture");
        std::fs::write(
            hidraw.join("uevent"),
            "DRIVER=hid-steam\nHID_ID=0003:000028DE:00001205\n",
        )
        .expect("fixture");
        assert_eq!(ids_for("/dev/input/event9", &root).as_deref(), Some("045e:028e"));
        assert_eq!(ids_for("/dev/hidraw4", &root).as_deref(), Some("28de:1205"));
        assert_eq!(
            ids_for("hidraw4", &root).as_deref(),
            Some("28de:1205"),
            "danstick's bare form too"
        );
        assert_eq!(
            ids_for("/dev/input/event77", &root),
            None,
            "a node that is not there"
        );
        assert_eq!(ids_for("", &root), None, "a keyboard seat names no node");
        for odd in ["..", ".", "/dev/input/..", "event9/../x", "mice", "event9x/../"] {
            assert_eq!(ids_for(odd, &root), None, "{odd:?} is not a device node");
        }
        let _ = std::fs::remove_dir_all(&root);
    }
}
