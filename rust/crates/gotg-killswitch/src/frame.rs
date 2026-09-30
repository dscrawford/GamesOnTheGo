//! What the kill switch tells its painter to draw: one frame of the bar,
//! small enough to cross a pipe in a single atomic write.
//!
//! The drawing is a separate process. The kill switch's loop is the one thing
//! that must keep running while a game is hung -- it is what stops the game --
//! and drawing means talking to a compositor: a round trip, a present that
//! waits for the panel, a connect. Any of those can stall, and a stall in the
//! same loop was a kill chord that did nothing. So the kill switch keeps the
//! chord, danstick's socket and every decision, and hands the painter only this.
//! A painter that hangs misses frames; the kill switch does not notice.
//!
//! Encoded field by field in native byte order: both ends are this binary.

use crate::pairing::{HOLDS_MAX, Hold, JOINED_MAX};

/// "GOSV", so a torn or foreign read is refused.
pub const MAGIC: u32 = 0x5653_4f47;

/// Bytes on the pipe: six words, the five arrays a word per entry, the
/// rebind's thirteen words, the menu's, and one for what the bar is saying.
pub const SIZE: usize = 4 * (6 + HOLDS_MAX * 3 + JOINED_MAX * 2 + REBIND_WORDS + MENU_WORDS + 1);

/// Owner, rows, focus, carried, two fills, the icons, the off mask, the
/// console, and each seat's presses in two words.
const MENU_WORDS: usize = 8 + ROWS_MAX + 2 * ROWS_MAX;

/// Seats a menu frame lists.
pub const ROWS_MAX: usize = crate::menu::SEATS_MAX;

/// A seat with nobody in it, where a drawing would be.
pub const EMPTY_SEAT: u8 = u8::MAX;

/// The menu as the bar draws it: whose it is, the seats (a drawing each, or
/// EMPTY_SEAT), the row under the cursor (rows past the seats are Exit), the
/// seat being carried (0 none), and the two holds' fills.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct MenuFrame {
    pub owner: i32,
    pub rows: u32,
    pub icons: [u8; ROWS_MAX],
    pub focus: u32,
    pub carried: i32,
    pub a_fill: f32,
    pub b_fill: f32,
    /// A bit per seat, seat 1 lowest: the game does not hear it.
    pub off: u32,
    /// The game's controller, drawn for everybody to try their buttons on.
    pub console: u32,
    /// Per seat, the console's controls it has down, a bit each in order.
    pub pressed: [u64; ROWS_MAX],
}

/// What the bar says in words when it says something on its own.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum Saying {
    #[default]
    Nothing,
    /// The game has been stopped from the menu and its saves are on their way.
    Saving,
}

const REBIND_WORDS: usize = 13;

/// A rebind as the bar draws it: which seat, on which console's drawing,
/// which control (an index into that console's, -1 before the first step),
/// how far through, the early-finish hold, how it ended, and what the
/// player is pressing.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Rebinding {
    pub player: i32,
    pub console: u32,
    pub control: i32,
    pub index: i32,
    pub total: i32,
    pub finish: f32,
    /// 0 still walking, 1 kept, 2 given up on.
    pub ended: u32,
    /// The console's controls down, a bit each in its order.
    pub pressed: u64,
    /// Left stick x, y, right stick x, y: -1..1, y down.
    pub sticks: [f32; 4],
}

/// One frame, in fixed arrays as it crosses the pipe: nothing to allocate
/// per frame at either end, and no way to hold more than a frame can say.
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct Frame {
    /// The bar: 0 out of sight, 1 all the way down.
    pub position: f32,
    /// 0..1 through the exit hold.
    pub exit_progress: f32,
    hold_fraction: [f32; HOLDS_MAX],
    hold_player: [i32; HOLDS_MAX],
    hold_icon: [u8; HOLDS_MAX],
    hold_count: usize,
    joined: [i32; JOINED_MAX],
    joined_icon: [u8; JOINED_MAX],
    joined_count: usize,
    /// On the wire as player 0 when there is none.
    pub rebind: Option<Rebinding>,
    /// On the wire as owner 0 when there is none.
    pub menu: Option<MenuFrame>,
    pub saying: Saying,
    /// danstick has nobody seated and nobody joining.
    pub nobody: bool,
}

impl Frame {
    /// Filled from what the kill switch knows, cut to what a frame holds.
    /// `joined` is (player, drawing), as `Pairing::joined` gives it.
    pub fn pack(position: f64, exit_progress: f64, holds: &[Hold], joined: &[(i32, u8)]) -> Self {
        let mut frame = Self {
            position: position as f32,
            exit_progress: exit_progress as f32,
            hold_count: holds.len().min(HOLDS_MAX),
            joined_count: joined.len().min(JOINED_MAX),
            ..Self::default()
        };
        for (i, hold) in holds.iter().take(HOLDS_MAX).enumerate() {
            frame.hold_fraction[i] = hold.fraction as f32;
            frame.hold_player[i] = hold.player;
            frame.hold_icon[i] = hold.icon;
        }
        for (i, &(player, icon)) in joined.iter().take(JOINED_MAX).enumerate() {
            frame.joined[i] = player;
            frame.joined_icon[i] = icon;
        }
        frame
    }

    /// This frame, saying whether nobody is seated.
    pub fn with_nobody(self, nobody: bool) -> Self {
        Self { nobody, ..self }
    }

    /// This frame, with the menu on it.
    pub fn with_menu(self, menu: Option<MenuFrame>) -> Self {
        Self { menu, ..self }
    }

    /// This frame, saying something.
    pub fn with_saying(self, saying: Saying) -> Self {
        Self { saying, ..self }
    }

    /// This frame, with a rebind on it.
    pub fn with_rebind(self, rebind: Option<Rebinding>) -> Self {
        Self { rebind, ..self }
    }

    /// How far each joining pad is, oldest press first.
    pub fn hold_fraction(&self) -> &[f32] {
        &self.hold_fraction[..self.hold_count]
    }

    /// The seat each joining pad is filling towards.
    pub fn hold_player(&self) -> &[i32] {
        &self.hold_player[..self.hold_count]
    }

    /// The drawing for each joining pad.
    pub fn hold_icon(&self) -> &[u8] {
        &self.hold_icon[..self.hold_count]
    }

    /// Seats just taken, oldest first.
    pub fn joined(&self) -> &[i32] {
        &self.joined[..self.joined_count]
    }

    /// The drawing for each seat just taken.
    pub fn joined_icon(&self) -> &[u8] {
        &self.joined_icon[..self.joined_count]
    }

    pub fn encode(&self) -> [u8; SIZE] {
        let mut out = [0u8; SIZE];
        let mut words = out.chunks_exact_mut(4);
        let mut put = |bytes: [u8; 4]| {
            if let Some(word) = words.next() {
                word.copy_from_slice(&bytes);
            }
        };
        put(MAGIC.to_ne_bytes());
        put(self.position.to_ne_bytes());
        put(self.exit_progress.to_ne_bytes());
        put((self.hold_count as u32).to_ne_bytes());
        put((self.joined_count as u32).to_ne_bytes());
        put(u32::from(self.nobody).to_ne_bytes());
        self.hold_fraction
            .iter()
            .for_each(|value| put(value.to_ne_bytes()));
        self.hold_player.iter().for_each(|value| put(value.to_ne_bytes()));
        self.hold_icon
            .iter()
            .for_each(|&value| put(u32::from(value).to_ne_bytes()));
        self.joined.iter().for_each(|value| put(value.to_ne_bytes()));
        self.joined_icon
            .iter()
            .for_each(|&value| put(u32::from(value).to_ne_bytes()));
        let rebind = self.rebind.unwrap_or(Rebinding {
            player: 0,
            console: 0,
            control: -1,
            index: 0,
            total: 0,
            finish: 0.0,
            ended: 0,
            pressed: 0,
            sticks: [0.0; 4],
        });
        put(rebind.player.to_ne_bytes());
        put(rebind.console.to_ne_bytes());
        put(rebind.control.to_ne_bytes());
        put(rebind.index.to_ne_bytes());
        put(rebind.total.to_ne_bytes());
        put(rebind.finish.to_ne_bytes());
        put(rebind.ended.to_ne_bytes());
        put((rebind.pressed as u32).to_ne_bytes());
        put(((rebind.pressed >> 32) as u32).to_ne_bytes());
        rebind.sticks.iter().for_each(|value| put(value.to_ne_bytes()));
        let menu = self.menu.unwrap_or(MenuFrame {
            owner: 0,
            rows: 0,
            icons: [EMPTY_SEAT; ROWS_MAX],
            focus: 0,
            carried: 0,
            a_fill: 0.0,
            b_fill: 0.0,
            off: 0,
            console: 0,
            pressed: [0; ROWS_MAX],
        });
        put(menu.owner.to_ne_bytes());
        put(menu.rows.to_ne_bytes());
        put(menu.focus.to_ne_bytes());
        put(menu.carried.to_ne_bytes());
        put(menu.a_fill.to_ne_bytes());
        put(menu.b_fill.to_ne_bytes());
        menu.icons
            .iter()
            .for_each(|&icon| put(u32::from(icon).to_ne_bytes()));
        put(menu.off.to_ne_bytes());
        put(menu.console.to_ne_bytes());
        for pressed in menu.pressed {
            put((pressed as u32).to_ne_bytes());
            put(((pressed >> 32) as u32).to_ne_bytes());
        }
        put(match self.saying {
            Saying::Nothing => 0u32,
            Saying::Saving => 1,
        }
        .to_ne_bytes());
        out
    }

    /// The frame in these bytes, if they are one this painter can draw: the
    /// right magic, and counts no larger than the arrays, whatever arrived.
    /// A drawing past the table is drawn as the fallback, by `icons`.
    pub fn decode(bytes: &[u8; SIZE]) -> Option<Self> {
        let word =
            |i: usize| -> [u8; 4] { [bytes[i * 4], bytes[i * 4 + 1], bytes[i * 4 + 2], bytes[i * 4 + 3]] };
        let count = |i: usize| u32::from_ne_bytes(word(i)) as usize;
        let icon = |i: usize| u8::try_from(u32::from_ne_bytes(word(i))).unwrap_or(u8::MAX);
        let (hold_count, joined_count) = (count(3), count(4));
        if u32::from_ne_bytes(word(0)) != MAGIC || hold_count > HOLDS_MAX || joined_count > JOINED_MAX {
            return None;
        }
        let holds = 6;
        let joined = holds + 3 * HOLDS_MAX;
        let rebind = joined + 2 * JOINED_MAX;
        let player = i32::from_ne_bytes(word(rebind));
        let menu = rebind + REBIND_WORDS;
        let owner = i32::from_ne_bytes(word(menu));
        let fill = |i: usize| {
            let value = f32::from_ne_bytes(word(i));
            if value.is_finite() {
                value.clamp(0.0, 1.0)
            } else {
                0.0
            }
        };
        Some(Self {
            menu: (owner > 0).then(|| MenuFrame {
                owner,
                rows: u32::from_ne_bytes(word(menu + 1)).min(ROWS_MAX as u32),
                focus: u32::from_ne_bytes(word(menu + 2)).min(ROWS_MAX as u32),
                carried: i32::from_ne_bytes(word(menu + 3)),
                a_fill: fill(menu + 4),
                b_fill: fill(menu + 5),
                icons: std::array::from_fn(|i| icon(menu + 6 + i)),
                off: u32::from_ne_bytes(word(menu + 6 + ROWS_MAX)),
                console: u32::from_ne_bytes(word(menu + 7 + ROWS_MAX)),
                pressed: std::array::from_fn(|i| {
                    let at = menu + 8 + ROWS_MAX + 2 * i;
                    u64::from(u32::from_ne_bytes(word(at)))
                        | u64::from(u32::from_ne_bytes(word(at + 1))) << 32
                }),
            }),
            saying: match u32::from_ne_bytes(word(menu + MENU_WORDS)) {
                1 => Saying::Saving,
                _ => Saying::Nothing,
            },
            rebind: (player > 0).then(|| Rebinding {
                player,
                console: u32::from_ne_bytes(word(rebind + 1)),
                control: i32::from_ne_bytes(word(rebind + 2)),
                index: i32::from_ne_bytes(word(rebind + 3)),
                total: i32::from_ne_bytes(word(rebind + 4)),
                finish: f32::from_ne_bytes(word(rebind + 5)),
                ended: u32::from_ne_bytes(word(rebind + 6)),
                pressed: u64::from(u32::from_ne_bytes(word(rebind + 7)))
                    | u64::from(u32::from_ne_bytes(word(rebind + 8))) << 32,
                // A stick off its travel is at its edge, and not a number
                // is the middle: this came through a pipe.
                sticks: std::array::from_fn(|i| {
                    let value = f32::from_ne_bytes(word(rebind + 9 + i));
                    if value.is_finite() {
                        value.clamp(-1.0, 1.0)
                    } else {
                        0.0
                    }
                }),
            }),
            nobody: u32::from_ne_bytes(word(5)) != 0,
            position: f32::from_ne_bytes(word(1)),
            exit_progress: f32::from_ne_bytes(word(2)),
            hold_fraction: std::array::from_fn(|i| f32::from_ne_bytes(word(holds + i))),
            hold_player: std::array::from_fn(|i| i32::from_ne_bytes(word(holds + HOLDS_MAX + i))),
            hold_icon: std::array::from_fn(|i| icon(holds + 2 * HOLDS_MAX + i)),
            hold_count,
            joined: std::array::from_fn(|i| i32::from_ne_bytes(word(joined + i))),
            joined_icon: std::array::from_fn(|i| icon(joined + JOINED_MAX + i)),
            joined_count,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn hold(fraction: f64, player: i32, icon: u8) -> Hold {
        Hold {
            fraction,
            player,
            icon,
            ..Hold::default()
        }
    }

    #[test]
    fn a_frame_carries_what_the_bar_draws() {
        let frame = Frame::pack(0.75, 0.0, &[hold(0.25, 2, 5), hold(0.5, 3, 9)], &[(1, 4)]);
        let back = Frame::decode(&frame.encode());
        assert_eq!(
            back.as_ref(),
            Some(&frame),
            "the painter reads back what was packed"
        );
        assert_eq!(frame.hold_player(), [2, 3], "holds in order");
        assert_eq!(frame.hold_fraction(), [0.25, 0.5]);
        assert_eq!(frame.hold_icon(), [5, 9], "each with its pad's drawing");
        assert_eq!((frame.joined(), frame.joined_icon()), (&[1][..], &[4][..]));
    }

    #[test]
    fn a_rebind_crosses_the_pipe_and_its_absence_does_too() {
        let rebinding = Rebinding {
            player: 2,
            console: 5,
            control: 3,
            index: 3,
            total: 14,
            finish: 0.25,
            ended: 0,
            pressed: 1 << 40 | 0b101,
            sticks: [-1.0, 0.5, 0.0, 0.25],
        };
        let frame = Frame {
            rebind: Some(rebinding),
            ..Frame::pack(1.0, 0.0, &[], &[])
        };
        assert_eq!(
            Frame::decode(&frame.encode()).and_then(|f| f.rebind),
            Some(rebinding)
        );
        let none = Frame::pack(1.0, 0.0, &[], &[]);
        assert_eq!(Frame::decode(&none.encode()).map(|f| f.rebind), Some(None));
        assert_eq!(Frame::decode(&none.encode()).map(|f| f.nobody), Some(false));
        let menu = MenuFrame {
            owner: 2,
            rows: 4,
            icons: [
                3, 5, EMPTY_SEAT, EMPTY_SEAT, EMPTY_SEAT, EMPTY_SEAT, EMPTY_SEAT, EMPTY_SEAT,
            ],
            focus: 4,
            carried: 0,
            a_fill: 0.5,
            b_fill: 0.0,
            off: 0b10,
            console: 3,
            pressed: [0b101, 1 << 40, 0, 0, 0, 0, 0, 0],
        };
        let with_menu = none.with_menu(Some(menu)).with_saying(Saying::Saving);
        let back = Frame::decode(&with_menu.encode()).expect("a frame");
        assert_eq!((back.menu, back.saying), (Some(menu), Saying::Saving));
        assert_eq!(Frame::decode(&none.encode()).map(|f| f.menu), Some(None));
        let nobody = none.with_nobody(true);
        assert_eq!(Frame::decode(&nobody.encode()).map(|f| f.nobody), Some(true));
        let wild = Frame {
            rebind: Some(Rebinding {
                sticks: [f32::NAN, 7.0, f32::NEG_INFINITY, -0.5],
                ..rebinding
            }),
            ..none
        };
        assert_eq!(
            Frame::decode(&wild.encode())
                .and_then(|f| f.rebind)
                .map(|r| r.sticks),
            Some([0.0, 1.0, 0.0, -0.5]),
            "a stick off its travel is drawn at its edge or in the middle"
        );
    }

    #[test]
    fn a_frame_fits_a_pipe_write_whole() {
        // Written in one go and read back whole only below PIPE_BUF, which
        // POSIX guarantees is at least 512.
        const { assert!(SIZE <= 512) };
    }

    #[test]
    fn a_frame_that_is_not_one_is_refused() {
        let mut bytes = Frame::pack(1.0, 0.0, &[], &[]).encode();
        bytes[0] ^= 0xff;
        assert_eq!(Frame::decode(&bytes), None, "the wrong magic");
        let mut bytes = Frame::pack(1.0, 0.0, &[], &[]).encode();
        bytes[12..16].copy_from_slice(&(HOLDS_MAX as u32 + 1).to_ne_bytes());
        assert_eq!(Frame::decode(&bytes), None, "a count past the arrays");
    }

    #[test]
    fn packing_more_than_fits_is_cut() {
        let holds = vec![hold(0.1, 1, 0); HOLDS_MAX + 3];
        let frame = Frame::pack(1.0, 0.0, &holds, &[(1, 0); JOINED_MAX + 2]);
        assert_eq!(
            (frame.hold_fraction().len(), frame.joined().len()),
            (HOLDS_MAX, JOINED_MAX)
        );
        assert!(Frame::decode(&frame.encode()).is_some());
    }

    #[test]
    fn decode_accepts_exactly_the_maximum_counts() {
        let holds = vec![hold(0.5, 1, 2); HOLDS_MAX];
        let frame = Frame::pack(1.0, 0.0, &holds, &[(1, 2); JOINED_MAX]);
        assert_eq!(
            Frame::decode(&frame.encode()),
            Some(frame),
            "the maximum itself is not refused"
        );
    }
}
