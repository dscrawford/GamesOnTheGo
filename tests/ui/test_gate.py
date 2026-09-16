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


def state_event(players, state=None):
    """padmap's state. "assigning" is how it says a session is open."""
    if state is None:
        state = "ready" if players else "idle"
    return {"event": "state", "state": state, "slots": 4, "players": players}


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
    # A session first. Binding buttons is refused without one, so a seated pad
    # that needs mapping means opening a session it is already seated in.
    gate = Gate(platform="n64")
    gate = apply(gate, state_event([seated(mappings=["console:gamecube"])]))
    gate, command = decide(gate)
    assert command == {"cmd": "begin", "players": 4}
    gate = apply(gate, state_event([seated(mappings=["console:gamecube"])], state="assigning"))
    gate, command = decide(gate)
    assert gate.state == MAPPING
    assert command == {"cmd": "map", "player": 1, "layout": "n64", "scope": "console:n64"}


def test_the_first_unmapped_pad_is_the_one_asked_about():
    players = [seated(player=1, mappings=["console:snes"]), seated(player=2, mappings=[])]
    gate = Gate(platform="snes")
    gate = apply(gate, state_event(players, state="assigning"))
    gate, command = decide(gate)
    assert command["cmd"] == "map"
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


def test_the_whole_sequence_for_a_machine_with_nothing_set_up():
    """begin, hold a button, bind the buttons, accept. In that order.

    The order is the point: `map` is refused with "mapping needs an open
    session", and `accept` is what closes the session -- so accepting the seat
    before binding, which is the obvious order, is the one that cannot work.
    """
    gate = Gate(platform="gamecube")
    gate = apply(gate, state_event([]))
    gate, command = decide(gate)
    assert command == {"cmd": "begin", "players": 4}
    assert gate.state == SEATING

    gate = apply(gate, state_event([], state="assigning"))
    gate, command = decide(gate)
    assert command is None          # padmap is reading the pads; nothing to send

    gate = apply(gate, {"event": "claim", "player": 1, "name": "GOTG test pad"})
    gate = apply(gate, state_event([seated(name="GOTG test pad")], state="assigning"))
    gate, command = decide(gate)
    assert gate.state == MAPPING
    assert command["cmd"] == "map"

    gate = apply(gate, {"event": "mapping", "control": "a", "label": "A", "index": 0, "total": 16})
    gate = apply(gate, {"event": "mapping", "done": True, "stored": True})
    gate = apply(gate, state_event(
        [seated(name="GOTG test pad", mappings=["console:gamecube"])], state="assigning"))
    gate, command = decide(gate)
    assert command == {"cmd": "accept"}

    gate = apply(gate, {"event": "accepted"})
    assert gate.state == READY
    assert gate.done


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
    gate = Gate(platform="gamecube", state=MAPPING, awaiting="map", session=True)
    gate = apply(gate, {"event": "mapping", "done": True, "stored": True})
    assert gate.state == CHECKING
    assert gate.awaiting == ""
    # With the mapping on the pad there is nothing left to ask, so the session
    # it was bound in is closed.
    gate = apply(gate, state_event([seated(mappings=["console:gamecube"])], state="assigning"))
    gate, command = decide(gate)
    assert command == {"cmd": "accept"}


def test_an_abandoned_capture_does_not_ask_again():
    # Backing out of the wizard is an answer. Asking a second time would make
    # the way out of the screen the screen itself.
    gate = Gate(platform="gamecube", state=MAPPING, awaiting="map", session=True)
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


# --- when padmap says no -----------------------------------------------------


def test_a_refused_bind_is_not_asked_for_again():
    """padmap refuses `map` without an open session, and would refuse it the
    same way for ever. Resending it every frame is a screen that never moves
    and a socket that never stops."""
    gate = Gate(platform="gamecube", session=True, awaiting="map")
    gate = apply(gate, {"event": "error", "message": "mapping needs an open session"})
    assert gate.awaiting == ""
    assert "map" in gate.refused
    assert gate.state == SKIPPED
    gate, command = decide(gate)
    assert command is None


def test_a_refused_session_leaves_the_screen_up():
    # "no joypads found" is answered by plugging one in, and padmap opens a
    # session by itself when somebody does -- so this one waits.
    gate = Gate(platform="gamecube", state=SEATING, awaiting="begin")
    gate = apply(gate, {"event": "error", "message": "no joypads found"})
    assert gate.state == SEATING
    assert not gate.done
    gate, command = decide(gate)
    assert command is None
    # And the session padmap opens on its own is picked up.
    gate = apply(gate, state_event([], state="assigning"))
    assert gate.session


def test_one_command_is_in_flight_at_a_time():
    gate = Gate(platform="gamecube")
    gate = apply(gate, state_event([]))
    gate, first = decide(gate)
    gate, second = decide(gate)
    assert first == {"cmd": "begin", "players": 4}
    assert second is None


def test_the_gamecube_control_names_match_the_drawing():
    """One table, not two. The binding screen reads these and the artwork has
    a circle for each; a name in one and not the other is a button that draws
    nothing or a label pointing at empty plastic."""
    import pathlib
    import xml.etree.ElementTree as ET

    from gotg_ui.gate import controls_for

    svg = pathlib.Path(__file__).resolve().parents[2] / "src/ui/assets/controllers/gamecube.svg"
    root = ET.parse(svg).getroot()
    drawn = {
        (el.get("id") or "")[len("anchor-"):]
        for el in root.iter()
        if (el.get("id") or "").startswith("anchor-")
    }
    named = set(controls_for("gamecube"))
    assert named == drawn


def test_a_console_nothing_here_knows_gets_no_invented_controls():
    from gotg_ui.gate import controls_for

    assert controls_for("ps2") == {}
