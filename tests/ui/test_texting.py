"""Property under test: a line being typed (the search, a storage path) takes
Escape, Return, Backspace and printable characters, and nothing else."""

from __future__ import annotations

from gotg_ui.intents import Key
from gotg_ui.texting import CANCEL, COMMIT, EDITED, IGNORED, edit


def test_a_printable_character_is_appended():
    assert edit("ab", ord("c"), "c") == ("abc", EDITED)


def test_backspace_drops_one_and_is_still_an_edit_when_empty():
    assert edit("ab", Key.BACKSPACE, "\x08") == ("a", EDITED)
    assert edit("", Key.BACKSPACE, "") == ("", EDITED)


def test_escape_cancels_and_return_or_keypad_enter_commit_without_touching_the_text():
    assert edit("ab", Key.ESCAPE, "\x1b") == ("ab", CANCEL)
    assert edit("ab", Key.RETURN, "\r") == ("ab", COMMIT)
    assert edit("ab", Key.KP_ENTER, "\r") == ("ab", COMMIT)


def test_a_key_with_no_text_is_ignored():
    assert edit("ab", Key.UP, "") == ("ab", IGNORED)
    assert edit("ab", 9, "\t") == ("ab", IGNORED)  # a tab is not printable
