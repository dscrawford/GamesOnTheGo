//! The few danstick events the overlay listens to, read off one line of JSON.
//!
//! danstick broadcasts to every client on its socket; the overlay is one more,
//! beside the picker and the gate. It wants three things: a hold filling
//! (`progress`), a seat taken (`claim`), and who is seated (`state`) -- and
//! nothing else, so everything else is [`Event::Other`] and ignored. A line
//! of the wrong shape is a stranger, never a crash: it comes from another
//! program.

use serde_json::{Map, Value};

use crate::pairing::Pairing;
use crate::pressing::{self, Binding, Kind};

/// Controls read off one `mapping`'s `captured`; more are ignored.
pub const CAPTURED_MAX: usize = 64;

/// Seats read off one `state`; more are ignored.
pub const SEATED_MAX: usize = 8;

#[derive(Debug, Clone, Default, PartialEq)]
pub struct Seat {
    pub node: String,
    pub name: String,
    pub player: i32,
    /// Whether danstick knows this pad's buttons: gate.py's `Seat.mapped`.
    pub mapped: bool,
}

/// A seat danstick knows the buttons of: any capture, since danstick falls
/// back to the universal one, or `configured`. A keyboard is its own
/// mapping, and a daemon that says neither is taken at its word that the pad
/// works -- asking every pad to be walked because the fields are missing
/// would be worse than asking none.
fn mapped(seat: &Map<String, Value>) -> bool {
    let scopes = seat.get("mappings").and_then(Value::as_array);
    let configured = seat.get("configured").and_then(Value::as_bool);
    flag(seat, "keyboard")
        || configured == Some(true)
        || scopes.is_some_and(|scopes| !scopes.is_empty())
        || (configured.is_none() && scopes.is_none())
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
        /// Whether a hold would take a seat now (`seating`); None from a
        /// daemon that does not say.
        listening: Option<bool>,
        /// idle, assigning or ready.
        status: String,
        /// Seats danstick has; 0 when it does not say.
        slots: i32,
    },
    /// A step of the mapping wizard, or its end (`done`).
    Mapping {
        player: i32,
        control: String,
        index: i32,
        total: i32,
        done: bool,
        stored: bool,
        /// What the walk has bound so far, each control once.
        captured: Vec<Binding>,
    },
    /// A raw input on the pad under the wizard: see [`pressing`].
    Input {
        player: i32,
        kind: Kind,
        index: i32,
        value: f32,
    },
    /// The long hold that ends a mapping run early, keeping what is bound.
    Finish {
        player: i32,
        frac: f64,
    },
    /// danstick refused a command.
    Error {
        message: String,
    },
    Other,
}

/// A small count off a line: anything else is 0.
fn count(object: &Map<String, Value>, field: &str) -> i32 {
    match object.get(field).and_then(Value::as_f64) {
        Some(value) if (0.0..=1000.0).contains(&value) => value as i32,
        _ => 0,
    }
}

fn flag(object: &Map<String, Value>, field: &str) -> bool {
    object.get(field).and_then(Value::as_bool).unwrap_or(false)
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
                            mapped: mapped(seat),
                        })
                        .collect()
                })
                .unwrap_or_default(),
            listening: root.get("seating").and_then(Value::as_bool),
            status: text(&root, "state"),
            slots: count(&root, "slots"),
        },
        Some("mapping") => Event::Mapping {
            player: player_of(&root),
            control: text(&root, "control"),
            index: count(&root, "index"),
            total: count(&root, "total"),
            done: flag(&root, "done"),
            stored: flag(&root, "stored"),
            captured: root
                .get("captured")
                .and_then(Value::as_object)
                .map(|captured| {
                    captured
                        .iter()
                        .filter_map(|(control, sdl)| pressing::spelled(control, sdl.as_str()?))
                        .take(CAPTURED_MAX)
                        .collect()
                })
                .unwrap_or_default(),
        },
        Some("input") => match (
            root.get("kind").and_then(Value::as_str).and_then(Kind::named),
            root.get("index").and_then(Value::as_f64),
            root.get("value").and_then(Value::as_f64),
        ) {
            (Some(kind), Some(index), Some(value)) if (0.0..256.0).contains(&index) && value.is_finite() => {
                Event::Input {
                    player: player_of(&root),
                    kind,
                    index: index as i32,
                    value: value.clamp(-16.0, 16.0) as f32,
                }
            }
            _ => Event::Other,
        },
        Some("finish") => Event::Finish {
            player: player_of(&root),
            frac: root.get("frac").and_then(Value::as_f64).unwrap_or(0.0),
        },
        Some("error") => Event::Error {
            message: text(&root, "message"),
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
        Event::State { seated, .. } => {
            pairing.room(Some(seated.len()));
            for seat in seated {
                pairing.seated(&seat.node, &seat.name, seat.player);
            }
        }
        // The rebind's, not the joining picture's: see `rebind`.
        Event::Mapping { .. }
        | Event::Finish { .. }
        | Event::Input { .. }
        | Event::Error { .. }
        | Event::Other => {}
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
        let Some(Event::State { seated, .. }) = parsed(line) else {
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
        let Some(Event::State { seated, .. }) = parsed(&line) else {
            panic!("not a state")
        };
        assert_eq!(seated.len(), SEATED_MAX, "more seats than fit are capped");
        let Some(Event::State { seated, .. }) =
            parsed(r#"{"event":"state","players":[1,"x",null,{"player":2,"node":"n"}]}"#)
        else {
            panic!("not a state")
        };
        assert_eq!(seated.len(), 1, "entries that are not objects are skipped");
        assert_eq!(
            parsed(r#"{"event":"state","players":"nope"}"#),
            Some(Event::State {
                seated: vec![],
                listening: None,
                status: String::new(),
                slots: 0
            })
        );
    }

    #[test]
    fn a_state_says_whether_danstick_is_listening_for_a_hold() {
        assert!(matches!(
            parsed(r#"{"event":"state","state":"idle","slots":4,"seating":false,"players":[]}"#),
            Some(Event::State { listening: Some(false), ref status, slots: 4, .. }) if status == "idle"
        ));
        assert!(matches!(
            parsed(r#"{"event":"state","players":[]}"#),
            Some(Event::State {
                listening: None,
                slots: 0,
                ..
            })
        ));
    }

    #[test]
    fn a_seat_says_whether_danstick_knows_its_buttons() {
        let seats = |line: &str| match parsed(line) {
            Some(Event::State { seated, .. }) => seated.iter().map(|s| s.mapped).collect::<Vec<_>>(),
            other => panic!("not a state: {other:?}"),
        };
        assert_eq!(
            seats(
                r#"{"event":"state","players":[
                {"player":1,"configured":false,"mappings":[]},
                {"player":2,"configured":true,"mappings":[]},
                {"player":3,"configured":false,"mappings":["console:n64"]},
                {"player":4,"configured":false,"mappings":[],"keyboard":true},
                {"player":5}]}"#
            ),
            [false, true, true, true, true]
        );
    }

    #[test]
    fn the_wizards_steps_its_finish_and_a_refusal_are_read() {
        assert_eq!(
            parsed(r#"{"event":"mapping","player":2,"control":"a","label":"A","index":3,"total":14}"#),
            Some(Event::Mapping {
                player: 2,
                control: "a".into(),
                index: 3,
                total: 14,
                done: false,
                stored: false,
                captured: Vec::new(),
            })
        );
        assert!(matches!(
            parsed(r#"{"event":"mapping","player":2,"done":true,"stored":true}"#),
            Some(Event::Mapping {
                done: true,
                stored: true,
                ..
            })
        ));
        assert_eq!(
            parsed(r#"{"event":"finish","player":2,"frac":0.5}"#),
            Some(Event::Finish { player: 2, frac: 0.5 })
        );
        assert_eq!(
            parsed(r#"{"event":"error","message":"no such player"}"#),
            Some(Event::Error {
                message: "no such player".into()
            })
        );
        assert!(
            matches!(
                parsed(r#"{"event":"mapping","index":-4,"total":"many"}"#),
                Some(Event::Mapping {
                    index: 0,
                    total: 0,
                    ..
                })
            ),
            "odd counts are 0, not a crash"
        );
    }

    #[test]
    fn what_the_walk_has_bound_and_what_is_under_the_thumb_are_read() {
        let Some(Event::Mapping { captured, .. }) = parsed(
            r#"{"event":"mapping","player":1,"control":"b","captured":{"a":"b0","dpup":"h0.1","x":7,"leftstick_left":"-a0"}}"#,
        ) else {
            panic!("a mapping");
        };
        let named: Vec<&str> = captured.iter().map(|b| b.control.as_str()).collect();
        assert_eq!(
            named,
            ["a", "dpup", "leftstick_left"],
            "a spelling that is not one is skipped"
        );
        assert_eq!(
            parsed(r#"{"event":"input","player":1,"kind":"axis","index":2,"value":-0.95}"#),
            Some(Event::Input {
                player: 1,
                kind: Kind::Axis,
                index: 2,
                value: -0.95
            })
        );
        for odd in [
            r#"{"event":"input","player":1,"kind":"wheel","index":2,"value":1}"#,
            r#"{"event":"input","player":1,"kind":"button","index":-1,"value":1}"#,
            r#"{"event":"input","player":1,"kind":"button","index":2}"#,
        ] {
            assert_eq!(parsed(odd), Some(Event::Other), "{odd}");
        }
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
