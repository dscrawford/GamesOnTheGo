"""Taking a seat, as a sequence of events.

padmap holds the pads for the length of a session, so nothing here is driven
by a button press this program can see: every change on screen arrives as an
event from the daemon. These pin what a given sequence leaves on screen.
"""

from __future__ import annotations

from gotg_ui.assign import Assignment, KeyHold, Session, Watch, apply, attend
from gotg_ui.gate import PAIR_HOLD


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


# --- keeping padmap listening, while the picker is up -----------------------


def test_it_asks_padmap_to_listen_as_soon_as_there_is_a_connection():
    watch = Watch()
    assert watch.wanted(True, "idle", 0) == {"cmd": "seating", "open": True, "players": 4, "hold": PAIR_HOLD}


def test_it_does_not_ask_again_for_the_same_state():
    # padmap does not acknowledge `seating`, so the only thing stopping this
    # from being a syscall a frame is not sending it twice for one state.
    watch = Watch()
    watch.wanted(True, "idle", 0)
    assert watch.wanted(True, "idle", 0) is None


def test_a_seat_taken_is_worth_asking_again():
    # Three seats are still free, and the pad that took the first one is not
    # the only one somebody might pick up.
    watch = Watch()
    watch.wanted(True, "idle", 0)
    assert watch.wanted(True, "ready", 1) is not None


def test_nothing_is_asked_while_a_session_is_open():
    # padmap suspends seating inside a session, and the assignment screen is
    # already asking for the same holds.
    assert Watch().wanted(True, "assigning", 0) is None


def test_nothing_is_asked_when_every_seat_is_taken():
    assert Watch().wanted(True, "ready", 4) is None


def test_nothing_is_asked_with_no_daemon_to_ask():
    assert Watch().wanted(False, "offline", 0) is None


def test_a_reconnected_daemon_is_asked_again():
    # A daemon that was restarted remembers nothing, so the picker has to say
    # it again -- and the state it comes back in is the one it went away in.
    watch = Watch()
    watch.wanted(True, "idle", 0)
    watch.wanted(False, "offline", 0)
    assert watch.wanted(True, "idle", 0) is not None


def test_there_is_no_way_to_close_it():
    # A second player usually turns up in the middle of a game, and padmap
    # keeps listening after the picker is gone. Nothing here may tell it to
    # stop.
    assert not hasattr(Watch(), "closed")


def test_a_daemon_too_old_to_listen_is_not_asked_twice():
    watch = Watch()
    watch.wanted(True, "idle", 0)
    watch.handle({"event": "error", "message": 'unknown command "seating"'})
    assert watch.wanted(True, "ready", 1) is None


def test_somebody_elses_error_is_not_this_one():
    # Other errors mention seating too -- "seating is suspended while a
    # session is open" -- and none of them mean the daemon cannot do it.
    watch = Watch()
    watch.handle({"event": "error", "message": "no joypads found"})
    watch.handle({"event": "error", "message": "seating is suspended while a session is open"})
    assert not watch.refused


def test_a_seat_freed_after_the_last_one_filled_is_asked_about_again():
    # Four seated is nothing to ask for, and the fourth unplugging puts the
    # state back to a tuple that was already sent. Comparing alone would then
    # never mention the seat they freed.
    watch = Watch()
    for seated in range(4):
        watch.wanted(True, "idle", seated)
    assert watch.wanted(True, "idle", 4) is None
    assert watch.wanted(True, "idle", 3) is not None


def test_a_session_that_opens_and_closes_is_asked_about_again():
    # padmap suspends seating for the length of a session, so the picker says
    # it again on the way out rather than assuming what it resumed.
    watch = Watch()
    watch.wanted(True, "idle", 0)
    watch.wanted(True, "assigning", 0)
    assert watch.wanted(True, "idle", 0) is not None


# --- the way out that does not cost you the controller ----------------------


def test_leaving_with_a_seat_claimed_keeps_it():
    # B used to cancel, and cancelling is padmap discarding every claim --
    # which, with the picker driven by published pads alone, discards the only
    # thing that could have pressed B a second time.
    session = Session()
    session.begin()
    session.handle({"event": "claim", "player": 1, "name": "Xbox Wireless Controller"})
    assert session.leave() == {"cmd": "accept"}
    assert not session.open


def test_leaving_with_nothing_claimed_cancels():
    session = Session()
    session.begin()
    assert session.leave() == {"cmd": "cancel"}
    assert not session.open


def test_leaving_twice_does_not_accept_an_empty_session():
    session = Session()
    session.begin()
    session.handle({"event": "claim", "player": 1, "name": "Pad"})
    session.leave()
    assert session.leave() == {"cmd": "cancel"}


def test_a_seated_pad_is_told_it_can_hold_to_start():
    # The one way out of this screen that a pad can reach: padmap takes a
    # longer hold on an already-claimed pad as "accept", and nothing said so.
    view = Assignment(state="assigning", pads=1)
    assert not view.keep_hint
    view = apply(view, {"event": "claim", "player": 1, "name": "Pad"})
    assert "hold" in view.keep_hint


def test_a_session_the_picker_did_not_start_still_opens_the_screen():
    # padmap opens one by itself for a pad it has never seen mapped. For the
    # length of it every pad is grabbed, so the picker answers no button --
    # and the grid sat there looking broken with nothing saying why.
    session = Session()
    assert not session.open
    session.handle({"event": "state", "state": "assigning", "slots": 4, "players": []})
    assert session.open


def test_and_it_closes_again_when_the_session_does():
    session = Session()
    session.handle({"event": "state", "state": "assigning", "slots": 4, "players": []})
    session.handle({"event": "state", "state": "ready", "slots": 4, "players": []})
    assert not session.open


# --- the whole rule, once a frame -------------------------------------------


class FakeDaemon:
    """padmap as the picker sees it: a queue of events and three readings."""

    def __init__(self, events=(), connected=True, state="idle", players=()):
        self.events = list(events)
        self.connected = connected
        self.status_word = state if connected else "offline"
        self.players = list(players)

    def poll(self):
        out, self.events = self.events, []
        return iter(out)


def test_padmap_is_told_to_listen_for_a_hold_without_being_asked():
    # The requirement: controllers pair from wherever the picker is, so the
    # command goes out on its own rather than waiting for a screen.
    command = attend(FakeDaemon(), Session(), Watch())
    assert command == {"cmd": "seating", "open": True, "players": 4, "hold": PAIR_HOLD}


def test_the_picker_never_opens_a_session_by_itself():
    # `begin` grabs every pad and takes the screen. Nothing on the grid should
    # ever send it -- that is the pairing screen nobody wants to visit.
    watch, session, daemon = Watch(), Session(), FakeDaemon()
    for state in ("idle", "ready", "idle"):
        daemon.status_word = state
        command = attend(daemon, session, watch)
        assert command is None or command["cmd"] == "seating"


def test_a_claim_arriving_is_a_seat_on_the_strip():
    daemon = FakeDaemon(events=[{"event": "claim", "player": 1, "name": "Xbox Wireless Controller"}])
    session = Session()
    attend(daemon, session, Watch())
    assert [s.player for s in session.view.seats] == [1]


def test_a_hold_in_flight_reaches_the_ring():
    daemon = FakeDaemon(events=[{"event": "progress", "frac": 0.5}])
    session = Session()
    attend(daemon, session, Watch())
    assert session.view.progress == 0.5


def test_a_daemon_too_old_to_listen_is_not_asked_again_and_hands_nothing_back():
    # It used to hand the raw pads back. It does not: a padmap that cannot
    # seat anybody is a picker the keyboard drives until padmap is fixed.
    daemon = FakeDaemon(events=[{"event": "error", "message": 'unknown command "seating"'}])
    watch = Watch()
    assert attend(daemon, Session(), watch) is None
    assert watch.refused
    assert attend(FakeDaemon(state="ready", players=[{"player": 1}]), Session(), watch) is None


# --- the space bar, held ------------------------------------------------------


def test_a_tap_on_space_is_still_the_menu():
    hold = KeyHold(seconds=0.6)
    hold.down(10.0)
    assert hold.due(10.2) is None
    assert hold.up() is True


def test_space_held_the_whole_way_seats_the_keyboard_once():
    hold = KeyHold(seconds=0.6)
    hold.down(10.0)
    assert hold.due(10.3) is None
    assert hold.due(10.6) == {"cmd": "seat_keyboard"}
    assert hold.due(10.9) is None, "said twice for one hold"
    assert hold.up() is False, "the release after a seat is not a tap"


def test_progress_fills_over_the_hold_and_is_nothing_when_nothing_is_held():
    hold = KeyHold(seconds=0.6)
    assert hold.progress(5.0) == 0.0
    hold.down(10.0)
    assert abs(hold.progress(10.3) - 0.5) < 1e-9
    assert hold.progress(11.0) == 1.0
    hold.up()
    assert hold.progress(11.0) == 0.0


def test_key_repeat_does_not_restart_the_hold():
    # A second KEYDOWN while the key is down is the repeat, not a new press.
    hold = KeyHold(seconds=0.6)
    hold.down(10.0)
    hold.down(10.5)
    assert hold.due(10.6) == {"cmd": "seat_keyboard"}
