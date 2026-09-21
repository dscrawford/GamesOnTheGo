"""The launch-time controller check.

Every launch begins the same way: whatever the daemon remembers is forgotten,
and the person about to play holds a button. What must *not* appear after
that is the wizard: a pad that already knows this console's buttons is asked
for its hold and nothing more, or the check is a toll on every launch rather
than a fix for the launches that would not have worked.
"""

from gotg_ui.gate import (
    CHECKING,
    MAPPING,
    READY,
    READYING,
    SEATING,
    SKIPPED,
    Gate,
    GoHold,
    Seat,
    apply,
    console_scope,
    decide,
    layout_for,
    ready_from_the_keyboard,
    seats_from,
)


def state_event(players, state=None):
    """padmap's state. "assigning" is how it says a session is open."""
    if state is None:
        state = "ready" if players else "idle"
    return {"event": "state", "state": state, "slots": 4, "players": players}


def seated(player=1, name="Xbox Wireless Controller", mappings=(), configured=None):
    """A player as the daemon reports one.

    `configured` defaults to whether there is a capture, which is what padmap
    does: it is "mapped, not merely known".
    """
    return {
        "player": player,
        "name": name,
        "mappings": list(mappings),
        "configured": bool(mappings) if configured is None else configured,
    }


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


# --- every launch begins unseated -------------------------------------------


def test_a_daemon_that_remembers_seats_is_told_to_forget_them_first():
    # A seat is taken in front of the screen about to be used, not remembered
    # from last night. Whatever padmap restored is not this launch's.
    gate = Gate(platform="gamecube")
    gate = apply(gate, state_event([seated(mappings=["console:gamecube"])]))
    gate, command = decide(gate)
    assert command == {"cmd": "unseat"}
    assert gate.state == SEATING
    assert gate.awaiting == "unseat"


def test_the_state_after_unseating_answers_it_and_a_hold_is_asked_for():
    gate = Gate(platform="gamecube")
    gate = apply(gate, state_event([seated()]))
    gate, _ = decide(gate)
    gate = apply(gate, state_event([]))
    assert gate.awaiting == ""
    gate, command = decide(gate)
    assert command == {"cmd": "begin", "players": 4}
    assert gate.state == SEATING


def test_a_stale_state_with_everybody_still_seated_does_not_answer_the_unseat():
    # The daemon greets a connection with a state and answers `status` with
    # another; the second was still in the socket when unseat went out. Read
    # as the answer, it had the gate mapping a pad it meant to forget.
    gate = Gate(platform="gamecube")
    gate = apply(gate, state_event([seated()]))
    gate, _ = decide(gate)
    gate = apply(gate, state_event([seated()]))          # the stale one
    assert gate.awaiting == "unseat"
    gate, command = decide(gate)
    assert command is None
    gate = apply(gate, state_event([]))                   # the real answer
    gate, command = decide(gate)
    assert command == {"cmd": "begin", "players": 4}


def test_a_claim_while_unseating_is_this_launches_own_seat():
    # Somebody held a button in the window between the unseat and its answer.
    # That is the hold the gate was about to ask for.
    gate = Gate(platform="gamecube")
    gate = apply(gate, state_event([seated()]))
    gate, _ = decide(gate)
    gate = apply(gate, {"event": "claim", "player": 1, "name": "Xbox Wireless Controller"})
    assert gate.awaiting == ""
    assert gate.unseated


def test_a_seat_taken_in_front_of_this_screen_is_not_forgotten_too():
    # Once, or the gate is one nobody gets past: the hold seats somebody, the
    # state says so, and a gate that forgot again would unseat them for ever.
    gate = Gate(platform="gamecube")
    gate = apply(gate, state_event([seated()]))
    gate, _ = decide(gate)                                    # unseat
    gate = apply(gate, state_event([]))
    gate, _ = decide(gate)                                    # begin
    gate = apply(gate, state_event([], state="assigning"))
    gate = apply(gate, {"event": "claim", "player": 1, "name": "Xbox Wireless Controller"})
    gate = apply(gate, state_event([seated(mappings=["console:gamecube"])], state="assigning"))
    gate, command = decide(gate)
    assert command is None and gate.state == READYING, "the seat this launch took was forgotten again"


def test_a_daemon_that_cannot_unseat_leaves_the_seats_standing():
    # An older daemon, or a session somebody else has open. The seats stand,
    # and the gate looks at them the way it always did: mapped means go.
    gate = Gate(platform="gamecube")
    gate = apply(gate, state_event([seated(mappings=["console:gamecube"])]))
    gate, _ = decide(gate)
    gate = apply(gate, {"event": "error", "message": 'unknown command "unseat"'})
    assert "unseat" in gate.refused
    gate, command = decide(gate)
    assert gate.state == READY
    assert command is None


# --- the wizard that should not appear ---------------------------------------


def test_a_mapped_controller_is_asked_nothing_but_to_ready_up():
    # Seated in front of this screen, in a session: no wizard, no accept
    # either. The game waits for the hold that says "I am ready".
    gate = Gate(platform="gamecube", unseated=True)
    gate = apply(gate, state_event([seated(mappings=["console:gamecube"])], state="assigning"))
    gate, command = decide(gate)
    assert gate.state == READYING
    assert command is None
    assert not gate.done
    assert "hold a button" in gate.prompt


def test_a_seated_and_mapped_pad_outside_any_session_still_goes_straight_in():
    # No session means no confirm hold for padmap to read -- the older-daemon
    # path, where unseat was refused and the seats stood.
    gate = Gate(platform="gamecube", unseated=True)
    gate = apply(gate, state_event([seated(mappings=["console:gamecube"])]))
    gate, command = decide(gate)
    assert gate.state == READY
    assert command is None
    assert gate.done


def test_enter_readies_up_from_the_keyboard_only_when_somebody_is_seated():
    gate = Gate(platform="gamecube", state=READYING, session=True,
                seats=(Seat(player=1, name="pad", configured=True),))
    gate, command = ready_from_the_keyboard(gate)
    assert command == {"cmd": "accept"}
    assert gate.awaiting == "accept"
    gate = Gate(platform="gamecube", state=SEATING, session=True)
    assert ready_from_the_keyboard(gate) == (gate, None)


def test_a_pad_mapped_for_another_console_is_not_asked_about_again():
    """Bound once is bound for everything.

    padmap falls back to its universal capture when a console has none of its
    own, so a pad mapped on GameCube already works on an N64 game. Asking again
    on each new platform was a wizard in front of a controller that was fine.
    """
    gate = Gate(platform="n64", unseated=True)
    gate = apply(gate, state_event([seated(mappings=["console:gamecube"])]))
    gate, command = decide(gate)
    assert gate.state == READY
    assert command is None


def test_a_pad_that_bound_itself_is_left_alone():
    # padmap reads the kernel's BTN_ codes, so a standard controller arrives
    # correctly bound and has no capture of its own to show for it. That is a
    # working pad, and the gate must not open a wizard in front of it.
    gate = Gate(platform="gamecube", unseated=True)
    gate = apply(gate, state_event([seated(mappings=[], configured=True)]))
    gate, command = decide(gate)
    assert gate.state == READY
    assert command is None


def test_a_pad_with_nothing_at_all_is_still_asked_about():
    # The case the gate exists for: seated, and padmap does not know where its
    # buttons are.
    gate = Gate(platform="gamecube")
    gate = apply(gate, state_event([seated(mappings=[], configured=False)], state="assigning"))
    gate, command = decide(gate)
    assert gate.state == MAPPING
    assert command == {"cmd": "map", "player": 1, "layout": "gamecube", "scope": "console:gamecube"}


def test_the_first_unmapped_pad_is_the_one_asked_about():
    players = [
        seated(player=1, mappings=["console:snes"]),
        seated(player=2, mappings=[], configured=False),
    ]
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
    assert command is None, "the gate started the game on nobody's say-so"
    assert gate.state == READYING

    # The ready-up: padmap's confirm, a longer hold on the seated pad, which
    # the daemon accepts itself when it completes.
    gate = apply(gate, {"event": "confirm", "frac": 0.5})
    assert gate.confirm == 0.5
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
    # With the mapping on the pad there is nothing left to ask -- and nothing
    # sent: the session closes when somebody holds a button to say go.
    gate = apply(gate, state_event([seated(mappings=["console:gamecube"])], state="assigning"))
    gate, command = decide(gate)
    assert command is None
    assert gate.state == READYING


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


# --- the window when there is nothing to ask ---------------------------------


def test_the_window_without_padmap_says_why_and_how_long():
    # Under Steam a line on stderr is a line in a log nobody reads. The
    # window is what somebody sees, and it counts down so nobody is stuck.
    from gotg_ui.gate import without_controllers

    said, footer = without_controllers("padmap is not installed", 7.2)
    assert said == "padmap is not installed"
    assert "8 s" in footer and "Enter" in footer
    said, footer = without_controllers("", 0.0)
    assert said == "padmap is not running"
    assert "0 s" in footer


def test_a_hold_in_flight_reaches_the_seating_screen_and_a_claim_ends_it():
    gate = Gate(platform="gamecube", state=SEATING, session=True)
    gate = apply(gate, {"event": "progress", "frac": 0.6})
    assert gate.progress == 0.6
    gate = apply(gate, {"event": "claim", "player": 1, "name": "pad"})
    assert gate.progress == 0.0


# --- the second that starts the game ------------------------------------------


def test_a_button_already_down_when_the_door_opens_does_not_count():
    # SDL reports it pressed the instant the clone is opened. That is the
    # hold that finished the wizard, still going.
    hold = GoHold(seconds=1.0, opened=100.0)
    hold.pressed(100.02)
    assert hold.progress(101.5) == 0.0
    assert not hold.done(101.5)


def test_a_button_the_pad_says_is_down_at_open_counts_for_nothing_until_it_comes_up():
    # padmap forwards a Steam Controller's state, so its clone shows A down
    # from its first frame, and SDL may report that press late or never.
    # The pad was asked; the answer outranks the quiet.
    hold = GoHold(seconds=1.0, opened=100.0, held_at_open=True)
    hold.pressed(101.0)
    assert not hold.done(103.0)
    hold.released(103.1)
    hold.pressed(103.2)
    assert hold.done(104.2)


def test_letting_go_arms_the_door_and_the_next_press_is_the_one():
    hold = GoHold(seconds=1.0, opened=100.0)
    hold.pressed(100.02)
    hold.released(100.8)
    hold.pressed(101.0)
    assert abs(hold.progress(101.5) - 0.5) < 1e-9
    assert hold.done(102.0)


def test_nothing_held_at_open_means_the_first_press_counts():
    hold = GoHold(seconds=1.0, opened=100.0)
    hold.pressed(100.6)
    assert hold.done(101.6)


def test_a_press_let_go_early_starts_over():
    hold = GoHold(seconds=1.0, opened=100.0)
    hold.pressed(100.6)
    hold.released(101.0)
    assert hold.progress(101.5) == 0.0
    hold.pressed(101.2)
    assert not hold.done(102.1)
    assert hold.done(102.2)
