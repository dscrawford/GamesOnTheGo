"""The socket half: framing, reconnection, and what the strip reads.

A real unix socket rather than a mock, because the things that break here are
framing and non-blocking reads -- both of which a mock would simply agree with.
"""

from __future__ import annotations

import json
import socket
import threading

import pytest

from gotg_ui.padmap import Padmap


class FakeDaemon:
    """padmap, as far as the client can tell: one connection, lines in and out."""

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
    fake = FakeDaemon(tmp_path / "padmap.sock")
    yield fake
    fake.close()


def connected(daemon) -> Padmap:
    client = Padmap(daemon.path)
    assert client.connect()
    return client


def test_no_daemon_is_a_state_not_a_crash(tmp_path):
    client = Padmap(tmp_path / "nothing.sock")
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
                 "players": [{"player": 2, "name": "padmap Player 2"},
                             {"player": 1, "name": "padmap Player 1"}]})
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
    client = Padmap(tmp_path / "nothing.sock")
    assert client.send({"cmd": "status"}) is False


# --- starting the daemon ----------------------------------------------------
#
# The picker is usually the first thing open on the machine, so if it does not
# start padmap nothing will. What is tested is that it never becomes a reason
# not to draw: every failure comes back as a sentence.


def fake_padmap(tmp_path, monkeypatch, *, exit_code=0, message=""):
    script = tmp_path / "padmap"
    script.write_text(
        "#!/bin/sh\n"
        f'[ -n "{message}" ] && echo "{message}" >&2\n'
        f"exit {exit_code}\n"
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path), prepend=False)
    monkeypatch.delenv("PADMAP_SKIP_DAEMON_CHECK", raising=False)
    return script


def test_a_daemon_that_starts_says_nothing(tmp_path, monkeypatch):
    from gotg_ui.padmap import ensure_daemon

    fake_padmap(tmp_path, monkeypatch)
    assert ensure_daemon() is None


def test_asking_twice_only_asks_once(tmp_path, monkeypatch):
    # padmap's own flag, and `gotg play` reads the same one -- so a game
    # launched from the grid does not stop to check what the picker checked.
    from gotg_ui.padmap import ensure_daemon

    fake_padmap(tmp_path, monkeypatch)
    ensure_daemon()
    import os

    assert os.environ["PADMAP_SKIP_DAEMON_CHECK"] == "1"
    assert ensure_daemon() is None


def test_a_daemon_that_will_not_start_is_a_sentence(tmp_path, monkeypatch):
    from gotg_ui.padmap import ensure_daemon

    fake_padmap(tmp_path, monkeypatch, exit_code=1, message="no permission for uinput")
    trouble = ensure_daemon()
    assert trouble is not None
    assert "uinput" in trouble


def test_no_padmap_at_all_is_a_sentence_too(tmp_path, monkeypatch):
    from gotg_ui.padmap import ensure_daemon

    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delenv("PADMAP_SKIP_DAEMON_CHECK", raising=False)
    assert ensure_daemon() == "padmap is not installed"
