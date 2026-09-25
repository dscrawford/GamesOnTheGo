"""Whether the keyboard may drive anything.

It used to be the one thing that always worked, underneath the rule that a
pad has to be published first -- and that was the hole the rule exists to
close, because a controller is a keyboard in hardware. These pin the new
answer: danstick seats the keyboard too, and until it has, the only key that
means anything is the one asking for the seat.
"""

from __future__ import annotations

from gotg_ui import keys

SEATED = [{"player": 1, "name": "Keyboard", "icon": "keyboard", "keyboard": True}]
A_PAD = [{"player": 1, "name": "Xbox Wireless Controller", "icon": "xbox"}]


def test_a_keyboard_danstick_has_not_seated_drives_nothing():
    assert not keys.drives([])
    assert not keys.drives(None)
    assert not keys.drives(A_PAD), "a seated pad is not a seated keyboard"


def test_a_keyboard_danstick_has_seated_drives_everything():
    assert keys.drives(SEATED)
    # Whichever half of the daemon's vocabulary arrives: a `claim` names it,
    # a `state` flags it, and a front-end reading one of them reads half.
    assert keys.drives([{"player": 2, "keyboard": True}])
    assert keys.drives([{"player": 2, "name": "keyboard"}])
    assert keys.drives([{"player": 2, "icon": "keyboard"}])


def test_no_daemon_is_not_permission():
    """The pad rule learned this one the hard way.

    A daemon that is missing or down looks exactly like an unassigned
    controller typing into the picker, so "danstick is not here" cannot be the
    thing that lets a keyboard in.
    """
    assert not keys.drives(SEATED, connected=False)
    assert not keys.drives([], connected=False)


def test_the_override_is_the_one_way_out(monkeypatch):
    monkeypatch.setenv("GOTG_ANY_PAD", "1")
    assert keys.drives([], connected=False), "the override has to lift this too"


def test_a_seat_that_is_not_the_keyboards_is_not_the_keyboards():
    assert not keys.seated([{"player": 1, "name": "Keyboard Warrior Pad"}])
    assert not keys.seated([{"player": 1, "icon": "keyboard-mouse"}])
    assert keys.seated([{"player": 1, "name": " Keyboard "}])


def test_the_keyboards_own_seat_is_findable():
    """The door needs the number: everybody seated readies up, and a keyboard
    that could not would hold the room for ever."""
    assert keys.seat_of(SEATED) == 1
    assert keys.seat_of([{"player": 3, "keyboard": True}]) == 3
    assert keys.seat_of(A_PAD) is None
    assert keys.seat_of([]) is None
