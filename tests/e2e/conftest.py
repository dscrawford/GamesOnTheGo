"""What the controller tests need: a daemon of their own, and SDL.

Every one of these runs against a real padmap daemon and real kernel devices,
because every bug this suite exists for lived in the gap between what the code
believed about SDL and what SDL does. The daemon is a private one -- its own
runtime, config, data and state directories under tmp_path -- so a test never
touches the padmap somebody is playing with, and never inherits its
assignments.

The tests skip when the machine cannot run them, and `GOTG_E2E_REQUIRE=1`
turns those skips into failures. A test that quietly does not run is worse
than no test, so anything that means to enforce this -- CI, a run on the Deck,
a release check -- sets that.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(__file__))

import fakepad  # noqa: E402


def _cannot(why: str) -> None:
    """Skip, unless this run was meant to be the one that proves it."""
    if os.environ.get("GOTG_E2E_REQUIRE") == "1":
        pytest.fail(f"controller e2e cannot run here, and GOTG_E2E_REQUIRE=1: {why}")
    pytest.skip(why)


def _somebody_is_playing() -> str:
    """The real daemon's socket, if one is up -- meaning a person is at a
    picker or a game on this machine right now.

    The fake pads these tests make are real devices, and a real daemon in
    seating mode claims them: one evening it seated "E2E Xbox Pad" as player
    two in the user's own game, mid-level, while the test waited for a claim
    that had gone to the wrong daemon. So the suite does not run beside a
    real one. GOTG_E2E_BESIDE_A_PLAYER=1 overrides, for whoever means it.
    """
    if os.environ.get("GOTG_E2E_BESIDE_A_PLAYER") == "1":
        return ""
    real = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "padmap", "padmap.sock")
    if os.path.exists(real):
        return f"a padmap daemon is up at {real}: somebody is playing, and the fake pads would join their game"
    return ""


@pytest.fixture
def daemon(tmp_path):
    """A padmap of this test's own, and a client connected to it."""
    if shutil.which("padmap") is None:
        _cannot("padmap is not on PATH")
    playing = _somebody_is_playing()
    if playing:
        pytest.fail(playing)
    trouble = fakepad.available()
    if trouble:
        _cannot(trouble)

    home = {
        "XDG_RUNTIME_DIR": str(tmp_path / "run"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        # The two things that would make the test about padmap's own opinions
        # rather than the picker's: a session opened for an unmapped pad, and a
        # seat handed out from a stored mapping. Both off, so every seat in
        # here was claimed by a hold.
        "PADMAP_NO_AUTOSETUP": "1",
        "PADMAP_NO_AUTOATTACH": "1",
    }
    for key, value in home.items():
        if key.startswith("XDG"):
            os.makedirs(value, exist_ok=True)
    env = {**os.environ, **home}

    started = subprocess.run(
        ["padmap", "ensure-daemon"], env=env, capture_output=True, text=True, timeout=60
    )
    if started.returncode != 0:
        _cannot(f"padmap would not start: {(started.stderr or started.stdout).strip()[-200:]}")

    path = os.path.join(home["XDG_RUNTIME_DIR"], "padmap", "padmap.sock")
    for _ in range(80):
        if os.path.exists(path):
            break
        time.sleep(0.25)
    else:
        _cannot("padmap started but never bound its socket")

    client = Daemon(path)
    client.env = home
    try:
        yield client
    finally:
        client.close()
        if client.pid:
            try:
                os.kill(client.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        # And any daemon a test started under this runtime dir since -- the
        # Steam route replaces the fixture's with one following a gate that
        # the test then kills, and the follower needs a moment to notice. A
        # daemon left behind holds the machine's real pads; three of them,
        # from one evening's experiments, were grabbing the user's
        # controllers during his own launch. By runtime dir, never by name:
        # matching argv has killed a user's real daemon from a test before.
        _reap(home["XDG_RUNTIME_DIR"])


def _reap(runtime_dir: str) -> None:
    """SIGTERM every padmap-rs whose XDG_RUNTIME_DIR is this test's."""
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/comm") as comm:
                if "padmap-rs" not in comm.read():
                    continue
            with open(f"/proc/{pid}/environ", "rb") as environ:
                env = environ.read().split(b"\0")
        except OSError:
            continue
        if f"XDG_RUNTIME_DIR={runtime_dir}".encode() in env:
            try:
                os.kill(int(pid), signal.SIGTERM)
            except ProcessLookupError:
                pass


class Daemon:
    """The socket half, with the waiting a test needs on top.

    Deliberately not gotg_ui.padmap.Padmap: that is one of the things under
    test, and a test that drove it could not tell a broken client from a
    broken daemon.
    """

    def __init__(self, path: str):
        self.path = path
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(path)
        self.sock.setblocking(False)
        self.buffer = b""
        self.seen: list[dict] = []
        self.pid: int | None = None
        self.env: dict[str, str] = {}
        self.send({"cmd": "status"})
        self.drain(1.0)

    def send(self, message: dict) -> None:
        self.sock.sendall(json.dumps(message).encode() + b"\n")

    def drain(self, seconds: float) -> list[dict]:
        """Everything that arrives in this long. Never raises on quiet."""
        got, end = [], time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                chunk = self.sock.recv(65536)
            except BlockingIOError:
                time.sleep(0.02)
                continue
            if not chunk:
                break
            self.buffer += chunk
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict):
                    if event.get("event") == "state" and isinstance(event.get("pid"), int):
                        self.pid = event["pid"]
                    got.append(event)
                    self.seen.append(event)
        return got

    def wait_for(self, kind: str, seconds: float = 6.0) -> dict | None:
        """The next event of this kind, or None if it never comes."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            for event in self.drain(0.2):
                if event.get("event") == kind:
                    return event
        return None

    @property
    def states(self) -> list[str]:
        return [e.get("state") for e in self.seen if e.get("event") == "state"]

    @property
    def players(self) -> list[dict]:
        for event in reversed(self.seen):
            if event.get("event") == "state":
                return event.get("players") or []
        return []

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


@pytest.fixture
def sdl():
    """pygame, headless, and the picker's own pads module wired to it.

    Module-level bookkeeping is cleared between tests: `pads` holds what it has
    opened in globals, the way the picker does, and a second test inheriting
    the first one's devices would pass for the wrong reason.
    """
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    try:
        import pygame
    except ImportError:
        _cannot("no pygame here -- run this through `nix run .#test-controllers`")

    from gotg_ui import pads

    pygame.init()
    pygame.display.set_mode((64, 64))
    pads._owners.names.clear()
    pads._owners.guids.clear()
    pads._mapped.clear()
    try:
        yield pygame
    finally:
        pygame.quit()
