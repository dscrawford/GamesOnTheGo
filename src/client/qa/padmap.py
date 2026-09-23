#!/usr/bin/env python3
"""A stand-in for padmap's socket, saying one pad joined, once.

The overlay listens to padmap for who is holding to join; in QA there is no
daemon, and the virtual pad cannot join one that is not there. So this plays
the events padmap sends while somebody pairs -- a hold filling for the length
the room asked for, then the claim -- at the second it is told, to every
client connected by then. What the overlay does with them is what is graded:
the events are padmap's own shape (see docs/requests), not a private demo
switch inside the program under test.

Stdlib only; nothing is read from clients.
"""

import argparse
import json
import os
import socket
import sys
import threading
import time

NODE = "/dev/input/qa-joiner"
NAME = "QA Joiner"
PLAYER = 2


def events(hold):
    """The lines padmap sends for one press held to the end, with when."""
    steps = max(int(hold * 30), 1)
    for i in range(1, steps + 1):
        frac = round(i / steps, 3)
        yield hold * i / steps, {"event": "progress", "frac": frac, "node": NODE, "name": NAME, "player": PLAYER}
    yield hold, {"event": "claim", "player": PLAYER, "name": NAME, "node": NODE, "icon": "", "configured": True}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--socket", required=True)
    ap.add_argument("--join-at", type=float, required=True, help="seconds after start")
    ap.add_argument("--hold", type=float, default=1.5)
    args = ap.parse_args()

    try:
        os.unlink(args.socket)
    except FileNotFoundError:
        pass
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(args.socket)
    server.listen(4)
    started = time.monotonic()
    clients = []
    lock = threading.Lock()

    def accept():
        while True:
            conn, _ = server.accept()
            with lock:
                clients.append(conn)

    threading.Thread(target=accept, daemon=True).start()
    print(f"padmap stand-in: listening on {args.socket}", flush=True)

    def send(line):
        data = (json.dumps(line) + "\n").encode()
        with lock:
            for conn in list(clients):
                try:
                    conn.sendall(data)
                except OSError:
                    clients.remove(conn)

    begin = started + args.join_at
    for offset, line in events(args.hold):
        time.sleep(max(begin + offset - time.monotonic(), 0.0))
        send(line)
    with lock:
        print(f"padmap stand-in: joined player {PLAYER} for {len(clients)} client(s)", flush=True)
    # Stay up, quiet, until the session goes: a socket that closed would be
    # reconnected to every couple of seconds for nothing.
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    sys.exit(main())
