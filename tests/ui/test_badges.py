"""Property under test: what the worker learns about the disk and the client's
report becomes a badge set and at most one `--check` start, and a worker that
fails costs the grid nothing."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from gotg_ui.badges import Asker, Badges, land
from gotg_ui.updates import GameUpdate, Report


def report(*keys: tuple[str, str]) -> Report:
    return Report(
        available=True,
        behind=True,
        unbuilt=False,
        writable=True,
        stale=False,
        pending=False,
        picker=None,
        picker_current=False,
        games={k: GameUpdate(k, (), ("build",)) for k in keys},
    )


def test_a_landing_names_what_is_here_and_what_of_it_is_behind():
    said = report(("n64", "zelda"), ("n64", "gone"))
    landed = land(Badges(), {("n64", "zelda"), ("snes", "mario")}, said)
    assert landed.installed == {("n64", "zelda"), ("snes", "mario")}
    assert landed.outdated == {("n64", "zelda")}
    assert landed.badges.report is said


def test_the_check_starts_once_and_only_after_the_client_has_answered():
    first = land(Badges(), set(), report())
    assert first.start_check and first.badges.check_started
    again = land(first.badges, set(), report())
    assert not again.start_check and again.badges.check_started


def test_a_client_that_said_nothing_starts_no_check_and_leaves_the_door_open():
    landed = land(Badges(), {("a", "b")}, None)
    assert not landed.start_check and not landed.badges.check_started
    assert landed.outdated == frozenset()
    assert land(landed.badges, set(), report()).start_check


def test_the_asker_hands_over_an_answer_once():
    with ThreadPoolExecutor(1) as pool:
        asker = Asker(lambda: ({("a", "b")}, None), pool)
        assert asker.poll() is None  # nothing asked yet
        asker.refresh()
        pool.submit(lambda: None).result()  # the one worker is free again: the ask is done
        assert asker.poll() == ({("a", "b")}, None)
        assert asker.poll() is None


def test_the_asker_swallows_a_worker_that_failed():
    def broken():
        raise RuntimeError("no disk")

    with ThreadPoolExecutor(1) as pool:
        asker = Asker(broken, pool)
        asker.refresh()
        pool.submit(lambda: None).result()
        assert asker.poll() is None
        assert asker.poll() is None


def test_a_second_ask_replaces_the_first():
    answers = iter([1, 2])
    with ThreadPoolExecutor(1) as pool:
        asker = Asker(lambda: next(answers), pool)
        asker.refresh()
        asker.refresh()
        pool.submit(lambda: None).result()
        assert asker.poll() == 2
