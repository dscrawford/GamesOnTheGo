"""What Steam leaves in the picker's environment, taken back out.

On a Deck in Game Mode a Steam Controller paired -- danstick seated it, the
picker drew the seat -- and then nothing it pressed moved anything. Steam
hands everything it launches SDL_GAMECONTROLLER_IGNORE_DEVICES naming the
controllers Steam Input handles, and danstick's clone of a Steam Controller
wears the Steam Controller's own ids, so the picker's SDL ignored the one
device it was meant to listen to.
"""

from __future__ import annotations

from gotg_ui import steam_leftovers


def test_steam_s_ignore_lists_are_taken_out():
    environ = {
        "SDL_GAMECONTROLLER_IGNORE_DEVICES": "0x28de/0x1304,0x28de/0x1205",
        "SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT": "0x28de/0x11ff",
        "HOME": "/home/deck",
    }
    assert steam_leftovers(environ) == (
        "SDL_GAMECONTROLLER_IGNORE_DEVICES",
        "SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT",
    )


def test_nothing_is_taken_where_steam_left_nothing():
    assert steam_leftovers({"HOME": "/home/deck"}) == ()


def test_the_picker_started_by_steam_sees_danstick_s_clones(monkeypatch):
    # The import is what runs before SDL starts, so it is the import that has
    # to have cleared them.
    import importlib

    import gotg_ui

    monkeypatch.setenv("SDL_GAMECONTROLLER_IGNORE_DEVICES", "0x28de/0x1304")
    importlib.reload(gotg_ui)
    import os

    assert "SDL_GAMECONTROLLER_IGNORE_DEVICES" not in os.environ
