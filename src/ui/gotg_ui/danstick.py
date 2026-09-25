"""Talking to danstick, which is what knows about controllers.

danstick holds the pads. It grabs them for the length of an assignment session,
asks the person holding them to press a button in the order they want to sit,
republishes each one through uinput under an identity it made, and writes the
emulator configuration that binds player N to it. None of that is decided here:
this module is a socket, a queue of events, and the commands the picker sends.

Newline-delimited JSON over a unix socket, documented in danstick's
`src/danstick/protocol.py`. The daemon is authoritative and holds no expectation
about who is listening, which is the property the picker needs: a front-end
that starts late, or is restarted mid-session, picks up from the next `state`
event rather than from anything it remembered.

Non-blocking throughout, because the caller is a 60Hz draw loop. `poll()`
returns whatever has arrived and never waits, and a daemon that is not running
is not an error -- the screen says so and keeps drawing.
"""

from __future__ import annotations

import json
import os
import select
import shutil
import socket
import subprocess
from collections.abc import Iterator
from pathlib import Path

from . import config, trace

# How long to wait for the connect itself. A unix socket either answers at once
# or is not there; this is only so a stale socket file cannot hang the frame.
CONNECT_TIMEOUT = float(config.get("theme.timeouts.danstick_connect", 0.25))

# Read in chunks this size. Events are tens of bytes; the sdl_mapping one is a
# few hundred per pad.
CHUNK = 65536

# Starting a daemon means spawning a process and waiting for its socket. Long
# enough for a cold start, short enough that a picker opening on a television
# is not staring at nothing.
DAEMON_TIMEOUT = int(config.get("theme.timeouts.danstick_daemon", 10))


def ensure_daemon(force: bool = False, *, fresh: bool = False, follow: int | None = None) -> str | None:
    """Start danstick's daemon, or restart one running older code.

    `fresh` starts it unseated and `follow` ends it when that pid is gone --
    together they make the daemon the session's: nobody is seated when a
    picker or a game opens, and nothing is left running when it closes.
    danstick treats the pair as a session name, so a daemon already following
    this pid is left alone; that is what carries the seats across the
    picker's execvp into the game, and what makes a launch with no picker
    start clean.

    Returns None when there is now a current daemon, and a sentence when there
    is not -- because that is a thing to say on screen, not a thing to stop
    for. A machine where danstick cannot reach uinput still plays games with
    whatever SDL finds by itself, which is what it did before danstick existed.

    DANSTICK_SKIP_DAEMON_CHECK is danstick's own flag for "already asked", and it
    is exported so that a game launched from the grid does not ask again --
    `gotg play` checks the same variable. It is set only when the answer was
    yes: a failure is worth asking about again, and setting it on the way out
    regardless meant one bad start disabled the check for every game launched
    afterwards. `force` is for the watch that notices a daemon has gone while
    the picker is open, which has to ask past the latch.
    """
    if not force and os.environ.get("DANSTICK_SKIP_DAEMON_CHECK") == "1":
        return None
    # What a daemon starts with. `seating` carries the same length on every
    # open -- see gate.PAIR_HOLD -- but an assignment session takes whatever
    # the daemon was started with, and that is the wizard.
    os.environ.setdefault("DANSTICK_HOLD_SECONDS", str(config.get("theme.timeouts.pair_hold", 1.5)))
    danstick = shutil.which("danstick")
    if danstick is None:
        return "danstick is not installed"
    argv = [danstick, "ensure-daemon"]
    if fresh:
        argv.append("--fresh")
    if follow is not None:
        argv += ["--follow", str(follow)]
    try:
        done = subprocess.run(
            argv,
            capture_output=True,
            timeout=DAEMON_TIMEOUT,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"danstick would not start: {exc}"
    if done.returncode != 0:
        return (done.stderr or done.stdout or "danstick would not start").strip().splitlines()[-1]
    os.environ["DANSTICK_SKIP_DAEMON_CHECK"] = "1"
    if follow is not None:
        # Which session this daemon belongs to, for whatever comes after the
        # execvp: the launch gate reads it to tell seats somebody took in the
        # picker a moment ago from seats a daemon has been holding since
        # yesterday. `danstick.sh` exports the same thing for the same reason.
        os.environ["DANSTICK_FOLLOW"] = str(follow)
    return None


class DaemonWatch:
    """When to ask ensure_daemon again, while the picker is open.

    The picker reconnects to the socket every frame, which recovers a
    *connection* -- a daemon started by somebody else gets picked up -- but a
    daemon that has died stays dead, and the strip said "danstick not running"
    for the rest of the evening. So while there is no connection, the daemon is
    asked after again, on an interval: often enough to feel immediate, rare
    enough that a machine with no danstick at all is not spawning a process every
    frame. Model only; the clock is passed in.
    """

    def __init__(self, interval: float = 5.0) -> None:
        self.interval = interval
        self._last: float | None = None

    def due(self, now: float) -> bool:
        return self._last is None or now - self._last >= self.interval

    def mark(self, now: float) -> None:
        self._last = now


def session_pid() -> int | None:
    """The pid danstick's daemon was asked to follow, if this is that session.

    Set by `ensure_daemon` here and by `danstick_ensure` in the client, and the
    picker's pid survives its execvp -- so a gate running inside the launch
    that picker started sees its own number here.
    """
    try:
        return int(os.environ["DANSTICK_FOLLOW"])
    except (KeyError, ValueError):
        return None


def socket_path() -> Path:
    """Where danstick listens, by the same rule danstick uses to bind.

    Under XDG_RUNTIME_DIR: per-user, mode 0700 by the spec, and cleaned up on
    logout so a stale file never outlives the session that made it.
    """
    return Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "danstick" / "danstick.sock"


class Danstick:
    """A connection to the daemon, or the absence of one.

    Absence is a state rather than an exception. A machine with no daemon
    running is the ordinary case before anyone has set up a controller, and the
    picker should still draw.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path if path is not None else socket_path()
        self._sock: socket.socket | None = None
        self._buffer = b""
        # The last state event: what the strip draws. Kept here so a reconnect
        # cannot leave the screen showing pads that left with the old one.
        self.state: dict = {}
        self.error: str | None = None
        # How many events have come in, ever. The frame loop compares it
        # across a frame to know the daemon said something -- which keeps the
        # screen drawing at full rate while a hold is filling on a pad the
        # picker cannot see itself. See pace.py.
        self.heard = 0

    # --- connection ---------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def connect(self) -> bool:
        """Try once. Returns whether there is now a connection.

        Cheap enough to call from a draw loop when disconnected: an absent
        socket fails at once with ENOENT, and a stale one inside the timeout.
        """
        if self._sock is not None:
            return True
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(CONNECT_TIMEOUT)
            sock.connect(str(self.path))
        except OSError as exc:
            self.error = str(exc)
            return False
        sock.setblocking(False)
        self._sock = sock
        self._buffer = b""
        self.error = None
        # Ask immediately rather than waiting for the daemon to volunteer one:
        # `state` is sent on change, and a front-end that connects between two
        # changes would otherwise draw nothing until somebody moved.
        self.send({"cmd": "status"})
        return True

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None
        self._buffer = b""
        self.state = {}

    def _drop(self, why: str) -> None:
        """The daemon went away. Not fatal -- connect() is tried again."""
        self.close()
        self.error = why

    # --- traffic ------------------------------------------------------------

    def send(self, message: dict) -> bool:
        if self._sock is None:
            return False
        payload = json.dumps(message, separators=(",", ":")).encode() + b"\n"
        try:
            self._sock.sendall(payload)
        except OSError as exc:
            self._drop(str(exc))
            return False
        return True

    def pending(self) -> bool:
        """Whether the daemon has something waiting to be read. Never blocks:
        this is what wakes an idle screen, so a hold on a pad this program
        cannot see fills in without waiting out a slow frame first."""
        if self._sock is None:
            return False
        try:
            readable, _, _ = select.select([self._sock], [], [], 0)
        except (OSError, ValueError):
            return False
        return bool(readable) or b"\n" in self._buffer

    def poll(self) -> Iterator[dict]:
        """Every complete event that has arrived, and nothing else.

        Never blocks and never waits for a whole message: a partial line is
        kept in the buffer for the next frame, which is what stops a long
        `sdl_mapping` event from being read as several broken ones.
        """
        if self._sock is None:
            return
        while True:
            try:
                chunk = self._sock.recv(CHUNK)
            except BlockingIOError:
                break
            except OSError as exc:
                self._drop(str(exc))
                return
            if not chunk:
                self._drop("danstick closed the connection")
                return
            self._buffer += chunk

        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except ValueError:
                # A line this side cannot parse is the daemon's to fix, and
                # dropping it keeps the framing rather than losing the rest of
                # the buffer with it.
                continue
            if isinstance(event, dict):
                if event.get("event") == "state":
                    self.state = event
                self.heard += 1
                trace.progress_gap(event)
                yield event

    # --- the commands the picker sends --------------------------------------

    def begin(self, players: int) -> bool:
        """Start an assignment session: who sits where, asked of the pads.

        danstick takes EVIOCGRAB for the length of it, so no controller input
        reaches this program until it ends -- which is why the screen that runs
        one has to be driven by what comes back rather than by the pad.
        """
        return self.send({"cmd": "begin", "players": players})

    def accept(self) -> bool:
        return self.send({"cmd": "accept"})

    def cancel(self) -> bool:
        return self.send({"cmd": "cancel"})

    def reset(self) -> bool:
        return self.send({"cmd": "reset"})

    def status(self) -> bool:
        return self.send({"cmd": "status"})

    def choose_layout(self, player: int) -> bool:
        """Ask which console this pad is, then map its buttons."""
        return self.send({"cmd": "choose_layout", "player": player})

    def map_for_game(self, player: int, console: str, key: str, title: str) -> bool:
        """Map this pad for one console, or for one game.

        Asked from the library, where both are already known -- which is what
        makes the scope question two entries wide instead of a list of every
        console.
        """
        return self.send({
            "cmd": "map_for_game",
            "player": player,
            "console": console,
            "key": key,
            "title": title,
        })

    def calibrate(self, player: int) -> bool:
        return self.send({"cmd": "calibrate", "player": player})

    def forget_pad(self, player: int) -> bool:
        return self.send({"cmd": "forget_pad", "player": player})

    def unseat(self, player: int | None = None) -> bool:
        """Drop a seat, or every seat, keeping seating open.

        The clone stops, the pad is released, the emulator configs are
        rewritten for whoever is left. Refused while a session is open.
        """
        message: dict = {"cmd": "unseat"}
        if player is not None:
            message["player"] = player
        return self.send(message)

    def seat_keyboard(self) -> bool:
        """Seat the keyboard as the next player.

        No clone: the keyboard is the compositor's. What danstick does is write
        every emulator's configuration so that player N is the keyboard --
        see docs/requests/keyboard-as-a-player.md for what was asked.
        """
        return self.send({"cmd": "seat_keyboard"})

    def skip_control(self) -> bool:
        """Move past a control this pad does not have."""
        return self.send({"cmd": "skip_control"})

    def configure_end(self) -> bool:
        """Leave a modal flow without finishing it."""
        return self.send({"cmd": "configure_end"})

    # --- what the strip draws from ------------------------------------------

    @property
    def players(self) -> list[dict]:
        """The seated pads, in player order.

        `players` entries are {"player": int, "name": str, "node": str}. Sorted
        here because the strip draws them left to right and the daemon promises
        a set rather than an order.
        """
        found = self.state.get("players") or []
        return sorted(
            (p for p in found if isinstance(p, dict) and isinstance(p.get("player"), int)),
            key=lambda p: p["player"],
        )

    @property
    def slots(self) -> int:
        """How many seats were asked for, which is how many the strip shows."""
        asked = self.state.get("slots")
        return asked if isinstance(asked, int) and asked > 0 else 4

    @property
    def status_word(self) -> str:
        """idle, assigning or ready -- and "offline" for no daemon at all."""
        if not self.connected:
            return "offline"
        state = self.state.get("state")
        return state if isinstance(state, str) else "idle"
