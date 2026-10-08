"""Property under test: the chip says the report's words, except for a while
after a press, when it says the loop's own -- and a click lands only on where
it was last drawn."""

from __future__ import annotations

from types import SimpleNamespace

from gotg_ui import updates
from gotg_ui.chip import ChipState
from gotg_ui.updates import Report


def report(**kw) -> Report:
    base = dict(
        available=True, behind=True, unbuilt=False, writable=True, stale=False, pending=False,
        picker=None, picker_current=False, games={},
    )
    base.update(kw)
    return Report(**base)


def test_nothing_is_said_before_anything_is_pressed():
    chip = ChipState()
    assert chip.words(None, None, 100.0) is None
    assert chip.words(report(), None, 100.0) == updates.UPDATE_AVAILABLE


def test_a_press_word_wins_for_its_seconds_and_then_gives_way():
    chip = ChipState().say("failed", 100.0)
    assert chip.words(report(), None, 105.0) == updates.UPDATE_FAILED
    assert chip.words(report(), None, 110.0) == updates.UPDATE_AVAILABLE  # exactly at `until`: over


def test_each_phase_lasts_as_long_as_the_loop_had_it_last():
    assert ChipState().say("busy", 0.0).until == 6.0
    assert ChipState().say("updating", 0.0).until == 3600.0
    for phase in ("up-to-date", "updated-here", "failed"):
        assert ChipState().say(phase, 0.0).until == 10.0
    assert ChipState().say("busy", 0.0, seconds=2.0).until == 2.0


def test_saying_leaves_the_old_state_alone_and_keeps_the_rect():
    first = ChipState(rect="r")
    second = first.say("busy", 1.0)
    assert first.phase is None and second.phase == "busy" and second.rect == "r"


def test_an_update_in_flight_is_only_one_while_its_word_lasts():
    chip = ChipState().say("updating", 0.0)
    assert chip.updating(10.0)
    assert not chip.updating(3600.0)
    assert not ChipState().say("failed", 0.0).updating(1.0)


def test_a_click_lands_on_the_drawn_chip_and_nowhere_else():
    assert not ChipState().hit((5, 5))
    chip = ChipState().drawn(SimpleNamespace(x=100, y=10, width=50, height=20))
    assert chip.hit((100, 10)) and chip.hit((149, 29))
    assert not chip.hit((150, 10)) and not chip.hit((99, 10)) and not chip.hit((100, 30))
    assert not chip.drawn(None).hit((100, 10))
