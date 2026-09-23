"""Cover art decoded off the frame thread.

Ten new covers decoded in the frame that first drew them was 13.5 ms: a page
scrolling into view hitched. On a worker the frame never waits.
"""

from __future__ import annotations

import threading
import time

from gotg_ui.decode import PENDING, Decoder


def _until(check, seconds: float = 2.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        found = check()
        if found:
            return found
        time.sleep(0.005)
    return None


def test_the_first_ask_never_waits_for_the_picture():
    gate = threading.Event()

    def slow(path):
        gate.wait(2)
        return f"picture of {path}"

    decoder = Decoder(slow)
    started = time.monotonic()
    assert decoder.want("zelda", "zelda.png") is PENDING
    assert time.monotonic() - started < 0.05, "the frame waited for the decode"
    gate.set()
    assert _until(lambda: decoder.arrived()) == ["zelda"]
    assert decoder.want("zelda", "zelda.png") == "picture of zelda.png"


def test_asking_again_while_it_decodes_does_not_decode_it_twice():
    calls = []
    gate = threading.Event()

    def load(path):
        calls.append(path)
        gate.wait(2)
        return path

    decoder = Decoder(load)
    for _ in range(5):
        assert decoder.want("k", "a.png") is PENDING
    gate.set()
    _until(lambda: decoder.arrived())
    assert calls == ["a.png"]


def test_a_file_that_will_not_decode_is_none_not_a_dead_worker():
    def load(path):
        if path == "broken.png":
            raise ValueError("truncated")
        return path

    decoder = Decoder(load)
    decoder.want("bad", "broken.png")
    decoder.want("good", "fine.png")
    _until(lambda: len(decoder._ready) == 2)
    assert decoder.want("bad", "broken.png") is None
    assert decoder.want("good", "fine.png") == "fine.png"


def test_a_picture_is_handed_over_once_and_not_kept_twice():
    decoder = Decoder(lambda path: path)
    decoder.want("k", "a.png")
    _until(lambda: decoder.arrived())
    assert decoder.want("k", "a.png") == "a.png"
    # The caller has it now; asking again is a fresh decode, not a copy kept here.
    assert decoder.want("k", "a.png") is PENDING
