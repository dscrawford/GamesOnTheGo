//! Each console's controller, as the picker's config/controllers describes it:
//! which danstick layout its buttons are walked in, its drawing, and where on
//! the drawing each button is. The rebind shows the drawing over the game
//! and rings the button danstick is asking for.
//!
//! Read at build time from the same files the picker reads, so the two draw
//! the same controller and ring the same place.

use resvg::tiny_skia::{Pixmap, Transform};
use resvg::usvg::{Options, Tree};

/// One control danstick walks, and where it sits on the drawing (0..1 of the
/// drawing's width and height), if the drawing shows it at all -- an N64's
/// Z is underneath the pad, and bound all the same.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Control {
    pub id: &'static str,
    pub label: &'static str,
    pub anchor: Option<(f32, f32)>,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Console {
    pub name: &'static str,
    pub platforms: &'static [&'static str],
    pub layout: &'static str,
    /// Into [`ARTWORK`].
    pub artwork: usize,
    /// The drawing's width over its height.
    pub aspect: f32,
    pub controls: &'static [Control],
}

include!(concat!(env!("OUT_DIR"), "/consoles.rs"));

/// The console a game on `platform` is played with; the generic pad for a
/// platform nobody has described, as the picker falls back.
pub fn for_platform(platform: &str) -> usize {
    CONSOLES
        .iter()
        .position(|console| console.platforms.contains(&platform))
        .or_else(|| CONSOLES.iter().position(|console| console.name == "generic"))
        .unwrap_or(0)
}

impl Console {
    /// danstick's scope for "every game on this console", as the gate asks.
    pub fn scope(&self) -> String {
        format!("console:{}", self.layout)
    }

    /// The control danstick is asking for, by its id.
    pub fn control(&self, id: &str) -> Option<usize> {
        self.controls.iter().position(|control| control.id == id)
    }
}

/// A console's drawing `height` pixels tall, in its own colours: row-major
/// RGBA with straight alpha, and its width. Not a silhouette: somebody
/// looking for the button they are asked to press needs the pad itself.
pub fn picture(console: usize, height: u32) -> Option<(u32, Vec<u8>)> {
    let console = CONSOLES.get(console)?;
    let svg = ARTWORK.get(console.artwork)?;
    let tree = Tree::from_data(svg, &Options::default()).ok()?;
    let size = tree.size();
    let scale = height as f32 / size.height();
    let width = (size.width() * scale).round().max(1.0) as u32;
    let mut pixmap = Pixmap::new(width, height.max(1))?;
    resvg::render(&tree, Transform::from_scale(scale, scale), &mut pixmap.as_mut());
    // tiny-skia's pixels are premultiplied; the painter's texture is not.
    let pixels = pixmap
        .pixels()
        .iter()
        .flat_map(|px| {
            let straight = px.demultiply();
            [
                straight.red(),
                straight.green(),
                straight.blue(),
                straight.alpha(),
            ]
        })
        .collect();
    Some((width, pixels))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn named(name: &str) -> &'static Console {
        CONSOLES
            .iter()
            .find(|console| console.name == name)
            .unwrap_or_else(|| panic!("no {name}"))
    }

    #[test]
    fn a_platform_finds_its_console_and_an_unknown_one_the_generic_pad() {
        assert_eq!(CONSOLES[for_platform("n64")].name, "n64");
        assert_eq!(CONSOLES[for_platform("gamecube")].name, "gamecube");
        assert_eq!(CONSOLES[for_platform("no-such-platform")].name, "generic");
    }

    #[test]
    fn the_scope_is_the_one_the_gate_asks_for() {
        assert_eq!(named("n64").scope(), "console:n64");
    }

    #[test]
    fn a_button_the_drawing_names_by_the_consoles_letters_is_still_found() {
        // danstick says `dpup`; the N64 drawing's circle is `Up`.
        let n64 = named("n64");
        let up = n64.control("dpup").map(|at| n64.controls[at]);
        assert!(up.is_some_and(|c| c.anchor.is_some()), "{up:?}");
        let a = n64.control("a").map(|at| n64.controls[at]);
        let (u, v) = a.and_then(|c| c.anchor).expect("A is on the drawing");
        assert!((0.0..=1.0).contains(&u) && (0.0..=1.0).contains(&v));
    }

    #[test]
    fn a_button_the_drawing_cannot_show_is_walked_all_the_same() {
        // Z is underneath an N64 pad; the drawing is from the front.
        let n64 = named("n64");
        let z = n64.control("lefttrigger").map(|at| n64.controls[at]);
        assert_eq!(z.map(|c| c.anchor), Some(None));
    }

    #[test]
    fn every_consoles_drawing_renders_in_colour() {
        for (at, console) in CONSOLES.iter().enumerate() {
            let (width, pixels) =
                picture(at, 60).unwrap_or_else(|| panic!("{} did not render", console.name));
            assert_eq!(pixels.len(), (width * 60 * 4) as usize, "{}", console.name);
            assert!(
                (width as f32 / 60.0 - console.aspect).abs() < 0.1,
                "{}: {width} wide is not its aspect {}",
                console.name,
                console.aspect
            );
            assert!(
                pixels
                    .chunks_exact(4)
                    .any(|px| px[3] > 200 && px[..3] != [255, 255, 255]),
                "{} drew nothing but white",
                console.name
            );
        }
    }
}
