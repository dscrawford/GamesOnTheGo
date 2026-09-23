//! What the kill switch tells its painter to draw: one frame of the bar,
//! small enough to cross a pipe in a single atomic write.
//!
//! The drawing is a separate process. The kill switch's loop is the one thing
//! that must keep running while a game is hung -- it is what stops the game --
//! and drawing means talking to a compositor: a round trip, a present that
//! waits for the panel, a connect. Any of those can stall, and a stall in the
//! same loop was a kill chord that did nothing. So the kill switch keeps the
//! chord, padmap's socket and every decision, and hands the painter only this.
//! A painter that hangs misses frames; the kill switch does not notice.
//!
//! Encoded field by field in native byte order: both ends are this binary.

use crate::pairing::{HOLDS_MAX, Hold, JOINED_MAX};

/// "GOSV", so a torn or foreign read is refused.
pub const MAGIC: u32 = 0x5653_4f47;

/// Bytes on the pipe: six words, then the three arrays.
pub const SIZE: usize = 4 * (6 + HOLDS_MAX * 2 + JOINED_MAX);

/// One frame, in fixed arrays as it crosses the pipe: nothing to allocate
/// per frame at either end, and no way to hold more than a frame can say.
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct Frame {
    /// The bar: 0 out of sight, 1 all the way down.
    pub position: f32,
    /// 0..1 through the exit hold.
    pub exit_progress: f32,
    /// Seconds, for the spinner.
    pub clock: f32,
    hold_fraction: [f32; HOLDS_MAX],
    hold_player: [i32; HOLDS_MAX],
    hold_count: usize,
    joined: [i32; JOINED_MAX],
    joined_count: usize,
}

impl Frame {
    /// Filled from what the kill switch knows, cut to what a frame holds.
    pub fn pack(position: f64, exit_progress: f64, clock: f64, holds: &[Hold], joined: &[i32]) -> Self {
        let mut frame = Self {
            position: position as f32,
            exit_progress: exit_progress as f32,
            clock: clock as f32,
            hold_count: holds.len().min(HOLDS_MAX),
            joined_count: joined.len().min(JOINED_MAX),
            ..Self::default()
        };
        for (i, hold) in holds.iter().take(HOLDS_MAX).enumerate() {
            frame.hold_fraction[i] = hold.fraction as f32;
            frame.hold_player[i] = hold.player;
        }
        frame.joined[..frame.joined_count].copy_from_slice(&joined[..frame.joined_count]);
        frame
    }

    /// How far each joining pad is, oldest press first.
    pub fn hold_fraction(&self) -> &[f32] {
        &self.hold_fraction[..self.hold_count]
    }

    /// The seat each joining pad is filling towards.
    pub fn hold_player(&self) -> &[i32] {
        &self.hold_player[..self.hold_count]
    }

    /// Seats just taken, oldest first.
    pub fn joined(&self) -> &[i32] {
        &self.joined[..self.joined_count]
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
        put(self.clock.to_ne_bytes());
        put((self.hold_count as u32).to_ne_bytes());
        put((self.joined_count as u32).to_ne_bytes());
        self.hold_fraction
            .iter()
            .for_each(|value| put(value.to_ne_bytes()));
        self.hold_player.iter().for_each(|value| put(value.to_ne_bytes()));
        self.joined.iter().for_each(|value| put(value.to_ne_bytes()));
        out
    }

    /// The frame in these bytes, if they are one this painter can draw: the
    /// right magic, and counts no larger than the arrays, whatever arrived.
    pub fn decode(bytes: &[u8; SIZE]) -> Option<Self> {
        let word =
            |i: usize| -> [u8; 4] { [bytes[i * 4], bytes[i * 4 + 1], bytes[i * 4 + 2], bytes[i * 4 + 3]] };
        let count = |i: usize| u32::from_ne_bytes(word(i)) as usize;
        let (hold_count, joined_count) = (count(4), count(5));
        if u32::from_ne_bytes(word(0)) != MAGIC || hold_count > HOLDS_MAX || joined_count > JOINED_MAX {
            return None;
        }
        Some(Self {
            position: f32::from_ne_bytes(word(1)),
            exit_progress: f32::from_ne_bytes(word(2)),
            clock: f32::from_ne_bytes(word(3)),
            hold_fraction: std::array::from_fn(|i| f32::from_ne_bytes(word(6 + i))),
            hold_player: std::array::from_fn(|i| i32::from_ne_bytes(word(6 + HOLDS_MAX + i))),
            hold_count,
            joined: std::array::from_fn(|i| i32::from_ne_bytes(word(6 + 2 * HOLDS_MAX + i))),
            joined_count,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn hold(fraction: f64, player: i32) -> Hold {
        Hold {
            fraction,
            player,
            ..Hold::default()
        }
    }

    #[test]
    fn a_frame_carries_what_the_bar_draws() {
        let frame = Frame::pack(0.75, 0.0, 12.5, &[hold(0.25, 2), hold(0.5, 3)], &[1]);
        let back = Frame::decode(&frame.encode());
        assert_eq!(
            back.as_ref(),
            Some(&frame),
            "the painter reads back what was packed"
        );
        assert_eq!(frame.hold_player(), [2, 3], "holds in order");
        assert_eq!(frame.hold_fraction(), [0.25, 0.5]);
        assert_eq!(frame.joined(), [1]);
    }

    #[test]
    fn a_frame_fits_a_pipe_write_whole() {
        // Written in one go and read back whole only below PIPE_BUF, which
        // POSIX guarantees is at least 512.
        const { assert!(SIZE <= 512) };
    }

    #[test]
    fn a_frame_that_is_not_one_is_refused() {
        let mut bytes = Frame::pack(1.0, 0.0, 0.0, &[], &[]).encode();
        bytes[0] ^= 0xff;
        assert_eq!(Frame::decode(&bytes), None, "the wrong magic");
        let mut bytes = Frame::pack(1.0, 0.0, 0.0, &[], &[]).encode();
        bytes[16..20].copy_from_slice(&(HOLDS_MAX as u32 + 1).to_ne_bytes());
        assert_eq!(Frame::decode(&bytes), None, "a count past the arrays");
    }

    #[test]
    fn packing_more_than_fits_is_cut() {
        let holds = vec![hold(0.1, 1); HOLDS_MAX + 3];
        let frame = Frame::pack(1.0, 0.0, 0.0, &holds, &[1; JOINED_MAX + 2]);
        assert_eq!(
            (frame.hold_fraction().len(), frame.joined().len()),
            (HOLDS_MAX, JOINED_MAX)
        );
        assert!(Frame::decode(&frame.encode()).is_some());
    }

    #[test]
    fn decode_accepts_exactly_the_maximum_counts() {
        let holds = vec![hold(0.5, 1); HOLDS_MAX];
        let frame = Frame::pack(1.0, 0.0, 0.0, &holds, &[1; JOINED_MAX]);
        assert_eq!(
            Frame::decode(&frame.encode()),
            Some(frame),
            "the maximum itself is not refused"
        );
    }
}
