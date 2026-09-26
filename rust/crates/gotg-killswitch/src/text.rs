//! Words on the overlay: a line of text as a coverage mask the painter tints.
//!
//! The overlay drew shapes and drawings and nothing that could be read, which
//! is fine for a ring and not for "No controllers connected" or the name of the
//! button a rebind is asking for. One face, the picker's (FreeSans Bold, what
//! pygame draws by default), embedded at build time from GOTG_FONT: no system
//! font scan in a process that starts beside every game, and the same letters
//! on the overlay as in the picker it is taking over from.

use std::sync::OnceLock;

use fontdue::layout::{CoordinateSystem, Layout, LayoutSettings, TextStyle};
use fontdue::{Font, FontSettings};

mod face {
    include!(concat!(env!("OUT_DIR"), "/font.rs"));
}

fn font() -> &'static Font {
    static FONT: OnceLock<Font> = OnceLock::new();
    FONT.get_or_init(|| {
        Font::from_bytes(face::FONT, FontSettings::default())
            .expect("the bundled face is a font: build.rs embedded it from GOTG_FONT")
    })
}

/// One line of text rasterised: its width and height in pixels and a byte of
/// coverage per pixel, row-major. Empty text is 0 x 0.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Mask {
    pub width: u32,
    pub height: u32,
    pub coverage: Vec<u8>,
}

/// `text` set `px` pixels tall, on one line. Newlines are not lines here: a
/// label is one line, and a caller that wants two asks twice.
pub fn render(text: &str, px: f32) -> Mask {
    let text = text.replace(['\n', '\r'], " ");
    let px = px.clamp(4.0, 400.0);
    let font = font();
    let mut layout = Layout::new(CoordinateSystem::PositiveYDown);
    layout.reset(&LayoutSettings::default());
    layout.append(&[font], &TextStyle::new(&text, px, 0));
    let glyphs = layout.glyphs();
    let width = glyphs
        .iter()
        .map(|g| (g.x + g.width as f32).ceil() as i64)
        .max()
        .unwrap_or(0)
        .max(0) as u32;
    let height = layout.height().ceil().max(0.0) as u32;
    if width == 0 || height == 0 {
        return Mask {
            width: 0,
            height: 0,
            coverage: Vec::new(),
        };
    }
    let mut coverage = vec![0u8; (width * height) as usize];
    for glyph in glyphs {
        if glyph.width == 0 || glyph.height == 0 {
            continue;
        }
        let (_, bitmap) = font.rasterize_config(glyph.key);
        let (gx, gy) = (glyph.x.round() as i64, glyph.y.round() as i64);
        for row in 0..glyph.height {
            for column in 0..glyph.width {
                let (x, y) = (gx + column as i64, gy + row as i64);
                if x < 0 || y < 0 || x >= i64::from(width) || y >= i64::from(height) {
                    continue;
                }
                let at = (y as u32 * width + x as u32) as usize;
                let value = bitmap[row * glyph.width + column];
                // Overlapping glyphs (kerned pairs) keep the darker ink.
                coverage[at] = coverage[at].max(value);
            }
        }
    }
    Mask {
        width,
        height,
        coverage,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn inked(mask: &Mask) -> usize {
        mask.coverage.iter().filter(|&&c| c > 128).count()
    }

    #[test]
    fn a_line_of_text_is_ink_about_as_tall_as_asked() {
        let mask = render("No controllers connected", 32.0);
        assert!(inked(&mask) > 200, "it drew something");
        assert!((28..=48).contains(&mask.height), "{} tall for 32 px", mask.height);
        assert_eq!(mask.coverage.len(), (mask.width * mask.height) as usize);
    }

    #[test]
    fn more_words_are_wider_and_bigger_text_is_taller() {
        let short = render("A", 32.0);
        let long = render("A (bottom face)", 32.0);
        assert!(long.width > short.width * 3);
        assert!(render("A", 64.0).height > short.height);
    }

    #[test]
    fn nothing_to_say_is_nothing_drawn() {
        let mask = render("", 32.0);
        assert_eq!((mask.width, mask.height), (0, 0));
        assert_eq!(render("   ", 32.0).coverage.iter().filter(|&&c| c > 0).count(), 0);
    }

    #[test]
    fn a_label_is_one_line_whatever_it_holds() {
        let one = render("C-up", 24.0);
        let broken = render("C-\nup", 24.0);
        assert_eq!(one.height, broken.height, "a newline is not a second line");
    }
}
