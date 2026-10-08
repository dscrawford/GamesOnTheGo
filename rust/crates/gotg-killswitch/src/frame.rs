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

use crate::menu::{Browse, Listed};
use crate::pairing::{HOLDS_MAX, Hold, JOINED_MAX, Shown};

/// "GOSV", so a torn or foreign read is refused.
pub const MAGIC: u32 = 0x5653_4f47;

/// Bytes on the pipe: seven words, the five arrays a word per entry, the
/// rebind's thirteen words, the menu's, and one for what the bar is saying.
pub const SIZE: usize = 4 * (7 + HOLDS_MAX * 3 + JOINED_MAX * 2 + REBIND_WORDS + MENU_WORDS + 1);

/// Owner, rows, focus, carried, two fills, the icons, the off mask, the
/// console, each seat's presses in two words and sticks in one, testing, and
/// the saves list.
const MENU_WORDS: usize = 9 + ROWS_MAX + 2 * ROWS_MAX + ROWS_MAX + SAVES_WORDS;

/// The saves row and list: whether there is a row, the list's state and the
/// prompt in one word, how many saves, and which is under the cursor. Their
/// words are not in the frame -- the painter reads them from the session the
/// client wrote them to (`gotg saves list --lines`), since a frame has room
/// for numbers and not for a dozen machine names and dates.
const SAVES_WORDS: usize = 3;

/// Seats a menu frame lists.
pub const ROWS_MAX: usize = crate::menu::SEATS_MAX;

/// A seat with nobody in it, where a drawing would be.
pub const EMPTY_SEAT: u8 = u8::MAX;

/// The menu as the bar draws it: whose it is, the seats (a drawing each, or
/// EMPTY_SEAT), where the cursor is (a seat; `rows` the game's controller;
/// then the saves row when there is one, then Exit), the
/// seat being carried (0 none), the two holds' fills (A's and B's), and what
/// the game's controller shows: each seat's presses and sticks, the owner's
/// trying, the saves list.
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
    /// Per seat, left stick x, y, right stick x, y: -1..1, y down.
    pub sticks: [[f32; 4]; ROWS_MAX],
    /// The owner is trying their buttons rather than driving the menu.
    pub testing: bool,
    pub saves: Saves,
}

/// The menu's saves: a row to open them, and the list while it is open.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct Saves {
    pub row: bool,
    pub browse: Option<Browse>,
}

impl Saves {
    fn put(self, out: &mut Writer) {
        self.words().into_iter().for_each(|word| out.put_u32(word));
    }

    fn get(from: &mut Reader) -> Self {
        Self::from_words([from.get_u32(), from.get_u32(), from.get_u32()])
    }

    fn words(self) -> [u32; SAVES_WORDS] {
        let (state, count, selected, confirming) = match self.browse {
            None => (0, 0, 0, false),
            Some(b) => match b.listed {
                Listed::Loading => (1, 0, 0, b.confirming),
                Listed::Failed => (2, 0, 0, b.confirming),
                Listed::Ready(n) => (3, n as u32, b.selected as u32, b.confirming),
            },
        };
        [
            u32::from(self.row) | u32::from(confirming) << 1 | state << 8,
            count,
            selected,
        ]
    }

    /// Whatever arrived, a list that can be drawn: the cursor on a save that
    /// is there, and no list at all for a state there is none of.
    fn from_words(words: [u32; SAVES_WORDS]) -> Self {
        let [flags, count, selected] = words;
        let listed = match (flags >> 8) & 0xff {
            1 => Some(Listed::Loading),
            2 => Some(Listed::Failed),
            3 => Some(Listed::Ready(count as usize)),
            _ => None,
        };
        Self {
            row: flags & 1 != 0,
            browse: listed.map(|listed| Browse {
                listed,
                selected: (selected as usize).min((count as usize).saturating_sub(1)),
                confirming: flags & 2 != 0,
            }),
        }
    }
}

/// Four stick axes in a word, a signed byte each: a dot on a ring a few
/// dozen pixels wide cannot show finer than that.
fn pack_sticks(sticks: [f32; 4]) -> u32 {
    sticks.iter().enumerate().fold(0, |word, (at, axis)| {
        let byte = (axis.clamp(-1.0, 1.0) * 127.0).round() as i8 as u8;
        word | u32::from(byte) << (8 * at)
    })
}

fn unpack_sticks(word: u32) -> [f32; 4] {
    std::array::from_fn(|at| f32::from((word >> (8 * at)) as u8 as i8) / 127.0)
}

/// Where the layout is written down once per direction: a cursor over the
/// frame's bytes that moves a word at a time, so a field named in `put` and
/// the same field named in `get` are in the same place by construction and
/// no offset is counted by hand. Every wire value is one 32-bit word (a
/// `u64` is two, low first; an icon is a `u8` widened).
struct Writer {
    bytes: [u8; SIZE],
    at: usize,
}

impl Writer {
    fn new() -> Self {
        Self {
            bytes: [0; SIZE],
            at: 0,
        }
    }

    fn put(&mut self, word: [u8; 4]) {
        if let Some(slot) = self.bytes.get_mut(self.at..self.at + 4) {
            slot.copy_from_slice(&word);
        }
        self.at += 4;
    }

    fn put_u32(&mut self, value: u32) {
        self.put(value.to_ne_bytes());
    }

    fn put_i32(&mut self, value: i32) {
        self.put(value.to_ne_bytes());
    }

    fn put_f32(&mut self, value: f32) {
        self.put(value.to_ne_bytes());
    }

    fn put_u8(&mut self, value: u8) {
        self.put_u32(u32::from(value));
    }

    fn put_u64(&mut self, value: u64) {
        self.put_u32(value as u32);
        self.put_u32((value >> 32) as u32);
    }

    /// The bytes, once every word the layout names has been written.
    fn finish(self) -> [u8; SIZE] {
        debug_assert_eq!(self.at, SIZE, "the layout writes exactly SIZE bytes");
        self.bytes
    }
}

struct Reader<'a> {
    bytes: &'a [u8; SIZE],
    at: usize,
}

impl<'a> Reader<'a> {
    fn new(bytes: &'a [u8; SIZE]) -> Self {
        Self { bytes, at: 0 }
    }

    fn get(&mut self) -> [u8; 4] {
        let word = self
            .bytes
            .get(self.at..self.at + 4)
            .and_then(|slot| <[u8; 4]>::try_from(slot).ok())
            .unwrap_or_default();
        self.at += 4;
        word
    }

    fn get_u32(&mut self) -> u32 {
        u32::from_ne_bytes(self.get())
    }

    fn get_i32(&mut self) -> i32 {
        i32::from_ne_bytes(self.get())
    }

    fn get_f32(&mut self) -> f32 {
        f32::from_ne_bytes(self.get())
    }

    /// A drawing past the table is drawn as the fallback, by `icons`.
    fn get_u8(&mut self) -> u8 {
        u8::try_from(self.get_u32()).unwrap_or(u8::MAX)
    }

    fn get_u64(&mut self) -> u64 {
        let low = u64::from(self.get_u32());
        low | u64::from(self.get_u32()) << 32
    }

    /// A number that came through a pipe: not a number is the middle, and
    /// one off its travel is at its edge.
    fn get_f32_within(&mut self, low: f32, high: f32) -> f32 {
        let value = self.get_f32();
        if value.is_finite() {
            value.clamp(low, high)
        } else {
            0.0_f32.clamp(low, high)
        }
    }

    fn finish(&self) {
        debug_assert_eq!(self.at, SIZE, "the layout reads exactly SIZE bytes");
    }
}

/// What the bar says in words when it says something on its own.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum Saying {
    #[default]
    Nothing,
    /// The game has been stopped from the menu and its saves are on their way.
    Saving,
    /// A save picked from the menu is being put back and the game started on it.
    Loading,
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

impl Rebinding {
    /// What the wire says when there is no rebind: player 0.
    const NONE: Self = Self {
        player: 0,
        console: 0,
        control: -1,
        index: 0,
        total: 0,
        finish: 0.0,
        ended: 0,
        pressed: 0,
        sticks: [0.0; 4],
    };

    fn put(&self, out: &mut Writer) {
        out.put_i32(self.player);
        out.put_u32(self.console);
        out.put_i32(self.control);
        out.put_i32(self.index);
        out.put_i32(self.total);
        out.put_f32(self.finish);
        out.put_u32(self.ended);
        out.put_u64(self.pressed);
        self.sticks.iter().for_each(|&axis| out.put_f32(axis));
    }

    fn get(from: &mut Reader) -> Self {
        Self {
            player: from.get_i32(),
            console: from.get_u32(),
            control: from.get_i32(),
            index: from.get_i32(),
            total: from.get_i32(),
            finish: from.get_f32(),
            ended: from.get_u32(),
            pressed: from.get_u64(),
            sticks: std::array::from_fn(|_| from.get_f32_within(-1.0, 1.0)),
        }
    }
}

impl MenuFrame {
    /// What the wire says when there is no menu: owner 0.
    const NONE: Self = Self {
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
        sticks: [[0.0; 4]; ROWS_MAX],
        testing: false,
        saves: Saves {
            row: false,
            browse: None,
        },
    };

    fn put(&self, out: &mut Writer) {
        out.put_i32(self.owner);
        out.put_u32(self.rows);
        out.put_u32(self.focus);
        out.put_i32(self.carried);
        out.put_f32(self.a_fill);
        out.put_f32(self.b_fill);
        self.icons.iter().for_each(|&icon| out.put_u8(icon));
        out.put_u32(self.off);
        out.put_u32(self.console);
        self.pressed.iter().for_each(|&down| out.put_u64(down));
        self.sticks
            .iter()
            .for_each(|&axes| out.put_u32(pack_sticks(axes)));
        out.put_u32(u32::from(self.testing));
        self.saves.put(out);
    }

    fn get(from: &mut Reader) -> Self {
        Self {
            owner: from.get_i32(),
            rows: from.get_u32().min(ROWS_MAX as u32),
            // A seat, the tester, the saves row, or Exit.
            focus: from.get_u32().min(ROWS_MAX as u32 + 2),
            carried: from.get_i32(),
            a_fill: from.get_f32_within(0.0, 1.0),
            b_fill: from.get_f32_within(0.0, 1.0),
            icons: std::array::from_fn(|_| from.get_u8()),
            off: from.get_u32(),
            console: from.get_u32(),
            pressed: std::array::from_fn(|_| from.get_u64()),
            sticks: std::array::from_fn(|_| unpack_sticks(from.get_u32())),
            testing: from.get_u32() != 0,
            saves: Saves::get(from),
        }
    }
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
    /// Which of `joined` were just taken (ticked), a bit per entry.
    joined_fresh: u32,
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
    /// `joined` is the seat line, as `Pairing::line` gives it.
    pub fn pack(position: f64, exit_progress: f64, holds: &[Hold], joined: &[Shown]) -> Self {
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
        for (i, shown) in joined.iter().take(JOINED_MAX).enumerate() {
            frame.joined[i] = shown.player;
            frame.joined_icon[i] = shown.icon;
            if shown.fresh {
                frame.joined_fresh |= 1 << i;
            }
        }
        frame
    }

    pub fn with_nobody(self, nobody: bool) -> Self {
        Self { nobody, ..self }
    }

    pub fn with_menu(self, menu: Option<MenuFrame>) -> Self {
        Self { menu, ..self }
    }

    pub fn with_saying(self, saying: Saying) -> Self {
        Self { saying, ..self }
    }

    pub fn with_rebind(self, rebind: Option<Rebinding>) -> Self {
        Self { rebind, ..self }
    }

    pub fn hold_fraction(&self) -> &[f32] {
        &self.hold_fraction[..self.hold_count]
    }

    pub fn hold_player(&self) -> &[i32] {
        &self.hold_player[..self.hold_count]
    }

    pub fn hold_icon(&self) -> &[u8] {
        &self.hold_icon[..self.hold_count]
    }

    pub fn joined(&self) -> &[i32] {
        &self.joined[..self.joined_count]
    }

    /// Which of them were just taken, a bit per entry of `joined`.
    pub fn joined_fresh(&self) -> u32 {
        self.joined_fresh
    }

    pub fn joined_icon(&self) -> &[u8] {
        &self.joined_icon[..self.joined_count]
    }

    pub fn encode(&self) -> [u8; SIZE] {
        let mut out = Writer::new();
        out.put_u32(MAGIC);
        out.put_f32(self.position);
        out.put_f32(self.exit_progress);
        out.put_u32(self.hold_count as u32);
        out.put_u32(self.joined_count as u32);
        out.put_u32(u32::from(self.nobody));
        out.put_u32(self.joined_fresh);
        self.hold_fraction.iter().for_each(|&value| out.put_f32(value));
        self.hold_player.iter().for_each(|&value| out.put_i32(value));
        self.hold_icon.iter().for_each(|&value| out.put_u8(value));
        self.joined.iter().for_each(|&value| out.put_i32(value));
        self.joined_icon.iter().for_each(|&value| out.put_u8(value));
        self.rebind.unwrap_or(Rebinding::NONE).put(&mut out);
        self.menu.unwrap_or(MenuFrame::NONE).put(&mut out);
        out.put_u32(match self.saying {
            Saying::Nothing => 0,
            Saying::Saving => 1,
            Saying::Loading => 2,
        });
        out.finish()
    }

    /// The frame in these bytes, if they are one this painter can draw: the
    /// right magic, and counts no larger than the arrays, whatever arrived.
    /// A drawing past the table is drawn as the fallback, by `icons`.
    pub fn decode(bytes: &[u8; SIZE]) -> Option<Self> {
        let mut from = Reader::new(bytes);
        let magic = from.get_u32();
        let position = from.get_f32();
        let exit_progress = from.get_f32();
        let (hold_count, joined_count) = (from.get_u32() as usize, from.get_u32() as usize);
        if magic != MAGIC || hold_count > HOLDS_MAX || joined_count > JOINED_MAX {
            return None;
        }
        let nobody = from.get_u32() != 0;
        let joined_fresh = from.get_u32() & ((1u32 << joined_count) - 1);
        let hold_fraction = std::array::from_fn(|_| from.get_f32());
        let hold_player = std::array::from_fn(|_| from.get_i32());
        let hold_icon = std::array::from_fn(|_| from.get_u8());
        let joined = std::array::from_fn(|_| from.get_i32());
        let joined_icon = std::array::from_fn(|_| from.get_u8());
        let rebind = Rebinding::get(&mut from);
        let menu = MenuFrame::get(&mut from);
        let saying = match from.get_u32() {
            1 => Saying::Saving,
            2 => Saying::Loading,
            _ => Saying::Nothing,
        };
        from.finish();
        Some(Self {
            position,
            exit_progress,
            hold_fraction,
            hold_player,
            hold_icon,
            hold_count,
            joined,
            joined_icon,
            joined_count,
            joined_fresh,
            rebind: (rebind.player > 0).then_some(rebind),
            menu: (menu.owner > 0).then_some(menu),
            saying,
            nobody,
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

    fn shown(player: i32, icon: u8, fresh: bool) -> Shown {
        Shown { player, icon, fresh }
    }

    /// Corner values through the pipe: every optional present and absent, the
    /// counts at their maxima, the fresh bits at their edges, a menu with
    /// every field at its limit. `decode(encode(f)) == f` for each.
    #[test]
    fn encode_and_decode_are_inverse_at_the_corners() {
        let holds = vec![hold(1.0, i32::MAX, u8::MAX); HOLDS_MAX];
        let joined: Vec<Shown> = (0..JOINED_MAX)
            .map(|i| shown(i as i32 + 1, u8::MAX, i == 0 || i == JOINED_MAX - 1))
            .collect();
        let limits = MenuFrame {
            owner: i32::MAX,
            rows: ROWS_MAX as u32,
            icons: [0; ROWS_MAX],
            // Decode clamps the cursor to the last row (Exit): the largest
            // value that survives is ROWS_MAX + 2.
            focus: ROWS_MAX as u32 + 2,
            carried: -1,
            a_fill: 1.0,
            b_fill: 1.0,
            off: u32::MAX,
            console: u32::MAX,
            pressed: [u64::MAX; ROWS_MAX],
            sticks: [[-1.0, 1.0, -1.0, 1.0]; ROWS_MAX],
            testing: true,
            saves: Saves {
                row: true,
                browse: Some(Browse {
                    listed: Listed::Ready(usize::from(u8::MAX)),
                    selected: usize::from(u8::MAX) - 1,
                    confirming: true,
                }),
            },
        };
        let empty_seats = MenuFrame {
            icons: [EMPTY_SEAT; ROWS_MAX],
            carried: 0,
            ..limits
        };
        let rebinding = Rebinding {
            player: i32::MAX,
            console: u32::MAX,
            control: -1,
            index: -1,
            total: i32::MAX,
            finish: 1.0,
            ended: 2,
            pressed: u64::MAX,
            sticks: [-1.0, 1.0, 0.0, -1.0],
        };
        let bare = Frame::pack(0.0, 0.0, &[], &[]);
        let full = Frame::pack(1.0, 1.0, &holds, &joined);
        for frame in [
            bare,
            full,
            full.with_menu(Some(limits)),
            full.with_menu(Some(empty_seats)),
            Frame {
                rebind: Some(rebinding),
                ..full
            },
            Frame {
                rebind: Some(rebinding),
                ..bare
            }
            .with_menu(Some(limits))
            .with_saying(Saying::Saving)
            .with_nobody(true),
            bare.with_saying(Saying::Loading),
        ] {
            assert_eq!(Frame::decode(&frame.encode()), Some(frame));
        }
        let past = full.with_menu(Some(MenuFrame {
            focus: u32::MAX,
            ..limits
        }));
        assert_eq!(
            Frame::decode(&past.encode())
                .and_then(|f| f.menu)
                .map(|m| m.focus),
            Some(ROWS_MAX as u32 + 2),
            "a cursor past Exit is drawn on Exit"
        );
    }

    /// A frame with something in every field, distinct values throughout.
    fn golden_frame() -> Frame {
        let holds = [hold(0.25, 2, 5), hold(0.5, 3, 9)];
        let joined = [shown(1, 4, false), shown(2, 6, true), shown(3, 7, true)];
        Frame {
            rebind: Some(Rebinding {
                player: 2,
                console: 5,
                control: 3,
                index: 4,
                total: 14,
                finish: 0.25,
                ended: 1,
                pressed: 1 << 40 | 0b101,
                sticks: [-1.0, 0.5, 0.0, 0.25],
            }),
            ..Frame::pack(0.75, 0.5, &holds, &joined)
        }
        .with_menu(Some(MenuFrame {
            owner: 2,
            rows: 3,
            icons: std::array::from_fn(|i| if i < 3 { i as u8 + 10 } else { EMPTY_SEAT }),
            focus: 4,
            carried: -1,
            a_fill: 0.5,
            b_fill: 0.25,
            off: 0b10,
            console: 6,
            pressed: std::array::from_fn(|i| (1 << (33 + i)) | i as u64),
            sticks: std::array::from_fn(|i| [i as f32 / 8.0, -1.0, 0.5, 1.0]),
            testing: true,
            saves: Saves {
                row: true,
                browse: Some(Browse {
                    listed: Listed::Ready(7),
                    selected: 3,
                    confirming: true,
                }),
            },
        }))
        .with_saying(Saying::Saving)
        .with_nobody(true)
    }

    /// The wire format, word by word, as it was before encode and decode
    /// shared one cursor. The painter is a separate process and a frame from
    /// an older killswitch must still read: if this fails, the layout moved.
    const GOLDEN: [u32; SIZE / 4] = [
        0x56534f47, 0x3f400000, 0x3f000000, 0x00000002, 0x00000003, 0x00000001, 0x00000006, 0x3e800000,
        0x3f000000, 0x00000000, 0x00000000, 0x00000000, 0x00000000, 0x00000000, 0x00000000, 0x00000002,
        0x00000003, 0x00000000, 0x00000000, 0x00000000, 0x00000000, 0x00000000, 0x00000000, 0x00000005,
        0x00000009, 0x00000000, 0x00000000, 0x00000000, 0x00000000, 0x00000000, 0x00000000, 0x00000001,
        0x00000002, 0x00000003, 0x00000000, 0x00000000, 0x00000000, 0x00000000, 0x00000000, 0x00000004,
        0x00000006, 0x00000007, 0x00000000, 0x00000000, 0x00000000, 0x00000000, 0x00000000, 0x00000002,
        0x00000005, 0x00000003, 0x00000004, 0x0000000e, 0x3e800000, 0x00000001, 0x00000005, 0x00000100,
        0xbf800000, 0x3f000000, 0x00000000, 0x3e800000, 0x00000002, 0x00000003, 0x00000004, 0xffffffff,
        0x3f000000, 0x3e800000, 0x0000000a, 0x0000000b, 0x0000000c, 0x000000ff, 0x000000ff, 0x000000ff,
        0x000000ff, 0x000000ff, 0x00000002, 0x00000006, 0x00000000, 0x00000002, 0x00000001, 0x00000004,
        0x00000002, 0x00000008, 0x00000003, 0x00000010, 0x00000004, 0x00000020, 0x00000005, 0x00000040,
        0x00000006, 0x00000080, 0x00000007, 0x00000100, 0x7f408100, 0x7f408110, 0x7f408120, 0x7f408130,
        0x7f408140, 0x7f40814f, 0x7f40815f, 0x7f40816f, 0x00000001, 0x00000303, 0x00000007, 0x00000003,
        0x00000001,
    ];

    #[test]
    fn the_wire_format_is_the_one_the_painter_reads() {
        let bytes = golden_frame().encode();
        let words: Vec<u32> = bytes
            .as_chunks::<4>()
            .0
            .iter()
            .map(|c| u32::from_ne_bytes(*c))
            .collect();
        assert_eq!(words, GOLDEN, "encoded bytes");
        let mut golden = [0u8; SIZE];
        for (chunk, word) in golden.as_chunks_mut::<4>().0.iter_mut().zip(GOLDEN) {
            *chunk = word.to_ne_bytes();
        }
        // Sticks are quantised to a byte an axis, so compare the bytes again.
        let decoded = Frame::decode(&golden).expect("the old frame still reads");
        assert_eq!(decoded.encode(), golden, "decoded and encoded again");
        assert_eq!(decoded.menu.map(|m| (m.owner, m.focus)), Some((2, 4)));
        assert_eq!(decoded.rebind.map(|r| r.pressed), Some(1 << 40 | 0b101));
    }

    #[test]
    fn a_frame_carries_what_the_bar_draws() {
        let frame = Frame::pack(
            0.75,
            0.0,
            &[hold(0.25, 2, 5), hold(0.5, 3, 9)],
            &[shown(1, 4, false), shown(2, 6, true)],
        );
        let back = Frame::decode(&frame.encode());
        assert_eq!(
            back.as_ref(),
            Some(&frame),
            "the painter reads back what was packed"
        );
        assert_eq!(frame.hold_player(), [2, 3], "holds in order");
        assert_eq!(frame.hold_fraction(), [0.25, 0.5]);
        assert_eq!(frame.hold_icon(), [5, 9], "each with its pad's drawing");
        assert_eq!((frame.joined(), frame.joined_icon()), (&[1, 2][..], &[4, 6][..]));
        assert_eq!(frame.joined_fresh(), 0b10, "only the second was just taken");
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
            sticks: [
                [0.0; 4],
                [-1.0, 1.0, 0.0, 0.0],
                [0.0; 4],
                [0.0; 4],
                [0.0; 4],
                [0.0; 4],
                [0.0; 4],
                [0.0; 4],
            ],
            testing: true,
            saves: Saves {
                row: true,
                browse: Some(Browse {
                    listed: Listed::Ready(7),
                    selected: 3,
                    confirming: true,
                }),
            },
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
    fn a_saves_list_crosses_the_pipe_in_each_of_its_states() {
        let base = Frame::pack(1.0, 0.0, &[], &[]);
        for browse in [
            None,
            Some(Browse {
                listed: Listed::Loading,
                selected: 0,
                confirming: false,
            }),
            Some(Browse {
                listed: Listed::Failed,
                selected: 0,
                confirming: false,
            }),
            Some(Browse {
                listed: Listed::Ready(0),
                selected: 0,
                confirming: false,
            }),
            Some(Browse {
                listed: Listed::Ready(12),
                selected: 11,
                confirming: false,
            }),
        ] {
            let saves = Saves { row: true, browse };
            let frame = base.with_menu(Some(MenuFrame {
                saves,
                ..menu_frame()
            }));
            let back = Frame::decode(&frame.encode())
                .and_then(|f| f.menu)
                .map(|m| m.saves);
            assert_eq!(back, Some(saves));
        }
        let loading = base.with_saying(Saying::Loading);
        assert_eq!(
            Frame::decode(&loading.encode()).map(|f| f.saying),
            Some(Saying::Loading)
        );
    }

    #[test]
    fn a_saves_list_past_its_end_or_in_no_state_is_drawn_safely() {
        let frame = Frame::pack(1.0, 0.0, &[], &[]).with_menu(Some(menu_frame()));
        let mut bytes = frame.encode();
        let at = 4 * (SIZE / 4 - 1 - SAVES_WORDS);
        // Ready, five saves, the cursor on the ninth.
        bytes[at..at + 4].copy_from_slice(&(1u32 | 3 << 8).to_ne_bytes());
        bytes[at + 4..at + 8].copy_from_slice(&5u32.to_ne_bytes());
        bytes[at + 8..at + 12].copy_from_slice(&9u32.to_ne_bytes());
        let browse = Frame::decode(&bytes)
            .and_then(|f| f.menu)
            .and_then(|m| m.saves.browse);
        assert_eq!(browse.map(|b| b.selected), Some(4), "on the last of them");
        bytes[at..at + 4].copy_from_slice(&(1u32 | 9 << 8).to_ne_bytes());
        let browse = Frame::decode(&bytes)
            .and_then(|f| f.menu)
            .and_then(|m| m.saves.browse);
        assert_eq!(browse, None, "a state there is none of is no list");
    }

    fn menu_frame() -> MenuFrame {
        MenuFrame {
            owner: 1,
            rows: 1,
            icons: [EMPTY_SEAT; ROWS_MAX],
            focus: 0,
            carried: 0,
            a_fill: 0.0,
            b_fill: 0.0,
            off: 0,
            console: 0,
            pressed: [0; ROWS_MAX],
            sticks: [[0.0; 4]; ROWS_MAX],
            testing: false,
            saves: Saves::default(),
        }
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
        let frame = Frame::pack(1.0, 0.0, &holds, &[shown(1, 0, true); JOINED_MAX + 2]);
        assert_eq!(
            (frame.hold_fraction().len(), frame.joined().len()),
            (HOLDS_MAX, JOINED_MAX)
        );
        assert!(Frame::decode(&frame.encode()).is_some());
    }

    #[test]
    fn decode_accepts_exactly_the_maximum_counts() {
        let holds = vec![hold(0.5, 1, 2); HOLDS_MAX];
        let frame = Frame::pack(1.0, 0.0, &holds, &[shown(1, 2, false); JOINED_MAX]);
        assert_eq!(
            Frame::decode(&frame.encode()),
            Some(frame),
            "the maximum itself is not refused"
        );
    }
}
