//! The few padmap events the overlay listens to, read off one line of JSON.
//!
//! padmap broadcasts to every client on its socket; the overlay is one more,
//! beside the picker and the gate. It wants three things: a hold filling
//! (`progress`), a seat taken (`claim`), and who is seated (`state`) -- and
//! nothing else, so everything else is [`Event::Other`] and ignored. A line
//! of the wrong shape is a stranger, never a crash: it comes from another
//! program.

use serde_json::{Map, Value};

use crate::pairing::Pairing;

/// Seats read off one `state`; more are ignored.
pub const SEATED_MAX: usize = 8;

#[derive(Debug, Clone, Default, PartialEq)]
pub struct Seat {
    pub node: String,
    pub name: String,
    pub player: i32,
}

#[derive(Debug, Clone, PartialEq)]
pub enum Event {
    Progress {
        frac: f64,
        node: String,
        name: String,
        player: i32,
    },
    Claim {
        node: String,
        name: String,
        player: i32,
    },
    State {
        seated: Vec<Seat>,
    },
    Other,
}

fn text(object: &Map<String, Value>, field: &str) -> String {
    object
        .get(field)
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned()
}

/// A seat is a small positive integer; anything else is "not said".
fn player_of(object: &Map<String, Value>) -> i32 {
    match object.get("player").and_then(Value::as_f64) {
        Some(value) if (1.0..=64.0).contains(&value) => value as i32,
        _ => 0,
    }
}

/// One line, without its newline. None for anything that is not a JSON
/// object; an object this does not know is [`Event::Other`].
pub fn parse(line: &[u8]) -> Option<Event> {
    let Value::Object(root) = serde_json::from_slice(line).ok()? else {
        return None;
    };
    let event = match root.get("event").and_then(Value::as_str) {
        Some("progress") => match root.get("frac").and_then(Value::as_f64) {
            Some(frac) => Event::Progress {
                frac,
                node: text(&root, "node"),
                name: text(&root, "name"),
                player: player_of(&root),
            },
            None => Event::Other,
        },
        Some("claim") => Event::Claim {
            node: text(&root, "node"),
            name: text(&root, "name"),
            player: player_of(&root),
        },
        Some("state") => Event::State {
            seated: root
                .get("players")
                .and_then(Value::as_array)
                .map(|players| {
                    players
                        .iter()
                        .filter_map(Value::as_object)
                        .take(SEATED_MAX)
                        .map(|seat| Seat {
                            node: text(seat, "node"),
                            name: text(seat, "name"),
                            player: player_of(seat),
                        })
                        .collect()
                })
                .unwrap_or_default(),
        },
        _ => Event::Other,
    };
    Some(event)
}

/// What one event does to the pairing picture. `icon_of` names the drawing
/// for a pad from its node and name -- `icons::resolve` on a real machine,
/// something fixed in a test that has no such devices.
pub fn apply(event: &Event, pairing: &mut Pairing, now: f64, icon_of: &mut dyn FnMut(&str, &str) -> u8) {
    match event {
        Event::Progress {
            frac,
            node,
            name,
            player,
        } => pairing.progress(node, name, *frac, *player, icon_of(node, name), now),
        Event::Claim { node, name, player } => pairing.claim(node, name, *player, icon_of(node, name), now),
        // Only the holds that have become seats. A `state` arrives while
        // somebody is still holding, and clearing everything on one was the
        // flash back to an empty seat in the picker.
        Event::State { seated } => {
            for seat in seated {
                pairing.seated(&seat.node, &seat.name, seat.player);
            }
        }
        Event::Other => {}
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::pairing::Pairing;

    fn parsed(line: &str) -> Option<Event> {
        parse(line.as_bytes())
    }

    #[test]
    fn a_progress_line_is_read() {
        let line = r#"{"event":"progress","frac":0.42,"node":"/dev/input/event9","name":"Pad","player":2}"#;
        assert_eq!(
            parsed(line),
            Some(Event::Progress {
                frac: 0.42,
                node: "/dev/input/event9".into(),
                name: "Pad".into(),
                player: 2
            })
        );
    }

    #[test]
    fn a_state_names_who_is_seated() {
        let line = r#"{"event":"state","players":[{"player":1,"node":"/dev/input/event3"},{"player":2,"name":"Pad"}],"slots":4}"#;
        let Some(Event::State { seated }) = parsed(line) else {
            panic!("not a state")
        };
        assert_eq!(seated.len(), 2);
    }

    #[test]
    fn nonsense_is_refused_and_strangers_ignored() {
        assert_eq!(parsed("{not json"), None);
        assert_eq!(parsed("[1,2]"), None, "not an object");
        assert_eq!(
            parsed(r#"{"event":"sdl_mapping","lines":[]}"#),
            Some(Event::Other)
        );
        let wild = parsed(r#"{"event":"claim","player":1e9}"#);
        assert_eq!(
            wild,
            Some(Event::Claim {
                node: String::new(),
                name: String::new(),
                player: 0
            })
        );
    }

    #[test]
    fn wrong_types_are_strangers_not_crashes() {
        assert_eq!(
            parsed(r#"{"event":"progress","frac":"0.5","node":"n"}"#),
            Some(Event::Other)
        );
        assert_eq!(parsed(r#"{"event":1,"frac":0.5}"#), Some(Event::Other));
        assert_eq!(parsed("{}"), Some(Event::Other));
        assert!(matches!(
            parsed(r#"{"event":"claim","player":{"x":1}}"#),
            Some(Event::Claim { player: 0, .. })
        ));
        assert!(matches!(
            parsed(r#"{"event":"progress","frac":0.5,"node":{"deep":[1,{"x":true}]}}"#),
            Some(Event::Progress { ref node, .. }) if node.is_empty()
        ));
    }

    #[test]
    fn state_seats_are_capped_and_strangers_skipped() {
        let many: Vec<String> = (0..SEATED_MAX + 5)
            .map(|i| format!(r#"{{"player":{},"node":"n{i}"}}"#, i + 1))
            .collect();
        let line = format!(r#"{{"event":"state","players":[{}]}}"#, many.join(","));
        let Some(Event::State { seated }) = parsed(&line) else {
            panic!("not a state")
        };
        assert_eq!(seated.len(), SEATED_MAX, "more seats than fit are capped");
        let Some(Event::State { seated }) =
            parsed(r#"{"event":"state","players":[1,"x",null,{"player":2,"node":"n"}]}"#)
        else {
            panic!("not a state")
        };
        assert_eq!(seated.len(), 1, "entries that are not objects are skipped");
        assert_eq!(
            parsed(r#"{"event":"state","players":"nope"}"#),
            Some(Event::State { seated: vec![] })
        );
    }

    #[test]
    fn a_bare_value_is_not_an_event() {
        for line in ["null", "42", "\"hi\"", ""] {
            assert_eq!(parsed(line), None, "{line:?}");
        }
    }

    /// Which argument the look-up was given, told apart without sysfs.
    fn by_argument(node: &str, name: &str) -> u8 {
        match (node.is_empty(), name.is_empty()) {
            (false, _) => 1,
            (true, false) => 2,
            (true, true) => 3,
        }
    }

    #[test]
    fn a_hold_and_a_seat_carry_the_drawing_their_pad_resolves_to() {
        let mut p = Pairing::new(1.5);
        let progress = |node: &str, name: &str| Event::Progress {
            frac: 0.4,
            node: node.into(),
            name: name.into(),
            player: 1,
        };
        apply(
            &progress("/dev/input/event9", "Pad"),
            &mut p,
            10.0,
            &mut by_argument,
        );
        assert_eq!(
            p.now(10.0)[0].icon,
            1,
            "the node and the name both reach the look-up"
        );
        let mut p = Pairing::new(1.5);
        apply(&progress("", "Pad"), &mut p, 10.0, &mut by_argument);
        assert_eq!(p.now(10.0)[0].icon, 2);
        let claim = Event::Claim {
            node: "/dev/input/event9".into(),
            name: "Pad".into(),
            player: 3,
        };
        apply(&claim, &mut p, 10.0, &mut by_argument);
        assert_eq!(p.joined(10.0), [(3, 1)]);
        apply(&Event::Other, &mut p, 10.0, &mut |_, _| {
            unreachable!("nothing to look up")
        });
    }
}
