//! What to draw, worked out from what the kill switch knows: the seats as the
//! menu lists them, the menu and the rebind as a frame carries them. Out of
//! main.rs, where they were plain functions beside the loop, because each is
//! a pure map from state to a value and the corners (more seats than a frame
//! holds, a drawing numbered like the empty marker, a control the console
//! does not have) are worth a test. Reading a pad is not here; the loop does
//! that and hands over what was `Held`.

use std::collections::HashMap;

use crate::consoles::Console;
use crate::events::Seat;
use crate::frame::{self, EMPTY_SEAT, MenuFrame, ROWS_MAX, Rebinding};
use crate::menu::{self, Row};
use crate::pressing::Held;
use crate::rebind::{Rebind, View};
use crate::seating::Seating;

/// The seats as the menu lists them: every slot danstick has, each with the
/// drawing of the pad in it, looked up once per node.
pub fn seat_rows(rebind: &Rebind, seating: &Seating, icons: &mut HashMap<String, u8>) -> Vec<Row> {
    rows_for(
        rebind.seated(),
        rebind.ports_off(),
        seating.slots(),
        icons,
        crate::icons::resolve,
    )
}

/// `seat_rows`, with the icon lookup (which reads /sys) handed in.
pub fn rows_for(
    seated: &[Seat],
    ports_off: &[i32],
    slots: i32,
    icons: &mut HashMap<String, u8>,
    mut resolve: impl FnMut(&str, &str) -> u8,
) -> Vec<Row> {
    let slots = seated
        .iter()
        .map(|seat| seat.player)
        .max()
        .unwrap_or(0)
        .max(slots)
        .clamp(1, menu::SEATS_MAX as i32);
    (1..=slots)
        .map(|player| Row {
            icon: seated.iter().find(|seat| seat.player == player).map(|seat| {
                let key = format!("{}|{}", seat.node, seat.name);
                *icons
                    .entry(key)
                    .or_insert_with(|| resolve(&seat.node, &seat.name))
            }),
            off: ports_off.contains(&player),
        })
        .collect()
}

/// The menu as a frame carries it.
pub fn menu_frame(
    view: &menu::View,
    console_index: usize,
    console: &Console,
    held: &[Held; ROWS_MAX],
) -> MenuFrame {
    let mut icons = [EMPTY_SEAT; ROWS_MAX];
    let mut off = 0u32;
    for (at, row) in view.rows.iter().take(ROWS_MAX).enumerate() {
        // A drawing numbered like the empty marker would read as empty.
        icons[at] = row.icon.map_or(EMPTY_SEAT, |icon| icon.min(EMPTY_SEAT - 1));
        if row.off {
            off |= 1 << at;
        }
    }
    MenuFrame {
        owner: view.owner,
        rows: view.rows.len().min(ROWS_MAX) as u32,
        icons,
        focus: view.focus as u32,
        carried: view.carried.unwrap_or(0),
        a_fill: view.a_fill,
        b_fill: view.b_fill,
        off,
        console: console_index as u32,
        pressed: std::array::from_fn(|at| held[at].bits(console.controls.iter().map(|control| control.id))),
        sticks: std::array::from_fn(|at| held[at].sticks),
        testing: view.testing,
        saves: frame::Saves {
            row: view.saves_row,
            browse: view.saves,
        },
    }
}

/// A rebind as the painter draws it.
pub fn rebinding(view: &View, held: &Held, console: &Console, console_index: usize) -> Rebinding {
    Rebinding {
        player: view.player,
        console: console_index as u32,
        control: console
            .control(&view.control)
            .and_then(|at| i32::try_from(at).ok())
            .unwrap_or(-1),
        index: view.index,
        total: view.total,
        finish: view.finish as f32,
        ended: match view.stored {
            None => 0,
            Some(true) => 1,
            Some(false) => 2,
        },
        pressed: held.bits(console.controls.iter().map(|control| control.id)),
        sticks: held.sticks,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::consoles::{CONSOLES, for_platform};

    fn seat(player: i32, node: &str) -> Seat {
        Seat {
            node: node.into(),
            name: format!("pad {player}"),
            player,
            mapped: true,
        }
    }

    fn view(rows: Vec<Row>) -> menu::View {
        menu::View {
            owner: 2,
            rows,
            focus: 1,
            carried: None,
            a_fill: 0.5,
            b_fill: 0.25,
            testing: false,
            saves_row: true,
            saves: None,
        }
    }

    #[test]
    fn every_slot_danstick_has_is_a_row_with_the_drawing_of_whoever_sits_in_it() {
        let mut icons = HashMap::new();
        let rows = rows_for(&[seat(2, "e2")], &[2], 4, &mut icons, |_, _| 7);
        assert_eq!(
            rows,
            vec![
                Row {
                    icon: None,
                    off: false
                },
                Row {
                    icon: Some(7),
                    off: true
                },
                Row {
                    icon: None,
                    off: false
                },
                Row {
                    icon: None,
                    off: false
                },
            ]
        );
    }

    #[test]
    fn a_seat_past_the_slots_danstick_reported_still_gets_a_row_up_to_the_most_a_menu_lists() {
        let mut icons = HashMap::new();
        let rows = rows_for(&[seat(6, "e6")], &[], 4, &mut icons, |_, _| 1);
        assert_eq!(rows.len(), 6);
        assert_eq!(rows[5].icon, Some(1));
        let rows = rows_for(&[seat(99, "e")], &[], 4, &mut icons, |_, _| 1);
        assert_eq!(rows.len(), menu::SEATS_MAX, "no more than the menu has room for");
        assert_eq!(
            rows_for(&[], &[], 0, &mut icons, |_, _| 1).len(),
            1,
            "at least one"
        );
    }

    #[test]
    fn a_pad_is_looked_up_once_per_node_and_name() {
        let mut icons = HashMap::new();
        let mut lookups = 0;
        for _ in 0..3 {
            rows_for(&[seat(1, "e1")], &[], 1, &mut icons, |_, _| {
                lookups += 1;
                3
            });
        }
        assert_eq!(lookups, 1);
    }

    #[test]
    fn the_menu_frame_marks_empty_seats_off_ports_and_keeps_a_drawing_from_looking_empty() {
        let console = &CONSOLES[for_platform("n64")];
        let rows = vec![
            Row {
                icon: Some(4),
                off: false,
            },
            Row {
                icon: None,
                off: true,
            },
            Row {
                icon: Some(EMPTY_SEAT),
                off: false,
            },
        ];
        let held: [Held; ROWS_MAX] = std::array::from_fn(|_| Held::default());
        let frame = menu_frame(&view(rows), 3, console, &held);
        assert_eq!(frame.icons[..4], [4, EMPTY_SEAT, EMPTY_SEAT - 1, EMPTY_SEAT]);
        assert_eq!((frame.rows, frame.off, frame.console), (3, 0b10, 3));
        assert_eq!((frame.owner, frame.focus, frame.carried), (2, 1, 0));
        assert!(frame.saves.row && frame.saves.browse.is_none());
    }

    #[test]
    fn the_menu_frame_lists_no_more_seats_than_a_frame_holds() {
        let console = &CONSOLES[for_platform("n64")];
        let rows = vec![
            Row {
                icon: Some(1),
                off: true
            };
            ROWS_MAX + 3
        ];
        let held: [Held; ROWS_MAX] = std::array::from_fn(|_| Held::default());
        let frame = menu_frame(&view(rows), 0, console, &held);
        assert_eq!(
            (frame.rows as usize, frame.off),
            (ROWS_MAX, u32::MAX >> (32 - ROWS_MAX))
        );
    }

    #[test]
    fn a_seats_presses_are_bits_of_the_consoles_own_list() {
        let console = &CONSOLES[for_platform("n64")];
        let first = console.controls[0].id;
        let mut held: [Held; ROWS_MAX] = std::array::from_fn(|_| Held::default());
        held[2].controls.insert(first.to_string());
        held[2].sticks = [0.5, -0.5, 0.0, 1.0];
        let frame = menu_frame(&view(vec![]), 0, console, &held);
        assert_eq!(frame.pressed[2], 1, "the first control is the lowest bit");
        assert_eq!((frame.pressed[0], frame.pressed[3]), (0, 0));
        assert_eq!(frame.sticks[2], [0.5, -0.5, 0.0, 1.0]);
    }

    fn walking(control: &str, stored: Option<bool>) -> View {
        View {
            player: 3,
            control: control.into(),
            index: 2,
            total: 14,
            finish: 0.5,
            stored,
            held: None,
        }
    }

    #[test]
    fn a_rebind_names_the_control_by_its_place_in_the_consoles_list_or_minus_one() {
        let console = &CONSOLES[for_platform("n64")];
        let id = console.controls[1].id;
        let held = Held::default();
        let drawn = rebinding(&walking(id, None), &held, console, 5);
        assert_eq!((drawn.player, drawn.console, drawn.control), (3, 5, 1));
        assert_eq!((drawn.index, drawn.total, drawn.finish), (2, 14, 0.5));
        let before_first = rebinding(&walking("", None), &held, console, 5);
        assert_eq!(before_first.control, -1);
        let unknown = rebinding(&walking("no-such-control", None), &held, console, 5);
        assert_eq!(
            unknown.control, -1,
            "a control the console does not draw is walked unlabelled"
        );
    }

    #[test]
    fn a_rebinds_ending_is_kept_or_given_up_on() {
        let console = &CONSOLES[for_platform("n64")];
        let held = Held::default();
        let ended = |stored| rebinding(&walking("", stored), &held, console, 0).ended;
        assert_eq!((ended(None), ended(Some(true)), ended(Some(false))), (0, 1, 2));
    }
}
