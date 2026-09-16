"""The launch-time controller check.

The property that matters most is the one about *not* appearing: a machine
somebody has already set up must reach the game without being asked anything,
or the check is a toll on every launch rather than a fix for the launches that
would not have worked.
"""

from gotg_ui.gate import (
    ANCHOR_ALIASES,
    CHECKING,
    MAPPING,
    READY,
    SEATING,
    SKIPPED,
    Gate,
    Seat,
    anchor_names,
    apply,
    artwork_for,
    console_scope,
    decide,
    layout_for,
    seats_from,
)


def state_event(players):
    return {"event": "state", "state": "ready", "slots": 4, "players": players}


def seated(player=1, name="Xbox Wireless Controller", mappings=()):
    return {"player": player, "name": name, "mappings": list(mappings)}


# --- which console's buttons -------------------------------------------------


def test_a_platform_padmap_has_a_layout_for_uses_it():
    assert layout_for("n64") == "n64"
    assert layout_for("gamecube") == "gamecube"


def test_wii_is_captured_as_a_gamecube_pad():
    # Dolphin here is configured for GameCube pads only, so a Wii game is
    # played with one and the control set is the same.
    assert layout_for("wii") == "gamecube"


def test_a_platform_with_no_layout_falls_back_rather_than_borrowing_one():
    # Not snes: the NES has no shoulder buttons, and a capture that asks for
    # two that do not exist is a capture nobody can finish.
    assert layout_for("nes") == "generic"
    assert layout_for("gb") == "generic"


def test_the_scope_is_padmaps_own_spelling():
    assert console_scope("gamecube") == "console:gamecube"


# --- the gate that should not appear -----------------------------------------


def test_a_mapped_controller_goes_straight_to_the_game():
    gate = Gate(platform="gamecube")
    gate = apply(gate, state_event([seated(mappings=["console:gamecube"])]))
    gate, command = decide(gate)
    assert gate.state == READY
    assert command is None
    assert gate.done


def test_a_pad_mapped_for_another_console_is_still_asked_about():
    gate = Gate(platform="n64")
    gate = apply(gate, state_event([seated(mappings=["console:gamecube"])]))
    gate, command = decide(gate)
    assert gate.state == MAPPING
    assert command == {"cmd": "map", "player": 1, "layout": "n64", "scope": "console:n64"}


def test_the_first_unmapped_pad_is_the_one_asked_about():
    gate = Gate(platform="snes")
    gate = apply(gate, state_event([
        seated(player=1, mappings=["console:snes"]),
        seated(player=2, mappings=[]),
    ]))
    gate, command = decide(gate)
    assert command["player"] == 2


# --- no controller at all ----------------------------------------------------


def test_no_controller_opens_a_session_to_take_a_seat_in():
    gate = Gate(platform="gamecube")
    gate = apply(gate, state_event([]))
    gate, command = decide(gate)
    assert gate.state == SEATING
    assert command == {"cmd": "begin", "players": 4}


def test_asking_happens_once_however_many_state_events_arrive():
    gate = Gate(platform="gamecube")
    gate = apply(gate, state_event([]))
    gate, first = decide(gate)
    gate = apply(gate, state_event([]))
    gate, second = decide(gate)
    assert first is not None
    assert second is None


def test_a_hold_fills_the_seat_and_the_mapping_question_follows():
    gate = Gate(platform="gamecube")
    gate = apply(gate, state_event([]))
    gate, _ = decide(gate)
    gate = apply(gate, {"event": "claim", "player": 1, "name": "GOTG test pad"})
    assert gate.seated == 1
    # Still inside the session; `accepted` is what makes the seat real, and
    # only then is there a mapping to look for.
    gate = apply(gate, {"event": "accepted"})
    gate = apply(gate, state_event([seated(name="GOTG test pad")]))
    gate, command = decide(gate)
    assert gate.state == MAPPING
    assert command["cmd"] == "map"


# --- walking the buttons -----------------------------------------------------


def test_a_wizard_step_says_which_button_to_press():
    gate = Gate(platform="gamecube", state=MAPPING)
    gate = apply(gate, {
        "event": "mapping", "player": 1, "control": "start",
        "label": "Start", "index": 4, "total": 16, "done": False,
    })
    assert gate.prompt == "press Start"
    assert (gate.index, gate.total) == (4, 16)


def test_a_button_already_used_says_so_rather_than_looking_dead():
    gate = Gate(platform="gamecube", state=MAPPING)
    gate = apply(gate, {
        "event": "mapping", "control": "b", "label": "B",
        "conflict": "a", "done": False,
    })
    assert "already a" in gate.prompt


def test_a_stored_capture_sends_the_gate_back_to_look_again():
    gate = Gate(platform="gamecube", state=MAPPING, asked=True)
    gate = apply(gate, {"event": "mapping", "done": True, "stored": True})
    assert gate.state == CHECKING
    assert not gate.asked
    # And with the mapping now on the pad, the next look reaches the game.
    gate = apply(gate, state_event([seated(mappings=["console:gamecube"])]))
    gate, command = decide(gate)
    assert gate.state == READY
    assert command is None


def test_an_abandoned_capture_does_not_ask_again():
    # Backing out of the wizard is an answer. Asking a second time would make
    # the way out of the screen the screen itself.
    gate = Gate(platform="gamecube", state=MAPPING, asked=True)
    gate = apply(gate, {"event": "mapping", "done": True, "stored": False})
    assert gate.state == SKIPPED
    assert gate.done


# --- what it is given --------------------------------------------------------


def test_seats_are_read_in_player_order():
    seats = seats_from([seated(player=3), seated(player=1)])
    assert [s.player for s in seats] == [1, 3]


def test_a_malformed_player_is_ignored_rather_than_raising():
    # The daemon is the authority and may grow fields; a front-end that died
    # on one would break on every padmap release.
    assert seats_from([{"name": "no player number"}, seated(player=2)]) == (
        Seat(player=2, name="Xbox Wireless Controller"),
    )


def test_an_unknown_event_leaves_the_gate_alone():
    gate = Gate(platform="n64", state=SEATING)
    assert apply(gate, {"event": "calibration", "frac": 0.5}) == gate


def test_an_error_is_shown_rather_than_thrown():
    gate = apply(Gate(), {"event": "error", "message": "no joypads found"})
    assert gate.message == "no joypads found"


# --- which drawing, and which circle on it ----------------------------------


def test_a_console_with_artwork_uses_its_own():
    assert artwork_for("gamecube") == "gamecube"
    assert artwork_for("genesis") == "megadrive"


def test_a_console_with_no_artwork_gets_the_generic_pad():
    # Still a stick, a d-pad and four face buttons in the right places, which
    # is enough to point at the button being asked for.
    assert artwork_for("switch") == "generic"
    assert artwork_for("wiiu") == "generic"


def test_the_gamecube_drawing_is_looked_up_by_padmaps_own_name():
    # Its anchors are named for padmap's controls, so no translation is wanted
    # and the canonical name has to come first.
    assert anchor_names("righttrigger")[0] == "righttrigger"


def test_the_older_drawings_are_still_reachable_through_an_alias():
    # snes.svg and n64.svg predate padmap and name their anchors the way ares
    # does, so "dpup" has to be able to find "Up".
    assert anchor_names("dpup") == ("dpup", "Up")
    assert anchor_names("a") == ("a", "A")


def test_a_control_with_no_alias_asks_for_itself_only():
    assert anchor_names("rightstick_left") == ("rightstick_left",)


def test_no_control_asks_for_nothing():
    assert anchor_names("") == ()


def test_the_gamecube_drawing_has_a_circle_for_every_control_padmap_asks_for():
    """The artwork and the wizard have to agree, or a step points at nothing.

    Parsed rather than eyeballed: an anchor named `dpUp`, or a control padmap
    adds later, is a button somebody is asked to press with no mark on the pad
    and no error anywhere.
    """
    import pathlib
    import xml.etree.ElementTree as ET

    svg = pathlib.Path(__file__).resolve().parents[2] / "src/ui/assets/controllers/gamecube.svg"
    root = ET.parse(svg).getroot()
    found = {
        (el.get("id") or "")[len("anchor-"):]
        for el in root.iter()
        if (el.get("id") or "").startswith("anchor-")
    }
    # padmap's gamecube layout, as of the pinned revision.
    wanted = {
        "a", "b", "x", "y", "start",
        "dpup", "dpdown", "dpleft", "dpright",
        "leftshoulder", "rightshoulder", "righttrigger",
        "rightstick_up", "rightstick_down", "rightstick_left", "rightstick_right",
    }
    assert wanted <= found, sorted(wanted - found)


def test_every_alias_points_at_a_different_name():
    # A pair that collided would light the wrong button on half the consoles.
    assert len(set(ANCHOR_ALIASES.values())) == len(ANCHOR_ALIASES)
