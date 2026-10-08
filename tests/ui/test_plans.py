"""Property under test: what the picker does about a pick, a press on the
chip, a restart and a finished loader is decided here, from values, before
`run()` touches a worker or a process."""

from __future__ import annotations

from types import SimpleNamespace

from gotg_ui import plans, updates
from gotg_ui.chip import ChipState
from gotg_ui.updates import Report

GAME = SimpleNamespace(key=("n64", "zelda"))


def never_asked():
    raise AssertionError("readiness was asked when the verb did not need it")


# plan_pick ---------------------------------------------------------------------------------


def pick(verb="play", variant=None, version=None, installing=False, ready=lambda: True, game=GAME):
    return plans.plan_pick(game, verb, variant, version, installing=installing, ready=ready)


def test_no_game_is_nothing_to_do():
    assert pick(game=None, ready=never_asked).action == plans.IGNORE


def test_saves_and_storage_open_their_screens_without_asking_readiness():
    assert pick("saves", "mod", "v1", ready=never_asked).action == plans.OPEN_SAVES
    assert pick("storage", ready=never_asked).action == plans.OPEN_STORAGE


def test_steam_add_goes_through_the_loader_and_comes_back_to_the_grid():
    p = pick("steam-add", "mod", "v1", ready=never_asked)
    assert (p.action, p.args, p.variant, p.version) == (plans.PREPARE, ("steam", "add"), "mod", "v1")
    assert p.sets_after and p.after is None


def test_uninstall_takes_no_variant_and_wants_its_badges_asked_again():
    p = pick("uninstall", "mod", "v1", ready=never_asked)
    assert (p.action, p.args, p.variant, p.version) == (plans.PREPARE, ("uninstall",), None, None)
    assert p.sets_after and p.after == "uninstall"


def test_installs_run_behind_the_grid_with_the_variant_and_version():
    p = pick("install", "mod", "v1", ready=never_asked)
    assert (p.action, p.variant, p.version) == (plans.INSTALL, "mod", "v1")
    assert not p.sets_after
    assert pick("cancel-install", ready=never_asked).action == plans.CANCEL_INSTALL
    assert pick("update", "mod", "v1", ready=never_asked).action == plans.UPDATE


def test_a_play_on_a_game_already_installing_adopts_that_install():
    p = pick("play", installing=True, ready=never_asked)
    assert p.action == plans.ADOPT and p.sets_after and p.after == "play"


def test_a_ready_game_starts_and_an_unready_one_is_prepared_per_variant():
    p = pick("play", "mod", "v1", ready=lambda: True)
    assert (p.action, p.variant, p.version, p.after, p.sets_after) == (plans.START, "mod", "v1", "play", True)
    p = pick("play", "mod", "v1", ready=lambda: False)
    assert (p.action, p.args, p.variant, p.version, p.after) == (plans.PREPARE, None, "mod", "v1", "play")


def test_installing_only_adopts_for_a_play():
    assert pick("configure", installing=True, ready=lambda: True).action == plans.START


# plan_self_update --------------------------------------------------------------------------


def report(**kw) -> Report:
    base = dict(
        available=True, behind=True, unbuilt=False, writable=True, stale=False, pending=False,
        picker="/nix/store/new-ui", picker_current=True, games={},
    )
    base.update(kw)
    return Report(**base)


def selfup(rep, root="/nix/store/old-ui", chip=None, now=0.0, busy=False):
    return plans.plan_self_update(rep, root, chip=chip or ChipState(), now=now, busy=busy)


def test_nothing_to_update_means_nothing_happens():
    assert selfup(None).action == plans.NOTHING
    assert selfup(report(available=False, picker_current=False)).action == plans.NOTHING
    assert selfup(report(writable=False)).action == plans.NOTHING


def test_a_second_press_while_updating_is_ignored_but_not_after_it_ended():
    running = ChipState().say("updating", 0.0)
    assert selfup(report(), chip=running, now=1.0).action == plans.NOTHING
    assert selfup(report(picker_current=False), chip=running, now=4000.0).action == plans.UPDATING


def test_a_failed_word_is_no_reason_to_refuse_the_second_press():
    failed = ChipState().say("failed", 0.0)
    assert selfup(report(picker_current=False), chip=failed, now=1.0).action == plans.UPDATING


def test_something_else_running_says_so_instead():
    p = selfup(report(), busy=True)
    assert (p.action, p.chip) == (plans.SAY, "busy")


def test_a_newer_picker_already_at_the_root_is_restarted_into():
    p = selfup(report())  # picker_current, picker differs from the running root
    assert (p.action, p.root) == (plans.RESTART, "/nix/store/new-ui")


def test_otherwise_the_client_updates_itself():
    p = selfup(report(picker_current=False))
    assert (p.action, p.chip) == (plans.UPDATING, "updating")
    assert selfup(report(), root=None).action == plans.UPDATING  # a picker from a checkout has no root to restart from


def test_the_chip_words_are_the_reports_not_the_transient_ones():
    assert updates.chip(report(), None) == updates.UPDATE_AVAILABLE


# plan_restart ------------------------------------------------------------------------------


def test_restarting_into_the_root_already_running_is_up_to_date():
    p = plans.plan_restart("/nix/store/a/", "/nix/store/a", lambda r: True)
    assert (p.action, p.chip, p.refresh) == (plans.NOOP, "up-to-date", True)


def test_a_checkout_has_no_root_and_says_so():
    p = plans.plan_restart("/nix/store/a", None, lambda r: True)
    assert (p.action, p.chip, p.refresh) == (plans.NOOP, "updated-here", True)


def test_a_root_that_is_not_a_picker_is_refused_with_the_failed_word():
    p = plans.plan_restart("/tmp/nonsense", "/nix/store/a", lambda r: False)
    assert (p.action, p.chip, p.root) == (plans.REFUSE, "failed", "/tmp/nonsense")


def test_a_usable_new_root_is_gone_to_and_usability_is_not_asked_when_it_need_not_be():
    p = plans.plan_restart("/nix/store/b", "/nix/store/a", lambda r: True)
    assert (p.action, p.root) == (plans.GO, "/nix/store/b")

    def asked(_):
        raise AssertionError("asked")

    assert plans.plan_restart("/nix/store/a", "/nix/store/a", asked).action == plans.NOOP


# on_prepare_done ---------------------------------------------------------------------------


def done(after, ok=True, lines=(), picker=None, restoring=None, starting=None, game=GAME, variant=None, version=None):
    return plans.on_prepare_done(
        after, ok, lines, picker=picker, restoring=restoring, starting=starting,
        game=game, variant=variant, version=version,
    )


def test_a_failed_loader_stays_on_screen_and_a_failed_self_update_says_so():
    p = done("install", ok=False)
    assert p.failed and not p.clear
    assert p.chip is None
    p = done("self-update", ok=False)
    assert p.failed and not p.clear and p.chip == "failed"


def test_steam_add_returns_to_the_grid():
    p = done(None)
    assert p.clear and p.begin is None and not p.refresh


def test_install_and_uninstall_ask_the_badges_again_and_forget_the_versions():
    for after in ("install", "uninstall"):
        p = done(after)
        assert p.clear and p.refresh and p.forget_versions and p.begin is None


def test_a_self_update_restarts_into_what_the_client_said():
    p = done("self-update", lines=["picker\t/nix/store/b"], picker="/nix/store/old")
    assert p.clear and p.clear_after and p.restart_root == "/nix/store/b" and p.chip is None


def test_an_older_client_that_says_nothing_falls_back_to_the_reports_root():
    p = done("self-update", lines=["gotg: ok"], picker="/nix/store/rep")
    assert p.restart_root == "/nix/store/rep"


def test_no_root_at_all_is_already_up_to_date():
    p = done("self-update", lines=[], picker=None)
    assert p.restart_root is None and p.chip == "up-to-date" and p.refresh and p.clear_after


def test_a_restored_save_starts_the_game_it_was_for():
    p = done("restored", restoring=(GAME, "mod", "v1"))
    assert p.begin == (GAME, "play", "mod", "v1") and p.clear_restoring and p.clear


def test_a_kept_save_launches_what_was_waiting():
    waiting = (GAME, "play", None, None)
    p = done("kept", starting=waiting)
    assert p.launch == waiting and p.clear and p.begin is None


def test_restored_or_kept_with_nothing_remembered_falls_through_to_the_verb():
    for after in ("restored", "kept"):
        p = done(after, game=GAME, variant="mod", version="v1")
        assert p.begin == (GAME, after, "mod", "v1") and p.clear


def test_any_other_verb_starts_what_the_loader_prepared():
    p = done("play", variant="mod", version="v1")
    assert p.begin == (GAME, "play", "mod", "v1") and p.clear and not p.clear_restoring


# plan_saves_answer -------------------------------------------------------------------------


def test_a_conflict_is_a_choice_and_anything_else_launches():
    assert plans.plan_saves_answer(SimpleNamespace(conflict=True), True) == plans.CHOOSE
    assert plans.plan_saves_answer(SimpleNamespace(conflict=False), True) == plans.LAUNCH


def test_no_answer_at_all_launches_too():
    assert plans.plan_saves_answer(None, True) == plans.LAUNCH


def test_an_answer_for_a_launch_that_was_cancelled_is_dropped():
    assert plans.plan_saves_answer(SimpleNamespace(conflict=True), False) == plans.DROP
    assert plans.plan_saves_answer(None, False) == plans.DROP
