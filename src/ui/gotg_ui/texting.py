"""One line of typed text: the search box and the storage path.

Pulled out of `run()`, which edited a string with the same four-way chain
twice -- Escape, Return or keypad Enter, Backspace, a printable character --
once for the search (applied to the grid as it is typed) and once for the path
to add to storage (applied on Return). What differs is what the caller does
with the result, so this returns the text and which of the four happened.
No pygame: the keycodes are intents.Key's, asserted against pygame in app.py.
"""

from __future__ import annotations

from .intents import Key

CANCEL = "cancel"
COMMIT = "commit"
EDITED = "edited"
IGNORED = "ignored"


def edit(text: str, key: int, char: str) -> tuple[str, str]:
    """The text after one KEYDOWN, and what that key was. `char` is the
    event's unicode; a key with none, or one that is not printable (Tab), is
    ignored."""
    if key == Key.ESCAPE:
        return text, CANCEL
    if key in (Key.RETURN, Key.KP_ENTER):
        return text, COMMIT
    if key == Key.BACKSPACE:
        return text[:-1], EDITED
    if char and char.isprintable():
        return text + char, EDITED
    return text, IGNORED
