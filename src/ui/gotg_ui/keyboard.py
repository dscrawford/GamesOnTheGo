"""An on-screen keyboard: the search typed with a d-pad.

The search box used to take only key events, and a pad has none: the first
press of X left a controller with no way to type and no way out. This is the
keyboard a pad drives -- a cursor over the keys, A to type the one under it --
and the way out is the key at the bottom right, which brings it down. B does
the same, so one press of the back button is still one step back.

Model only, and pygame-free: where the keys are drawn is draw_keyboard's.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

SPACE = "space"
BACKSPACE = "backspace"
CLEAR = "clear"
CLOSE = "close"

# The way out is the last key: a thumb on the corner finds it without looking.
ROWS: tuple[tuple[str, ...], ...] = (
    tuple("1234567890"),
    tuple("qwertyuiop"),
    tuple("asdfghjkl-"),
    tuple("zxcvbnm.'&"),
    (SPACE, BACKSPACE, CLEAR, CLOSE),
)

LABELS = {SPACE: "Space", BACKSPACE: "Delete", CLEAR: "Clear", CLOSE: "Close"}


@dataclass(frozen=True)
class Keyboard:
    """The text so far and which key the cursor is on."""

    text: str = ""
    row: int = 1  # the letters, not the digits: most titles start with one
    col: int = 0
    rows: tuple[tuple[str, ...], ...] = field(default=ROWS)

    @property
    def key(self) -> str:
        return self.rows[self.row][self.col]

    @property
    def closes(self) -> bool:
        """Whether the key under the cursor is the one that brings it down."""
        return self.key == CLOSE

    def move(self, dx: int, dy: int) -> Keyboard:
        """A step, wrapping both ways. Between rows of different lengths the
        cursor keeps its place along the row rather than its index, so down
        from the right-hand letters lands on Close rather than Space."""
        row = (self.row + dy) % len(self.rows)
        width, was = len(self.rows[row]), len(self.rows[self.row])
        col = self.col * width // was if width != was else self.col
        col = (col + dx) % width
        return replace(self, row=row, col=col)

    def at(self, row: int, col: int) -> Keyboard:
        """The cursor put on one key: a pointer over it."""
        if not (0 <= row < len(self.rows) and 0 <= col < len(self.rows[row])):
            return self
        return replace(self, row=row, col=col)

    def press(self) -> Keyboard:
        """A on the key under the cursor. Close is the caller's to notice
        (`closes`): the text is not changed by it."""
        key = self.key
        if key == SPACE:
            return self.typed(" ")
        if key == BACKSPACE:
            return self.backspace()
        if key == CLEAR:
            return replace(self, text="")
        if key == CLOSE:
            return self
        return self.typed(key)

    def typed(self, char: str) -> Keyboard:
        return replace(self, text=self.text + char)

    def backspace(self) -> Keyboard:
        return replace(self, text=self.text[:-1])

    def with_text(self, text: str) -> Keyboard:
        """The text replaced from outside: a real keyboard typing into it."""
        return replace(self, text=text)
