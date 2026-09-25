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

/// Bytes on the pipe: five words, the five arrays a word per entry, then
/// the rebind's seven words.
pub const SIZE: usize = 4 * (5 + HOLDS_MAX * 3 + JOINED_MAX * 2 + REBIND_WORDS);

const REBIND_WORDS: usize = 7;

/// A rebind as the bar draws it: which seat, on which console's drawing,
/// which control (an index into that console's, -1 before the first step),
/// how far through, the early-finish hold, and how it ended.
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
        });
        put(rebind.player.to_ne_bytes());
        put(rebind.console.to_ne_bytes());
        put(rebind.control.to_ne_bytes());
        put(rebind.index.to_ne_bytes());
        put(rebind.total.to_ne_bytes());
        put(rebind.finish.to_ne_bytes());
        put(rebind.ended.to_ne_bytes());
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
        let holds = 5;
        let joined = holds + 3 * HOLDS_MAX;
        let rebind = joined + 2 * JOINED_MAX;
        let player = i32::from_ne_bytes(word(rebind));
        Some(Self {
            rebind: (player > 0).then(|| Rebinding {
                player,
                console: u32::from_ne_bytes(word(rebind + 1)),
                control: i32::from_ne_bytes(word(rebind + 2)),
                index: i32::from_ne_bytes(word(rebind + 3)),
                total: i32::from_ne_bytes(word(rebind + 4)),
                finish: f32::from_ne_bytes(word(rebind + 5)),
                ended: u32::from_ne_bytes(word(rebind + 6)),
            }),
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
