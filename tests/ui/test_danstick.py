"""The socket half: framing, reconnection, and what the strip reads.

A real unix socket rather than a mock, because the things that break here are
framing and non-blocking reads -- both of which a mock would simply agree with.
"""

from __future__ import annotations

import json
import socket
import threading

import pytest

from gotg_ui.danstick import Danstick


class FakeDaemon:
    """danstick, as far as the client can tell: one connection, lines in and out."""

    def __init__(self, path):
        self.path = str(path)
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(self.path)
        self.server.listen(1)
        self.conn: socket.socket | None = None
        self.received: list[dict] = []
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()

    def _accept(self):
        conn, _ = self.server.accept()
        self.conn = conn

    def wait(self):
        for _ in range(500):
            if self.conn is not None:
                return
            threading.Event().wait(0.01)
        raise AssertionError("the client never connected")

    def send_raw(self, payload: bytes):
        self.wait()
        self.conn.sendall(payload)

    def send(self, message: dict):
        self.send_raw(json.dumps(message).encode() + b"\n")

    def read_commands(self) -> list[dict]:
        self.wait()
        self.conn.settimeout(0.5)
        buffer = b""
        try:
            while True:
                chunk = self.conn.recv(65536)
                if not chunk:
                    break
                buffer += chunk
                if buffer.endswith(b"\n"):
                    break
        except TimeoutError:
            pass
        return [json.loads(line) for line in buffer.splitlines() if line.strip()]

    def close(self):
        if self.conn is not None:
            self.conn.close()
        self.server.close()


@pytest.fixture
def daemon(tmp_path):
    fake = FakeDaemon(tmp_path / "danstick.sock")
    yield fake
    fake.close()


def connected(daemon) -> Danstick:
    client = Danstick(daemon.path)
    assert client.connect()
    return client


def test_no_daemon_is_a_state_not_a_crash(tmp_path):
    client = Danstick(tmp_path / "nothing.sock")
    assert not client.connect()
    assert not client.connected
    assert client.status_word == "offline"
    assert list(client.poll()) == []


def test_connecting_asks_for_the_state_at_once(daemon):
    # Otherwise a front-end that connects between two changes draws nothing
    # until somebody moves a controller.
    connected(daemon)
    assert {"cmd": "status"} in daemon.read_commands()


def test_events_arrive_whole(daemon):
    client = connected(daemon)
    daemon.send({"event": "state", "state": "ready", "slots": 2, "players": []})
    daemon.send({"event": "claim", "player": 1})
    events = []
    for _ in range(100):
        events.extend(client.poll())
        if len(events) >= 2:
            break
    assert [e.get("event") for e in events] == ["state", "claim"]


def test_a_message_split_across_reads_is_not_two_broken_ones(daemon):
    client = connected(daemon)
    payload = json.dumps({"event": "sdl_mapping", "lines": ["x" * 4000]}).encode() + b"\n"
    daemon.send_raw(payload[:100])
    assert list(client.poll()) == []  # nothing complete yet
    daemon.send_raw(payload[100:])
    events = []
    for _ in range(100):
        events.extend(client.poll())
        if events:
            break
    assert events[0]["event"] == "sdl_mapping"


def test_state_is_remembered_for_the_strip(daemon):
    client = connected(daemon)
    daemon.send({"event": "state", "state": "ready", "slots": 4,
                 "players": [{"player": 2, "name": "danstick Player 2"},
                             {"player": 1, "name": "danstick Player 1"}]})
    for _ in range(100):
        list(client.poll())
        if client.players:
            break
    assert [p["player"] for p in client.players] == [1, 2]
    assert client.slots == 4
    assert client.status_word == "ready"


def test_a_daemon_that_goes_away_leaves_no_stale_pads(daemon):
    client = connected(daemon)
    daemon.send({"event": "state", "state": "ready", "slots": 4,
                 "players": [{"player": 1, "name": "one"}]})
    for _ in range(100):
        list(client.poll())
        if client.players:
            break
    daemon.conn.close()
    for _ in range(100):
        list(client.poll())
        if not client.connected:
            break
    assert not client.connected
    assert client.players == []
    assert client.status_word == "offline"


def test_a_line_that_is_not_json_does_not_eat_the_rest(daemon):
    client = connected(daemon)
    daemon.send_raw(b"{not json\n")
    daemon.send({"event": "state", "state": "idle", "slots": 1, "players": []})
    events = []
    for _ in range(100):
        events.extend(client.poll())
        if events:
            break
    assert [e.get("event") for e in events] == ["state"]


def test_the_commands_are_the_protocol_s_words(daemon):
    client = connected(daemon)
    client.begin(4)
    client.choose_layout(2)
    client.map_for_game(1, "n64", "n64/goldeneye-007-usa", "GoldenEye 007")
    sent = daemon.read_commands()
    assert {"cmd": "begin", "players": 4} in sent
    assert {"cmd": "choose_layout", "player": 2} in sent
    assert {"cmd": "map_for_game", "player": 1, "console": "n64",
            "key": "n64/goldeneye-007-usa", "title": "GoldenEye 007"} in sent


def test_sending_to_a_dead_socket_reports_rather_than_raises(tmp_path):
    client = Danstick(tmp_path / "nothing.sock")
    assert client.send({"cmd": "status"}) is False


# --- starting the daemon ----------------------------------------------------
#
# The picker is usually the first thing open on the machine, so if it does not
# start danstick nothing will. What is tested is that it never becomes a reason
# not to draw: every failure comes back as a sentence.


def fake_danstick(tmp_path, monkeypatch, *, exit_code=0, message=""):
    script = tmp_path / "danstick"
    script.write_text(
        "#!/bin/sh\n"
        f'[ -n "{message}" ] && echo "{message}" >&2\n'
        f"exit {exit_code}\n"
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path), prepend=False)
    monkeypatch.delenv("DANSTICK_SKIP_DAEMON_CHECK", raising=False)
    return script


def test_a_daemon_that_starts_says_nothing(tmp_path, monkeypatch):
    from gotg_ui.danstick import ensure_daemon

    fake_danstick(tmp_path, monkeypatch)
    assert ensure_daemon() is None


def test_the_picker_starts_the_daemon_with_fixed_slots(tmp_path, monkeypatch):
    # The same policy `gotg play` asks for: danstick replaces a daemon on
    # other slots, and the picker's seats would go with it at the launch.
    import os

    from gotg_ui.danstick import ensure_daemon

    fake_danstick(tmp_path, monkeypatch)
    monkeypatch.delenv("DANSTICK_SLOTS", raising=False)
    ensure_daemon()
    assert os.environ["DANSTICK_SLOTS"] == "fixed"
    monkeypatch.setenv("DANSTICK_SLOTS", "on-demand")
    monkeypatch.delenv("DANSTICK_SKIP_DAEMON_CHECK", raising=False)
    ensure_daemon()
    assert os.environ["DANSTICK_SLOTS"] == "on-demand", "somebody's own choice stands"


def test_asking_twice_only_asks_once(tmp_path, monkeypatch):
    # danstick's own flag, and `gotg play` reads the same one -- so a game
    # launched from the grid does not stop to check what the picker checked.
    from gotg_ui.danstick import ensure_daemon

    fake_danstick(tmp_path, monkeypatch)
    ensure_daemon()
    import os

    assert os.environ["DANSTICK_SKIP_DAEMON_CHECK"] == "1"
    assert ensure_daemon() is None


def test_a_daemon_that_will_not_start_is_a_sentence(tmp_path, monkeypatch):
    from gotg_ui.danstick import ensure_daemon

    fake_danstick(tmp_path, monkeypatch, exit_code=1, message="no permission for uinput")
    trouble = ensure_daemon()
    assert trouble is not None
    assert "uinput" in trouble


def test_no_danstick_at_all_is_a_sentence_too(tmp_path, monkeypatch):
    from gotg_ui.danstick import ensure_daemon

    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delenv("DANSTICK_SKIP_DAEMON_CHECK", raising=False)
    assert ensure_daemon() == "danstick is not installed"


def test_a_daemon_that_would_not_start_is_asked_again(tmp_path, monkeypatch):
    # The latch used to be set on the way out regardless, so one bad start at
    # the picker disabled the check for every game launched from it after.
    import os

    from gotg_ui.danstick import ensure_daemon

    fake_danstick(tmp_path, monkeypatch, exit_code=1, message="no permission for uinput")
    assert ensure_daemon() is not None
    assert "DANSTICK_SKIP_DAEMON_CHECK" not in os.environ
    assert ensure_daemon() is not None


def test_force_asks_past_the_latch(tmp_path, monkeypatch):
    # The watch that notices a daemon has gone while the picker is open has
    # to ask again, and the latch was set by the picker's own first ask.
    import os

    from gotg_ui.danstick import ensure_daemon

    script = fake_danstick(tmp_path, monkeypatch)
    assert ensure_daemon() is None
    assert os.environ["DANSTICK_SKIP_DAEMON_CHECK"] == "1"
    log = tmp_path / "calls"
    script.write_text(f'#!/bin/sh\necho asked >>{log}\nexit 0\n')
    assert ensure_daemon() is None
    assert not log.exists()
    assert ensure_daemon(force=True) is None
    assert log.read_text().count("asked") == 1


def test_the_daemon_is_started_unseated_and_following_the_session(tmp_path, monkeypatch):
    # Nobody is seated when a picker or a game opens, and the daemon goes
    # when the session does. Both are danstick's flags; this only has to say
    # them, and say the pid it means.
    from gotg_ui.danstick import ensure_daemon

    script = fake_danstick(tmp_path, monkeypatch)
    log = tmp_path / "calls"
    script.write_text(f'#!/bin/sh\necho "$@" >>{log}\nexit 0\n')
    assert ensure_daemon(fresh=True, follow=4321) is None
    assert log.read_text().strip() == "ensure-daemon --fresh --follow 4321"


def test_asking_again_mid_session_follows_but_is_not_fresh(tmp_path, monkeypatch):
    # A daemon that died halfway through an evening restores the seats it
    # had, which is what somebody halfway through an evening wants back.
    from gotg_ui.danstick import ensure_daemon

    script = fake_danstick(tmp_path, monkeypatch)
    log = tmp_path / "calls"
    script.write_text(f'#!/bin/sh\necho "$@" >>{log}\nexit 0\n')
    assert ensure_daemon(force=True, follow=4321) is None
    assert log.read_text().strip() == "ensure-daemon --follow 4321"


def test_seat_keyboard_is_the_protocols_word(daemon):
    client = connected(daemon)
    client.seat_keyboard()
    assert {"cmd": "seat_keyboard"} in daemon.read_commands()


def test_unseat_is_the_protocols_word(daemon):
    client = connected(daemon)
    client.unseat()
    client.unseat(2)
    sent = daemon.read_commands()
    assert {"cmd": "unseat"} in sent
    assert {"cmd": "unseat", "player": 2} in sent


def test_the_watch_asks_on_an_interval_not_every_frame():
    from gotg_ui.danstick import DaemonWatch

    watch = DaemonWatch(interval=5.0)
    assert watch.due(100.0)
    watch.mark(100.0)
    assert not watch.due(101.0)
    assert not watch.due(104.9)
    assert watch.due(105.0)
    watch.mark(105.0)
    assert not watch.due(109.0)
