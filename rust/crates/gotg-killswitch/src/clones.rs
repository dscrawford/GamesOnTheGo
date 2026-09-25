//! Which seat a pad is, from what SDL says about it.
//!
//! The rebind chord has to reach padmap as "player N", and all the kill switch
//! has is SDL's view of the pad the chord came from. padmap's clones are named
//! `padmap Player N`, but SDL renames a clone that mirrors a pad it knows
//! (`Xbox 360 Controller`), so the name cannot say. The GUID can: bytes 2-3
//! carry a CRC-16 of the name the device was made with, taken before any
//! renaming -- the same thing the picker's clones.py reads.

/// Seats looked for. padmap allows sixteen, RetroArch's limit.
const SEATS: i32 = 16;

/// SDL's CRC-16 (reflected 0x8005, from zero), as it hashes a device's name
/// into the GUID.
fn crc16(bytes: &[u8]) -> u16 {
    let mut crc: u16 = 0;
    for &byte in bytes {
        crc ^= u16::from(byte);
        for _ in 0..8 {
            crc = if crc & 1 == 1 {
                (crc >> 1) ^ 0xA001
            } else {
                crc >> 1
            };
        }
    }
    crc
}

/// The seat a pad is padmap's clone for, or None for any other pad -- a raw
/// controller, which padmap has not published and cannot be rebound here.
pub fn player_of_guid(guid: &[u8; 16]) -> Option<i32> {
    let crc = u16::from_le_bytes([guid[2], guid[3]]);
    if crc == 0 {
        return None;
    }
    (1..=SEATS).find(|n| crc16(format!("padmap Player {n}").as_bytes()) == crc)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn guid(hex: &str) -> [u8; 16] {
        let mut out = [0u8; 16];
        for (i, byte) in out.iter_mut().enumerate() {
            *byte = u8::from_str_radix(&hex[i * 2..i * 2 + 2], 16).expect("hex");
        }
        out
    }

    #[test]
    fn a_clone_is_known_by_the_name_it_was_made_with() {
        // The Deck's env.sh, 2026-09-24: player one's clone of a Steam
        // Controller Puck, which SDL shows under another name.
        assert_eq!(player_of_guid(&guid("0300c9a7de2800000413000001000000")), Some(1));
        let mut two = guid("0300c9a7de2800000413000001000000");
        [two[2], two[3]] = crc16(b"padmap Player 2").to_le_bytes();
        assert_eq!(player_of_guid(&two), Some(2));
    }

    #[test]
    fn a_raw_pad_or_a_blank_crc_is_nobody() {
        // An Xbox pad over Bluetooth, and a GUID with no name hashed into it.
        assert_eq!(player_of_guid(&guid("050040a45e040000130b000017050000")), None);
        assert_eq!(player_of_guid(&guid("030000005e0400008e02000014010000")), None);
    }
}
