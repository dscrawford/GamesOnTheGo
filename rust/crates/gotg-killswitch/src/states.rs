//! Save states: the emulator's own slots, filled and loaded by pressing its
//! hotkeys on `keyboard.rs`. The spec: `slots=8;save=LEFTSHIFT+F{slot};load=F{slot}`.

use std::path::Path;

/// Slots offered at most: the spec's digit.
pub const SLOTS_MAX: u32 = 9;

/// A key of a chord: one of the table, or the F-key of the slot's number.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Key {
    Code(u16),
    SlotF,
}

/// Linux input codes for the keys an emulator's hotkey may be.
const KEYS: &[(&str, u16)] = &[
    ("ESC", 1),
    ("1", 2),
    ("2", 3),
    ("3", 4),
    ("4", 5),
    ("5", 6),
    ("6", 7),
    ("7", 8),
    ("8", 9),
    ("9", 10),
    ("0", 11),
    ("TAB", 15),
    ("Q", 16),
    ("W", 17),
    ("E", 18),
    ("R", 19),
    ("T", 20),
    ("Y", 21),
    ("U", 22),
    ("I", 23),
    ("O", 24),
    ("P", 25),
    ("ENTER", 28),
    ("LEFTCTRL", 29),
    ("A", 30),
    ("S", 31),
    ("D", 32),
    ("F", 33),
    ("G", 34),
    ("H", 35),
    ("J", 36),
    ("K", 37),
    ("L", 38),
    ("LEFTSHIFT", 42),
    ("Z", 44),
    ("X", 45),
    ("C", 46),
    ("V", 47),
    ("B", 48),
    ("N", 49),
    ("M", 50),
    ("RIGHTSHIFT", 54),
    ("LEFTALT", 56),
    ("SPACE", 57),
    ("F1", 59),
    ("F2", 60),
    ("F3", 61),
    ("F4", 62),
    ("F5", 63),
    ("F6", 64),
    ("F7", 65),
    ("F8", 66),
    ("F9", 67),
    ("F10", 68),
    ("F11", 87),
    ("F12", 88),
    ("RIGHTCTRL", 97),
    ("RIGHTALT", 100),
];

fn f_key(n: u32) -> Option<u16> {
    match n {
        1..=10 => Some(58 + n as u16),
        11 => Some(87),
        12 => Some(88),
        _ => None,
    }
}

fn key(name: &str) -> Option<Key> {
    if name == "F{slot}" {
        return Some(Key::SlotF);
    }
    KEYS.iter()
        .find(|(known, _)| *known == name)
        .map(|&(_, code)| Key::Code(code))
}

/// What the emulator's save states are driven with.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Spec {
    pub slots: u32,
    save: Vec<Key>,
    load: Vec<Key>,
}

impl Spec {
    /// None for anything the overlay cannot press, rather than a row that
    /// does nothing.
    pub fn parse(text: &str) -> Option<Self> {
        let (mut slots, mut save, mut load) = (None, None, None);
        for field in text.split(';') {
            let (name, value) = field.split_once('=')?;
            match name {
                "slots" => slots = value.parse().ok().filter(|n| (1..=SLOTS_MAX).contains(n)),
                "save" => save = chord(value),
                "load" => load = chord(value),
                _ => return None,
            }
        }
        Some(Self {
            slots: slots?,
            save: save?,
            load: load?,
        })
    }

    /// The codes to press, in order, to save into or load `slot` (1-based).
    pub fn chord(&self, save: bool, slot: u32) -> Vec<u16> {
        let keys = if save { &self.save } else { &self.load };
        keys.iter()
            .filter_map(|key| match key {
                Key::Code(code) => Some(*code),
                Key::SlotF => f_key(slot),
            })
            .collect()
    }

    /// Every code any chord of any slot presses: what the keyboard is made with.
    pub fn codes(&self) -> Vec<u16> {
        let mut codes: Vec<u16> = (1..=self.slots)
            .flat_map(|slot| [self.chord(true, slot), self.chord(false, slot)])
            .flatten()
            .collect();
        codes.sort_unstable();
        codes.dedup();
        codes
    }
}

fn chord(text: &str) -> Option<Vec<Key>> {
    let keys: Option<Vec<Key>> = text.split('+').map(key).collect();
    keys.filter(|keys| !keys.is_empty() && keys.len() <= 4)
}

/// One slot, as the client said it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StateLine {
    pub slot: u32,
    /// When it was written, in this machine's words; empty for an empty slot.
    pub when: String,
    /// The game it holds, for an environment several share.
    pub detail: String,
}

impl StateLine {
    pub fn present(&self) -> bool {
        !self.when.is_empty()
    }
}

/// `gotg saves states --lines`: `slot:N`, when it was written in this
/// machine's words (nothing for an empty slot), and the game it holds.
pub fn parse_lines(text: &str) -> Vec<StateLine> {
    text.lines()
        .filter_map(|line| {
            let mut fields = line.split('\t');
            let id = fields.next()?;
            let slot: u32 = id.strip_prefix("slot:")?.parse().ok()?;
            (1..=SLOTS_MAX).contains(&slot).then(|| StateLine {
                slot,
                when: printable(fields.next().unwrap_or("")),
                detail: printable(fields.next().unwrap_or("")),
            })
        })
        .take(SLOTS_MAX as usize)
        .collect()
}

fn printable(text: &str) -> String {
    text.chars().filter(|c| !c.is_control()).take(80).collect()
}

/// A bit per listed line that holds a state: what Y can load.
pub fn present_bits(lines: &[StateLine]) -> u64 {
    lines
        .iter()
        .enumerate()
        .filter(|(_, line)| line.present())
        .fold(0, |bits, (at, _)| bits | 1 << at.min(63))
}

pub fn lines_path(session: &Path) -> std::path::PathBuf {
    session.join("states.tsv")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_spec_names_the_slots_and_the_chords_for_each() {
        let spec = Spec::parse("slots=8;save=LEFTSHIFT+F{slot};load=F{slot}").expect("a spec");
        assert_eq!(spec.slots, 8);
        assert_eq!(spec.chord(true, 3), [42, 61]);
        assert_eq!(spec.chord(false, 8), [66]);
        let ares = Spec::parse("slots=1;save=F5;load=F7").expect("a spec");
        assert_eq!(ares.chord(true, 1), [63]);
        assert_eq!(ares.chord(false, 1), [65]);
        assert_eq!(ares.codes(), [63, 65]);
    }

    #[test]
    fn a_spec_the_overlay_cannot_press_is_no_spec() {
        for text in [
            "",
            ";",
            "slots=8",
            "save=F5;load=F7",
            "slots=0;save=F5;load=F7",
            "slots=10;save=F5;load=F7",
            "slots=-1;save=F5;load=F7",
            "slots=1;save=F5;load=F99",
            "slots=1;save=F13;load=F7",
            "slots=1;save=PAGEUP;load=F7",
            "slots=1;save=f5;load=F7",
            "slots=1;save=;load=F7",
            "slots=1;save=F5+;load=F7",
            "slots=1;save=F{SLOT};load=F7",
            "slots=1;save=F5;load=F7;",
            "slots=1;save=F5;load=F7;x=1",
            "slots=1;save=A+B+C+D+E;load=F7",
        ] {
            assert_eq!(Spec::parse(text), None, "{text}");
        }
    }

    #[test]
    fn codes_are_every_key_of_every_slot_once_and_sorted() {
        let dolphin = Spec::parse("slots=8;save=LEFTSHIFT+F{slot};load=F{slot}").expect("a spec");
        assert_eq!(dolphin.codes(), [42, 59, 60, 61, 62, 63, 64, 65, 66]);
        let same = Spec::parse("slots=1;save=F5;load=F5").expect("a spec");
        assert_eq!(same.codes(), [63]);
        let nine = Spec::parse("slots=9;save=F{slot};load=F{slot}").expect("a spec");
        assert_eq!(nine.codes().len(), 9);
    }

    #[test]
    fn the_clients_lines_are_slots_with_or_without_a_state() {
        let lines =
            parse_lines("slot:1\ttoday, 21:10\tusa.zelda\nslot:2\t\t\nslot:x\t\t\nslot:0\t\t\nslot:10\t\t\n");
        assert_eq!(lines.len(), 2);
        assert!(lines[0].present() && lines[0].slot == 1 && lines[0].detail == "usa.zelda");
        assert!(!lines[1].present() && lines[1].slot == 2);
        assert_eq!(present_bits(&lines), 1);
        assert_eq!(parse_lines("slot:3\ttoday\tg\r\n")[0].detail, "g", "CRLF");
        assert_eq!(
            parse_lines("slot:1")[0].when,
            "",
            "no tabs at all is an empty slot"
        );
        let long = format!("slot:1\t{}\t{}\n", "w".repeat(500), "é".repeat(500));
        assert_eq!(
            parse_lines(&long)[0].detail.chars().count(),
            80,
            "by characters, not bytes"
        );
        let many: String = (1..=9)
            .cycle()
            .take(40)
            .map(|n| format!("slot:{n}\t\t\n"))
            .collect();
        assert_eq!(parse_lines(&many).len(), SLOTS_MAX as usize);
    }

    #[test]
    fn present_bits_follow_the_listed_position_not_the_slot_number() {
        let line = |slot, when: &str| StateLine {
            slot,
            when: when.to_owned(),
            detail: String::new(),
        };
        assert_eq!(present_bits(&[]), 0);
        assert_eq!(present_bits(&[line(3, "t")]), 0b1);
        assert_eq!(
            present_bits(&[line(1, ""), line(2, "t"), line(3, ""), line(4, "t")]),
            0b1010
        );
    }
}
