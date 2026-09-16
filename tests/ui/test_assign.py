"""Taking a seat, as a sequence of events.

padmap holds the pads for the length of a session, so nothing here is driven
by a button press this program can see: every change on screen arrives as an
event from the daemon. These pin what a given sequence leaves on screen.
"""

from __future__ import annotations

from gotg_ui.assign import Assignment, Session, apply


def test_nothing_yet_says_how_to_start():
    assert "press A" in Assignment().prompt


def test_it_asks_for_the_next_free_seat():
    view = Assignment(state="assigning", pads=2)
    assert "player 1" in view.prompt
    view = apply(view, {"event": "claim", "player": 1, "name": "N64 Adapter"})
    assert "player 2" in view.prompt


def test_a_claim_seats_a_player():
    view = apply(Assignment(state="assigning"), {"event": "claim", "player": 2, "name": "Pad"})
    assert [s.player for s in view.seats] == [2]
    assert view.seats[0].name == "Pad"


def test_seats_are_shown_in_player_order_whatever_order_they_arrive():
    view = Assignment(state="assigning")
    for player in (3, 1, 2):
        view = apply(view, {"event": "claim", "player": player})
    assert [s.player for s in view.seats] == [1, 2, 3]


def test_claiming_the_same_seat_twice_does_not_double_it():
    view = Assignment(state="assigning")
    view = apply(view, {"event": "claim", "player": 1, "name": "first"})
    view = apply(view, {"event": "claim", "player": 1, "name": "second"})
    assert [s.player for s in view.seats] == [1]
    assert view.seats[0].name == "second"


def test_a_hold_in_flight_is_progress_and_a_claim_clears_it():
    view = apply(Assignment(state="assigning"), {"event": "progress", "frac": 0.6})
    assert view.progress == 0.6
    view = apply(view, {"event": "claim", "player": 1})
    assert view.progress == 0.0


def test_no_controllers_is_said_rather_than_waited_for():
    view = Assignment(state="assigning", pads=0)
    assert "plug one in" in view.prompt


def test_a_full_house_asks_to_keep_it():
    view = Assignment(state="assigning", slots=2, pads=2)
    view = apply(view, {"event": "claim", "player": 1})
    view = apply(view, {"event": "claim", "player": 2})
    assert view.waiting_for is None
    assert "keep it" in view.prompt


def test_accepting_finishes():
    view = apply(Assignment(state="assigning"), {"event": "accepted", "players": []})
    assert view.finished
    assert view.prompt == "controllers assigned"


def test_an_error_is_shown_rather_than_swallowed():
    view = apply(Assignment(), {"event": "error", "message": "no permission for uinput"})
    assert "uinput" in view.message


def test_an_event_nobody_knows_changes_nothing():
    # The daemon is the authority and is free to grow events; a front-end that
    # rejected them would break on every padmap release.
    before = Assignment(state="assigning", pads=2)
    assert apply(before, {"event": "something-new", "x": 1}) == before


def test_a_state_event_rebuilds_the_seats():
    view = apply(
        Assignment(),
        {"event": "state", "state": "ready", "slots": 2,
         "players": [{"player": 2, "name": "two"}, {"player": 1, "name": "one"}]},
    )
    assert [s.player for s in view.seats] == [1, 2]
    assert view.slots == 2
    assert view.state == "ready"


def test_the_session_sends_padmap_s_words():
    session = Session(slots=3)
    assert session.begin() == {"cmd": "begin", "players": 3}
    assert session.open
    assert session.accept() == {"cmd": "accept"}
    assert session.cancel() == {"cmd": "cancel"}
    assert not session.open


def test_the_session_closes_itself_when_padmap_accepts():
    session = Session()
    session.begin()
    session.handle({"event": "accepted", "players": []})
    assert not session.open
    assert session.view.finished
