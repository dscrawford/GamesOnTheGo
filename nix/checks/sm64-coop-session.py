"""One co-op session, checked against the wrapper that has to run it."""
import sys

from splitscreen.config import load
from splitscreen.layout import compute

path, expected = sys.argv[1], int(sys.argv[2])
session = load(path)

assert len(session.instances) == expected, (
    f"{path}: {len(session.instances)} instances, expected {expected}"
)
assert session.layout == "grid", f"{path}: layout is {session.layout!r}, not grid"

# One window each: these are separate processes, not one emulator opening
# several windows, so nothing here needs a title rule to tell them apart.
for instance in session.instances:
    assert len(instance.window_specs) == 1, f"{instance.id}: {len(instance.window_specs)} windows"

# Exactly one host, and everybody else joining it on that same port.
hosts = [i for i in session.instances if "--server" in i.command]
clients = [i for i in session.instances if "--client" in i.command]
assert len(hosts) == 1, f"{path}: {len(hosts)} hosts, expected 1"
assert len(clients) == expected - 1, f"{path}: {len(clients)} clients"

port = hosts[0].command[hosts[0].command.index("--server") + 1]
for client in clients:
    at = client.command.index("--client")
    joined = client.command[at + 2]
    assert joined == port, f"{client.id} joins {joined}, but the host holds {port}"
    # Never the last argument. Upstream reads the port with
    # `if ((i + 2) < argc)` after consuming the ip, so `--client <ip> <port>`
    # at the end of the line drops the port and silently dials 7777 -- which
    # is three players on a JOINING screen and nothing in any log.
    assert at + 2 < len(client.command) - 1, (
        f"{client.id}: --client is last, so its port would be thrown away"
    )
    # A client that starts before the host spends the opening on a connecting
    # screen, so each one waits for the port to be taken.
    assert client.pre_launch, f"{client.id} does not wait for the host"

# Every player writes somewhere of their own, or they overwrite each other's
# save file and settings.
saves = [i.command[i.command.index("--savepath") + 1] for i in session.instances]
assert len(set(saves)) == expected, f"{path}: save paths collide: {saves}"

# Slots the layout actually produces, for the record: 2 side by side, 3 and 4
# in a 2x2.
print(f"{expected}p ok:", compute(session.layout, expected, 1920, 1080))
