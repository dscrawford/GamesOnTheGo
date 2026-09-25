"""Pairing, with a room full of people: several controllers held at once, measured.

The connection API is padmap's seating mode: `{"cmd": "seating", "open": true,
"players": 4, "hold": 1.5}`, then a hold of any button on any unseated pad
claims the next seat. The daemon says `progress` per pad per tick (`frac`,
`name`, `node`, `player` -- the seat that hold is filling towards; `frac: 0`
when it is let go), `claim` when a seat goes, and `state` after. Everything in
here drives that directly -- no picker, no gate -- because the thing reported
from the sofa lives in the daemon: "if someone claims a controller, it cancels
another controller and they have to hold A again."

The evening it was reported is in a trace (session 249121, /tmp/gotg-trace.log
and padmap.log of 2026-09-23): the Xbox pad went down at ~2.02 s, the Steam
Controller 1.22 s later; the Xbox pad claimed seat one at 3.567 s with the
Steam Controller's fill at 0.227, and the next reading for it came 965 ms
later at 0.047 -- a second press. Its seat came 2.75 s after its first press
instead of 1.5. Readings came 48-61 ms apart on that desktop (one 565 ms stall
while a Steam Controller paired), and claim-to-`state` took 195-255 ms across
five sessions. The timings below come from those numbers.

Situations, and what each expects of the padmap pinned in flake.nix (e0092be;
the markers below came off as padmap answered them -- bf6606d, 26754a9,
bc61806, 0bcd1dc):

  Several people, one room
  - four pads pressed 0.3 s apart, in the reverse of the order they were
    plugged in, all held: four claims, seats 1-4 in press order, each at its
    own hold's length. STRICT XFAIL -- all four are seated in order now, but a
    later claim waits behind the previous one's writing and lands ~0.6 s
    late: a-join-costs-the-same-however-full.md.
  - the same four, letting go and pressing again: all four seated, in press
    order, one clone each. PASSES.
  - four pads pressed in one tick: no seat given twice, no pad seated twice.
    PASSES.
  - the reported evening, replayed: the second pad's fill keeps climbing
    through the first claim and takes seat two without a second press. PASSES.
  - the reported evening, the way it ended: the second person lets go and
    presses again, and is seated. PASSES.
  - letting go loses your place: the pad that kept holding is seated first,
    and while both fill the readings say who is ahead. PASSES.

  Things that happen mid-hold
  - a `status` (so a `state`) mid-hold resets nothing. PASSES.
  - `seating` sent again with the same hold resets nothing. PASSES.
  - a press made while the last claim is still being handled is not lost.
    STRICT XFAIL -- the watched pads are reopened after a claim and the
    queued press goes with them: a-press-during-a-claim-is-kept.md.
  - a controller switched on mid-hold resets nobody's hold. PASSES.

  A full room
  - a fifth pad held with four seats taken: seated nowhere, no fifth clone,
    and the four seated players still reach their clones. PASSES.
  - ...and it is told so (`full`). PASSES.
  - ...and another spare pad's fill is not stopped by it. PASSES.

  Joining mid-game (the overlay's case: seating stays open, nobody interacts)
  - a pad held mid-game is seated, its clone appears, and its presses reach
    the game inside a frame. PASSES.
  - the players already in the game lose their input for under a second
    while somebody joins. PASSES.
  - ...and not at all: their clones stay the same devices. PASSES.

  Performance
  - hold to claim is the hold plus little; claim to the new clone, and the
    first seat's claim to `state`, are bounded. PASSES.
  - the fourth seat's `state` comes within 100 ms of the first's. STRICT
    XFAIL -- 206 ms at e0092be, down from 1.19 s:
    a-join-costs-the-same-however-full.md.
  - four holds at once: each is read often enough that the 0.5 s safety net
    in `joining.NAMED_STALE` is never crossed, starts filling promptly, and
    fills towards its press-order seat. PASSES.
  - four holds at once cost a seated pad's forwarding nothing. STRICT XFAIL
    -- p95 went from ~15 ms at d2000c5 to 16.85 and 16.90 ms at e0092be:
    holds-cost-a-seated-pad-nothing.md.

A strict xfail here uses `raises=AssertionError`, and the setup inside it
fails through `pytest.fail` instead: a broken precondition is a real failure,
never mistaken for the known bug. The day padmap fixes one, it XPASSes, the
suite fails, and the marker comes off.

Never on a machine somebody is playing on -- see conftest. Run on the cluster
(k8s/controllers), or `nix run .#test-controllers -- -q -k pairing`.
"""

from __future__ import annotations

import contextlib
import json
import os
import select
import statistics
import time
from collections.abc import Callable, Iterator

import pytest
from fakepad import BTN_SOUTH, FakePad, kernel_names
from test_controllers import A_FRAME_MS, EV_KEY_T, EVENT, _percentile, _tap_latencies

from gotg_ui.joining import NAMED_STALE

# --- the numbers, and where each came from ------------------------------------

# The hold the trace was taken with, and theme.timeouts.pair_hold's default.
# Fixed rather than read from the theme: the offsets below are fractions of
# it, and test_a_seat_takes_the_hold_the_picker_asked_for already proves the
# configured length reaches the daemon.
HOLD = 1.5

# How far apart the staggered presses are: well over padmap's 20 ms tick, so
# press order is unambiguous, and short enough that all four are mid-hold
# when the first claims.
STAGGER = 0.3

# The reported evening: the Steam Controller went down 1.22 s after the Xbox
# pad, and was a third of a second into its hold when the Xbox pad claimed.
EVENING_GAP = 1.22

# How late a claim may land after its hold's length: a 20 ms tick, plus a
# once-a-second scan (~100 ms on a desktop). The trace's 565 ms stall was a
# Steam Controller pairing, which a pod has none of. Later than this, a person
# wonders whether it worked.
CLAIM_SLACK = 0.4

# The median of those, tighter: one stall is weather, every claim late is a
# regression. Tick plus a scan's share.
CLAIM_SLACK_TYPICAL = 0.15

# A claim is never early: the hold asked for is the hold taken. Allowance for
# the few milliseconds between writing the press and padmap reading it,
# which pushes the claim later, never earlier -- this is clock granularity.
EARLY = 0.02

# Claim to `state`, and claim to the clone's node. The trace: 195-255 ms to
# `state` (the republish writes profiles and SDL mappings first), and the
# clone 2 ms after the claim line in padmap.log. Four times the worst seen,
# for a slow pod.
PUBLISH_WITHIN = 1.0

# A pause with nothing claimed. After a claim, wait for the daemon instead
# (`Room.listening`): at the pin a claim republishes every clone and rewrites
# every seated player's config, *then* reopens the watched pads, and a press
# made in between goes to a closed fd. That window was ~250 ms on the desktop
# and 0.3-1.5 s on the pod, growing a seat at a time -- a fixed 0.6 s here
# lost presses there and read as a wrong seat order.
SETTLE = 0.6

# How long the republish after a claim may take before the daemon is
# listening again: the pod's fourth seat took 1.46 s.
LISTENING_WITHIN = 4.0

# How much later a room's fourth seat may reach `state` than its first. Near
# nothing when a join publishes only the pad that joined; 1.19 s on the pod at
# the pin, where each join redoes the whole room.
STATE_GROWTH = 0.1

# Readings: the desktop's median was 58 ms (tick 20 ms plus scans). The max is
# the contract -- `joining.NAMED_STALE`, past which the overlay and the picker
# take a pad's fill off the screen. The median bound catches the loop getting
# slower long before the safety net is reached.
CADENCE_MEDIAN = 0.1

# Press to first reading: the ring should start moving the moment a thumb
# goes down. A tick, a scan, and a slow pod.
FIRST_READING_WITHIN = 0.25

# What four holds may add to a seated pad's median press latency. Seen with
# seating closed: 0.03 ms. Four holds are four small JSON lines per tick; two
# milliseconds of median is well past noise and well short of a frame.
LOAD_SLACK_MS = 2.0

# The longest a seated player's presses may stop arriving while somebody else
# joins. At this pin the join tears every clone down and makes it again (~50
# ms in padmap.log); a second is the point where a player notices.
REJOIN_WITHIN = 1.0

# --- what is known to be broken at the pin, and where it is asked for ----------

JOIN_COST = pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="a claim's `state` still waits behind writing the room's files, so it grows with the room "
    "(252-458 ms for seats 1-4 at e0092be) and a staggered claim lands after the previous one's. "
    "docs/requests/a-join-costs-the-same-however-full.md",
)
ITEM_1_REOPEN = pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="after a claim padmap reopens the pads it watches; a press made in between is queued on the "
    "closed fd and gone (e0092be). docs/requests/a-press-during-a-claim-is-kept.md",
)


# --- the room: the socket read continuously while presses happen on time ------


class Room:
    """The daemon's socket, read without pause, with presses done on a script.

    Every event is stamped as it arrives, on one clock with the presses, so
    "how long after the press" is a subtraction. Not `Daemon.drain`, which
    sleeps 20 ms on a quiet socket -- a third of the gaps this measures --
    and not `_progress_arrivals`, which drops everything but progress,
    claims included.
    """

    def __init__(self, daemon):
        self.daemon = daemon
        self.zero = time.monotonic()
        self.log: list[tuple[float, dict]] = []
        self.marks: list[tuple[float, str]] = []
        self._fresh = False

    def now(self) -> float:
        return time.monotonic() - self.zero

    def mark(self, label: str) -> float:
        at = self.now()
        self.marks.append((at, label))
        return at

    def run(
        self,
        until: float,
        script: list[tuple[float, Callable[[], object]]] = (),
        stop: Callable[[], bool] | None = None,
    ) -> bool:
        """Read until `until` (room time), doing each step of `script` when it
        is due. True as soon as `stop` holds; steps not yet due are skipped."""
        pending = sorted(script, key=lambda step: step[0])
        until = max([until] + [at for at, _ in pending])
        self._fresh = True
        while True:
            now = self.now()
            while pending and pending[0][0] <= now:
                pending.pop(0)[1]()
                self._fresh = True
                now = self.now()
            if stop is not None and self._fresh:
                self._fresh = False
                if stop():
                    return True
            if now >= until:
                return False
            wait = 0.002 if not pending else max(0.0, min(0.002, pending[0][0] - now))
            self._read(wait)

    def settle(self, seconds: float = SETTLE) -> None:
        self.run(self.now() + seconds)

    def seated_by(self, player: int, since: float) -> float | None:
        """When a `state` after `since` first had this player seated."""
        for at, event in self.events("state", since):
            if any(p.get("player") == player for p in event.get("players") or []):
                return at
        return None

    def listening(self, claim: tuple[float, dict]) -> None:
        """Until padmap is reading the unseated pads again after this claim.

        The claim's `state` goes out just before seating reopens its pads, in
        the same tick; a tenth of a second past it is past the reopen.
        """
        at, event = claim
        player = event.get("player")
        self.run(at + LISTENING_WITHIN, stop=lambda: self.seated_by(player, at) is not None)
        if self.seated_by(player, at) is None:
            pytest.fail(f"no state seated player {player} within {LISTENING_WITHIN} s of the claim")
        self.settle(0.1)

    def _read(self, wait: float) -> None:
        sock = self.daemon.sock
        if not select.select([sock], [], [], wait)[0]:
            return
        try:
            chunk = sock.recv(65536)
        except BlockingIOError:
            return
        if not chunk:
            pytest.fail("padmap closed the socket mid-test -- did the daemon die?")
        stamp = self.now()
        self.daemon.buffer += chunk
        while b"\n" in self.daemon.buffer:
            line, self.daemon.buffer = self.daemon.buffer.split(b"\n", 1)
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("event") == "state" and isinstance(event.get("pid"), int):
                self.daemon.pid = event["pid"]
            self.daemon.seen.append(event)
            self.log.append((stamp, event))
            self._fresh = True

    # What was said.

    def events(self, kind: str, since: float = 0.0) -> list[tuple[float, dict]]:
        return [(at, e) for at, e in self.log if at >= since and e.get("event") == kind]

    def claims(self, since: float = 0.0) -> list[tuple[float, dict]]:
        return self.events("claim", since)

    def claim(self, pad: FakePad, since: float = 0.0) -> tuple[float, dict] | None:
        return next(((at, e) for at, e in self.claims(since) if e.get("name") == pad.name), None)

    def readings(self, pad: FakePad, since: float = 0.0) -> list[tuple[float, float, int | None]]:
        """(arrived, frac, player) for every `progress` naming this pad."""
        return [
            (at, float(e.get("frac", 0)), e.get("player"))
            for at, e in self.events("progress", since)
            if e.get("name") == pad.name
        ]

    def pressed(self, pad: FakePad) -> float:
        """When this pad last went down, by the script."""
        return max(at for at, label in self.marks if label == f"down:{pad.name}")

    def players(self) -> list[dict]:
        for _, event in reversed(self.log):
            if event.get("event") == "state":
                return event.get("players") or []
        return self.daemon.players


def _press(room: Room, pad: FakePad) -> Callable[[], None]:
    """A script step: this pad goes down, and the room notes when."""

    def step() -> None:
        room.mark(f"down:{pad.name}")
        pad.down(BTN_SOUTH)

    return step


@contextlib.contextmanager
def _pads(count: int, first: str = "A") -> Iterator[list[FakePad]]:
    """`count` pads, each with ids of its own, named so events can say which.

    Plugged in A, B, C... -- the order padmap enumerates them in, give or
    take, which the tests press against on purpose.
    """
    with contextlib.ExitStack() as stack:
        base = ord(first) - ord("A")
        yield [
            stack.enter_context(FakePad(f"PAIR Pad {chr(ord(first) + i)}", 0x2A10 + base + i, 0x5B10 + base + i, 1))
            for i in range(count)
        ]


def _open(daemon, hold: float = HOLD, players: int = 4) -> Room:
    """Seating open, as the picker and the gate open it, and every pad watched.

    A second is padmap's scan interval: pads made just before this are all
    being read before anybody presses.
    """
    daemon.send({"cmd": "seating", "open": True, "players": players, "hold": hold})
    daemon.drain(1.2)
    return Room(daemon)


def _seat_alone(room: Room, pad: FakePad, tries: int = 3) -> tuple[float, float, dict]:
    """Hold one pad by itself until it is seated: (pressed, claimed, claim).

    Nothing else is in flight, so nothing is lost to the bug -- this is how
    each test fills the seats its situation starts from. A hold that nobody
    read is held again, as a person would; see Picker.claim.
    """
    for _ in range(tries):
        pressed = room.now()
        _press(room, pad)()
        room.run(pressed + HOLD + 2.0, stop=lambda pressed=pressed: room.claim(pad, pressed) is not None)
        pad.up(BTN_SOUTH)
        found = room.claim(pad, pressed)
        if found is not None:
            room.listening(found)
            return pressed, found[0], found[1]
        room.settle()
    pytest.fail(f"{pad.name} held {tries} times with nothing else going on, and was never seated")


def _awake(room: Room, *pads: FakePad, within: float = 4.0) -> None:
    """Tap each pad until padmap reads it, so a hold that follows is timed from
    its press. A tap is far shorter than any hold and claims nothing.

    `Room.listening` is not enough on its own: after a claim's `state` the
    pinned daemon still walks every input device and reopens the pads it
    watches, and on the pod that took another ~0.2 s (holds pressed then
    claimed 150-190 ms late). A press inside it started late or not at all,
    and a test timed from it measured something else -- the full-room
    refusal test XPASSed that way.
    """
    for pad in pads:
        end = room.now() + within
        while room.now() < end:
            tapped = room.now()
            pad.down(BTN_SOUTH)
            room.run(tapped + 0.08)
            pad.up(BTN_SOUTH)
            if room.run(tapped + 0.3, stop=lambda pad=pad, tapped=tapped: bool(room.readings(pad, tapped))):
                break
        else:
            pytest.fail(f"seating never read {pad.name} in {within} s of tapping it")
    room.settle(0.2)


def _refused_at(room: Room, pad: FakePad, since: float) -> float:
    """When this pad's hold ran out in a full room: its reading of 1.0, which
    padmap sends on the tick the hold completes, refused or not."""
    done = [at for at, f, _ in room.readings(pad, since) if f >= 0.999]
    if not done:
        pytest.fail(f"{pad.name}'s hold never ran to the end -- no refusal happened to measure")
    return done[0]


def _seat_in_turn(room: Room, pads: list[FakePad]) -> None:
    for pad in pads:
        _seat_alone(room, pad)


# --- the kernel side: padmap's clones -----------------------------------------


def _clone_info(player: int) -> tuple[str, str] | None:
    """(/dev/input/eventN, sysfs path) of `padmap Player N`, or None.

    The sysfs path (…/inputM) is the device's identity: M is never reused
    soon, where eventN is, so a clone torn down and made again is told apart
    from the same one kept.
    """
    name, sysfs = None, ""
    with open("/proc/bus/input/devices") as devices:
        for line in devices:
            if line.startswith('N: Name="'):
                name, sysfs = line.split('"')[1], ""
            elif line.startswith("S: Sysfs="):
                sysfs = line.split("=", 1)[1].strip()
            elif line.startswith("H: Handlers=") and name == f"padmap Player {player}":
                for handler in line.split("=", 1)[1].split():
                    if handler.startswith("event"):
                        return f"/dev/input/{handler}", sysfs
    return None


def _wait_clone(player: int, within: float) -> tuple[str, str] | None:
    end = time.monotonic() + within
    while time.monotonic() < end:
        found = _clone_info(player)
        if found:
            return found
        time.sleep(0.005)
    return None


def _flush(fd: int) -> None:
    while True:
        try:
            if not os.read(fd, EVENT.size * 256):
                return
        except BlockingIOError:
            return


def _pressed_on(fd: int, value: int, seconds: float) -> float | None:
    """When BTN_SOUTH=`value` arrived on this clone, or None. A clone that has
    gone (ENODEV) delivered nothing."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not select.select([fd], [], [], 0.002)[0]:
            continue
        arrived = time.monotonic()
        try:
            data = os.read(fd, EVENT.size * 256)
        except BlockingIOError:
            continue
        except OSError:
            return None
        for at in range(0, len(data) - EVENT.size + 1, EVENT.size):
            _, _, kind, code, got = EVENT.unpack_from(data, at)
            if kind == EV_KEY_T and code == BTN_SOUTH and got == value:
                return arrived
    return None


def _reaches(pad: FakePad, fd: int, seconds: float = 0.3) -> bool:
    """One press on the pad, read on the clone behind `fd`."""
    try:
        _flush(fd)
    except OSError:
        return False
    pad.down(BTN_SOUTH)
    try:
        return _pressed_on(fd, 1, seconds) is not None
    finally:
        pad.up(BTN_SOUTH)


def _each_seat_once(room: Room, pads: list[FakePad]) -> None:
    """Every pad seated once, every seat given once, and one clone a seat."""
    room.run(room.now() + PUBLISH_WITHIN, stop=lambda: len(room.players()) >= len(pads))
    claims = room.claims()
    names = [e["name"] for _, e in claims]
    assert sorted(names) == sorted(p.name for p in pads), f"not one claim a pad: {names}"
    players = sorted(e["player"] for _, e in claims)
    assert players == list(range(1, len(pads) + 1)), f"a seat went twice or was skipped: {players}"
    seated = room.players()
    assert sorted(p.get("player") for p in seated) == players, f"state disagrees with the claims: {seated}"
    nodes = [p.get("node") for p in seated]
    assert len(set(nodes)) == len(nodes), f"one pad in two seats: {seated}"
    present = kernel_names()
    for player in players:
        assert present.count(f"padmap Player {player}") == 1, (
            f"player {player} has {present.count(f'padmap Player {player}')} clones"
        )
    assert f"padmap Player {len(pads) + 1}" not in present


# --- several people, one room --------------------------------------------------


@JOIN_COST
def test_four_pads_pressed_apart_take_seats_one_to_four_in_press_order(daemon):
    """The party: four people pick up pads a moment apart and hold A.

    Pressed D, C, B, A -- the reverse of how they were plugged in, so press
    order and plug order disagree and the answer says which one won.
    """
    with _pads(4) as (a, b, c, d):
        order = [d, c, b, a]
        room = _open(daemon)
        start = room.now() + 0.05
        room.run(
            start + STAGGER * 3 + HOLD + CLAIM_SLACK + 1.0,
            [(start + STAGGER * i, _press(room, pad)) for i, pad in enumerate(order)],
            stop=lambda: len(room.claims()) >= 4,
        )
        for pad in order:
            pad.up(BTN_SOUTH)

        claims = room.claims()
        assert [e["name"] for _, e in claims] == [p.name for p in order], (
            f"four holds, claims {[(e['name'], e['player']) for _, e in claims]}"
        )
        assert [e["player"] for _, e in claims] == [1, 2, 3, 4]
        for (at, _), pad in zip(claims, order, strict=True):
            late = at - room.pressed(pad) - HOLD
            assert -EARLY <= late <= CLAIM_SLACK, f"{pad.name} claimed {late * 1000:+.0f} ms off its own hold"


def test_four_pads_pressed_apart_are_all_seated_by_pressing_again(daemon):
    """What people do today, and it has to keep working: whoever's fill died
    lets go and presses again, in the order they were in. Everybody is
    seated, in that order, once. With padmap fixed each round seats more
    than one; the assertions do not care how many rounds it took."""
    with _pads(4) as (a, b, c, d):
        waiting = [d, c, b, a]
        room = _open(daemon)
        rounds = 0
        while waiting and rounds < 8:
            rounds += 1
            start = room.now() + 0.05
            room.run(
                start + STAGGER * (len(waiting) - 1) + HOLD + 2.0,
                [(start + STAGGER * i, _press(room, pad)) for i, pad in enumerate(waiting)],
                stop=lambda start=start: bool(room.claims(start)),
            )
            for pad in waiting:
                pad.up(BTN_SOUTH)
            # Seated at any time, not only by the claim that ended the round:
            # holds survive a claim now, so a second pad can take its seat
            # between the round stopping and its thumb coming up.
            room.settle()
            taken = room.claims(start)
            waiting = [pad for pad in waiting if room.claim(pad) is None]
            if taken:
                room.listening(taken[-1])

        assert not waiting, f"{[p.name for p in waiting]} never seated in {rounds} rounds"
        assert [e["name"] for _, e in room.claims()] == [p.name for p in (d, c, b, a)], (
            "pressing again did not keep the order people pressed in"
        )
        _each_seat_once(room, [a, b, c, d])
        print(f"\nfour seated by pressing again: {rounds} rounds, {room.now():.1f} s")


def test_four_pads_pressed_in_one_tick_never_share_a_seat(daemon):
    """Four thumbs down in the same 20 ms: however many seats that one hold
    gives out, no seat twice and no pad twice -- and the rest are seated by
    holding again."""
    with _pads(4) as pads:
        room = _open(daemon)
        pressed = room.now()
        for pad in pads:
            _press(room, pad)()
        room.run(pressed + HOLD + CLAIM_SLACK + 1.0, stop=lambda: len(room.claims()) >= 4)
        for pad in pads:
            pad.up(BTN_SOUTH)

        together = room.claims()
        assert together, "four pads held together and nobody was seated"
        assert len({e["player"] for _, e in together}) == len(together), f"one seat, two claims: {together}"
        assert len({e["name"] for _, e in together}) == len(together), f"one pad, two seats: {together}"
        room.listening(together[-1])

        # The first claim of the tick at the hold's length. The rest are
        # announced one after another, each behind the previous one's
        # whole-room republish and `state` (tick_seating's claim loop at the
        # pin; the pod had the second 415 ms late). That growth is
        # test_a_join_costs_the_same_however_full_the_room's; here each later
        # claim is bounded against the previous claim's `state` instead.
        first_at, first = together[0]
        late = first_at - pressed - HOLD
        assert -EARLY <= late <= CLAIM_SLACK, f"{first['name']} claimed {late * 1000:+.0f} ms off its hold"
        for (before_at, before), (at, event) in zip(together, together[1:], strict=False):
            assert at - pressed - HOLD >= -EARLY, f"{event['name']} claimed before its hold was up"
            published = room.seated_by(before["player"], before_at)
            if published is None:
                pytest.fail(f"no state for player {before['player']} ever came")
            behind = at - published
            assert behind <= CLAIM_SLACK, (
                f"{event['name']} was announced {behind * 1000:.0f} ms after the previous claim's state"
            )
        _seat_in_turn(room, [pad for pad in pads if room.claim(pad) is None])
        _each_seat_once(room, pads)
        print(f"\none tick, four holds: {len(together)} seated by it")


def test_a_claim_does_not_cancel_another_hold_in_flight(daemon):
    """The bug, replayed at the reported evening's timing.

    The second pad goes down 1.22 s after the first and is a third of the way
    through its hold when the first claims. It must go on filling from there
    and take seat two at its own hold's length, with nobody letting go.
    """
    with _pads(2) as (first, second):
        room = _open(daemon)
        start = room.now() + 0.05
        room.run(
            start + EVENING_GAP + HOLD + CLAIM_SLACK + 0.5,
            [(start, _press(room, first)), (start + EVENING_GAP, _press(room, second))],
            stop=lambda: room.claim(second) is not None,
        )
        first.up(BTN_SOUTH)
        second.up(BTN_SOUTH)

        one = room.claim(first)
        if one is None or one[1]["player"] != 1:
            pytest.fail(f"the first pad was not seated first: {room.claims()}")
        before = [f for at, f, _ in room.readings(second) if at <= one[0] and f > 0]
        after = [f for at, f, _ in room.readings(second) if at > one[0] and f > 0]
        if not before:
            pytest.fail("the second pad never started filling -- nothing here is about the claim")
        assert after and max(after) >= max(before) + 0.2, (
            f"the second pad's fill stopped at {max(before):.2f} when the first claimed"
        )
        two = room.claim(second)
        assert two is not None, "the second pad never took a seat without pressing again"
        assert two[1]["player"] == 2
        late = two[0] - room.pressed(second) - HOLD
        assert -EARLY <= late <= CLAIM_SLACK, f"the second claim came {late * 1000:+.0f} ms off its own hold"


def test_letting_go_and_pressing_again_seats_the_second_pad(daemon):
    """How the reported evening ended, and the workaround until padmap is fixed.

    The first pad claims; the second person, 0.8 s into a fill that went
    nowhere, lets go and presses again 0.1 s later -- the trace's 965 ms gap.
    They are seated second. With padmap fixed the fill would have finished
    on its own; letting go before it does is still a release, so this passes
    either way.
    """
    with _pads(2) as (first, second):
        room = _open(daemon)
        start = room.now() + 0.05
        room.run(
            start + HOLD + CLAIM_SLACK + 1.0,
            [(start, _press(room, first)), (start + EVENING_GAP, _press(room, second))],
            stop=lambda: room.claim(first) is not None,
        )
        first.up(BTN_SOUTH)
        one = room.claim(first)
        assert one is not None and one[1]["player"] == 1, f"the first pad was not seated: {room.claims()}"

        again = one[0] + 0.9
        room.run(
            again + HOLD + CLAIM_SLACK + 1.0,
            [(one[0] + 0.8, lambda: second.up(BTN_SOUTH)), (again, _press(room, second))],
            stop=lambda: room.claim(second) is not None,
        )
        second.up(BTN_SOUTH)

        two = room.claim(second)
        assert two is not None, "the second pad pressed again and was still not seated"
        assert two[1]["player"] == 2
        first_press = min(at for at, label in room.marks if label == f"down:{second.name}")
        print(f"\nthe second person's seat took {two[0] - first_press:.2f} s from their first press")
        if two[0] > again:
            late = two[0] - again - HOLD
            assert -EARLY <= late <= CLAIM_SLACK, f"the press again claimed {late * 1000:+.0f} ms off its hold"


def test_letting_go_loses_your_place(daemon):
    """First down, second down, first lets go and presses again: the second
    is now ahead. The readings say so while both fill -- the seat each is
    filling towards -- and the seats go that way."""
    with _pads(2) as (first, second):
        room = _open(daemon)
        start = room.now() + 0.05
        room.run(
            start + STAGGER + HOLD + CLAIM_SLACK + 1.0,
            [
                (start, _press(room, first)),
                (start + STAGGER, _press(room, second)),
                (start + 0.6, lambda: first.up(BTN_SOUTH)),
                (start + 0.8, _press(room, first)),
            ],
            stop=lambda: room.claim(second) is not None,
        )
        # Straight away: with padmap fixed the first pad would claim half a
        # second from now on its own, and this test is about the order.
        first.up(BTN_SOUTH)
        second.up(BTN_SOUTH)

        two = room.claim(second)
        assert two is not None and two[1]["player"] == 1, f"the pad that kept holding was not seated first: {two}"
        assert room.claim(first) is None, "the pad that let go was seated ahead of the one that kept holding"
        let_go = [f for at, f, _ in room.readings(first, start + 0.6) if at < start + 0.8 + 0.1]
        assert 0.0 in let_go, "letting go said nothing for that pad"

        both = [(at, e) for at, e in room.events("progress", start + 0.85) if at < two[0] and e.get("frac", 0) > 0]
        ahead = {e.get("player") for _, e in both if e.get("name") == second.name}
        behind = {e.get("player") for _, e in both if e.get("name") == first.name}
        assert ahead == {1} and behind == {2}, f"while both filled: second towards {ahead}, first towards {behind}"

        room.listening(two)
        _, _, claim = _seat_alone(room, first)
        assert claim["player"] == 2


# --- things that happen mid-hold -----------------------------------------------


def _held_while(daemon, pad: FakePad, poke: dict) -> tuple[Room, float, tuple[float, dict] | None]:
    """One pad held, `poke` sent 0.7 s into it: (room, start, its claim)."""
    room = _open(daemon)
    start = room.now() + 0.05
    room.run(
        start + HOLD + CLAIM_SLACK + 1.0,
        [(start, _press(room, pad)), (start + 0.7, lambda: daemon.send(poke))],
        stop=lambda: room.claim(pad) is not None,
    )
    pad.up(BTN_SOUTH)
    return room, start, room.claim(pad)


def test_a_state_mid_hold_resets_nothing(daemon):
    """padmap restates the world while buttons are down -- a pad arriving, a
    republish, a client asking. A `state` is news, not a reset."""
    with _pads(1) as (pad,):
        room, start, claim = _held_while(daemon, pad, {"cmd": "status"})
        assert room.events("state", start + 0.7), "asked for a state mid-hold and none came"
        assert claim is not None, "a status mid-hold cost the hold its seat"
        late = claim[0] - room.pressed(pad) - HOLD
        assert -EARLY <= late <= CLAIM_SLACK, f"the hold claimed {late * 1000:+.0f} ms off its length"
        fills = [f for _, f, _ in room.readings(pad) if f > 0]
        assert fills == sorted(fills), f"the fill went backwards: {fills}"


def test_seating_sent_again_with_the_same_hold_resets_nothing(daemon):
    """The picker sent `seating` again about 200 ms after every claim (the
    trace: `sent seating 1.5` after each one; assign.Watch no longer does),
    and a gate still sends it when it opens -- the launch door, while somebody
    in the room is holding. Same length, so nobody's hold should change."""
    with _pads(1) as (pad,):
        poke = {"cmd": "seating", "open": True, "players": 4, "hold": HOLD}
        room, _, claim = _held_while(daemon, pad, poke)
        assert claim is not None, "seating sent again with the same hold threw the hold away"
        late = claim[0] - room.pressed(pad) - HOLD
        assert -EARLY <= late <= CLAIM_SLACK, f"the hold restarted: {late * 1000:+.0f} ms off its length"


def test_a_controller_switched_on_mid_hold_resets_nobody(daemon):
    """Somebody switches a pad on while somebody else is holding. The long
    hold gives padmap's once-a-second scan time to notice the newcomer well
    before the first hold is due."""
    with _pads(1) as (pad,), contextlib.ExitStack() as later:
        room = _open(daemon, hold=4.0)
        start = room.now() + 0.05
        room.run(
            start + 4.0 + CLAIM_SLACK + 1.0,
            [
                (start, _press(room, pad)),
                (start + 0.3, lambda: later.enter_context(FakePad("PAIR Pad Late", 0x2A30, 0x5B30, 1))),
            ],
            stop=lambda: room.claim(pad) is not None,
        )
        pad.up(BTN_SOUTH)
        claim = room.claim(pad)
        assert claim is not None, "a controller switched on mid-hold threw the hold away"
        late = claim[0] - room.pressed(pad) - 4.0
        assert -EARLY <= late <= CLAIM_SLACK, f"the hold restarted: {late * 1000:+.0f} ms off its length"


@ITEM_1_REOPEN
def test_a_press_made_while_a_claim_republishes_is_not_lost(daemon):
    """Somebody is seated; the next person was already reaching for a pad and
    presses the moment the seat lights up. The daemon is still republishing
    the room then -- 0.6-0.9 s for a third seat on the pod -- and at this pin
    the pad's fd is closed and reopened after it, taking the press with it.
    The thumb stays down, no new edge comes, and the hold never starts: the
    person has to let go and press again. This was the wrong seat order the
    pressing-again test first saw on the cluster."""
    with _pads(4) as (a, b, c, d):
        room = _open(daemon)
        _seat_in_turn(room, [a, b])
        # Held again if its press was lost -- to the very bug measured below,
        # when it landed while the second seat was still being handled -- but
        # not waited out afterwards: the fourth press has to land inside the
        # third claim's handling.
        claim = None
        for _ in range(3):
            start = room.now()
            _press(room, c)()
            room.run(start + HOLD + 2.0, stop=lambda start=start: room.claim(c, start) is not None)
            c.up(BTN_SOUTH)
            claim = room.claim(c, start)
            if claim is not None:
                break
            room.settle()
        if claim is None:
            pytest.fail("the third pad held three times with nothing else going on, and was never seated")
        room.run(claim[0] + 0.05)
        if room.seated_by(3, claim[0]) is not None:
            pytest.skip("the republish was over before a press could land inside it; nothing to measure here")
        pressed = room.now()
        _press(room, d)()
        room.run(pressed + LISTENING_WITHIN + HOLD + CLAIM_SLACK, stop=lambda: room.claim(d, pressed) is not None)
        d.up(BTN_SOUTH)
        assert room.claim(d, pressed) is not None, (
            "a pad pressed while the last claim republished was never read: its hold never started"
        )


# --- a full room ----------------------------------------------------------------


def test_a_full_room_seats_nobody_else_and_keeps_playing(daemon):
    """Four seats, four people, and a fifth picks up a spare pad and holds it.
    Nobody new is seated, no fifth clone appears, and the four keep playing."""
    with _pads(5) as pads:
        seated, spare = pads[:4], pads[4]
        room = _open(daemon)
        _seat_in_turn(room, seated)
        _awake(room, spare)

        start = room.now()
        _press(room, spare)()
        room.run(start + HOLD + 1.5)
        spare.up(BTN_SOUTH)
        room.settle(0.5)
        _refused_at(room, spare, start)

        assert room.claim(spare) is None, f"a fifth pad was seated in a room of four: {room.claim(spare)}"
        assert sorted(p.get("player") for p in room.players()) == [1, 2, 3, 4]
        assert "padmap Player 5" not in kernel_names()
        for player, pad in enumerate(seated, 1):
            clone = _clone_info(player)
            assert clone, f"player {player} has no clone after the fifth pad's hold"
            fd = os.open(clone[0], os.O_RDONLY | os.O_NONBLOCK)
            try:
                assert _reaches(pad, fd), f"player {player}'s presses stopped reaching the game"
            finally:
                os.close(fd)


def _full_room(daemon, pads: list[FakePad]) -> Room:
    room = _open(daemon)
    _seat_in_turn(room, pads[:4])
    return room


def test_a_full_room_says_so(daemon):
    """A fill that reaches the end and then simply stops is a pad that looks
    broken. padmap names the pad in a `full` event, and takes its fill down."""
    with _pads(5) as pads:
        room = _full_room(daemon, pads)
        spare = pads[4]
        _awake(room, spare)
        start = room.now()
        _press(room, spare)()
        room.run(start + HOLD + 1.5, stop=lambda: bool(room.events("full", start)))
        spare.up(BTN_SOUTH)
        room.settle(0.3)
        _refused_at(room, spare, start)

        full = [e for _, e in room.events("full", start)]
        assert full, "a fifth pad held to the end in a full room, and nothing said why it was not seated"
        assert full[0].get("name") == spare.name, f"the wrong pad was told: {full[0]}"


def test_a_full_room_refuses_one_hold_not_every_hold(daemon):
    """Two spare pads, half a second apart. The first is refused; the second
    goes on filling until it is refused in its own right -- the fifth person
    at the party must not cancel the sixth."""
    with _pads(6) as pads:
        room = _full_room(daemon, pads)
        fifth, sixth = pads[4], pads[5]
        _awake(room, fifth, sixth)
        start = room.now() + 0.05
        room.run(
            start + 0.5 + HOLD + 1.0,
            [(start, _press(room, fifth)), (start + 0.5, _press(room, sixth))],
        )
        fifth.up(BTN_SOUTH)
        sixth.up(BTN_SOUTH)

        # Measured from the fifth's own refusal, not from the clock: the
        # sixth is about two thirds full then, with half a second to go.
        refused = _refused_at(room, fifth, start)
        before = [f for at, f, _ in room.readings(sixth, start) if at <= refused and f > 0]
        after = [f for at, f, _ in room.readings(sixth, start) if at > refused + 0.05 and f > 0]
        if not before or max(before) >= 0.95:
            pytest.fail(f"the sixth pad was not mid-hold when the fifth was refused: {before[-3:]}")
        assert after and max(after) >= max(before) + 0.2, (
            f"the sixth pad's fill stopped at {max(before):.2f} when the fifth was refused"
        )


# --- joining mid-game -------------------------------------------------------------


def test_a_pad_held_mid_game_is_seated_and_reaches_the_game_inside_a_frame(daemon):
    """The overlay's case. Seating was opened once, before the game, and
    nobody has touched it since; the only client is watching. A pad held
    mid-level takes the next seat, its clone appears, and its presses reach
    the game as fast as anybody's."""
    with _pads(2) as (first, second):
        room = _open(daemon)
        _seat_alone(room, first)

        start = room.now()
        _press(room, second)()
        room.run(start + HOLD + 2.0, stop=lambda: room.claim(second) is not None)
        second.up(BTN_SOUTH)
        joined = room.claim(second)
        assert joined is not None, "a pad held mid-game took no seat"
        assert joined[1]["player"] == 2
        late = joined[0] - start - HOLD
        assert -EARLY <= late <= CLAIM_SLACK, f"the join came {late * 1000:+.0f} ms off the hold"

        clone = _wait_clone(2, PUBLISH_WITHIN)
        assert clone, f"seated mid-game, and no clone within {PUBLISH_WITHIN} s"
        time.sleep(0.3)
        fd = os.open(clone[0], os.O_RDONLY | os.O_NONBLOCK)
        try:
            latencies = _tap_latencies(second, fd)
        finally:
            os.close(fd)
        assert len(latencies) > 60, f"only {len(latencies)} of 80 presses reached the new player's clone"
        p95 = _percentile(latencies, 0.95)
        assert p95 < A_FRAME_MS, (
            f"a press on the pad that joined mid-game took {p95:.1f} ms (p50 "
            f"{_percentile(latencies, 0.5):.1f}); a frame is 16.7 ms"
        )


def _tap_through(
    pad: FakePad, player: int, newcomer: int, seconds: float, every: float = 0.02
) -> tuple[list[float], float | None]:
    """Press and release `pad` every `every` seconds for `seconds`, reading
    whichever clone is player `player` right now -- reopening it when it is
    made again. (arrival times, when `padmap Player <newcomer>` appeared)."""
    arrived: list[float] = []
    appeared = None
    fd, identity = None, None
    value = 1
    end = time.monotonic() + seconds
    try:
        while time.monotonic() < end:
            clone = _clone_info(player)
            if clone and clone[1] != identity:
                if fd is not None:
                    os.close(fd)
                fd, identity = None, None
                try:
                    fd, identity = os.open(clone[0], os.O_RDONLY | os.O_NONBLOCK), clone[1]
                    _flush(fd)
                except OSError:
                    if fd is not None:
                        os.close(fd)
                    fd, identity = None, None
            if appeared is None and _clone_info(newcomer):
                appeared = time.monotonic()
            sent = time.monotonic()
            pad.down(BTN_SOUTH) if value else pad.up(BTN_SOUTH)
            if fd is not None:
                got = _pressed_on(fd, value, every)
                if got is not None:
                    arrived.append(got)
                elif not os.path.exists(f"/sys{identity}"):
                    os.close(fd)
                    fd, identity = None, None
            value ^= 1
            time.sleep(max(0.0, every - (time.monotonic() - sent)))
    finally:
        pad.up(BTN_SOUTH)
        if fd is not None:
            os.close(fd)
    return arrived, appeared


def test_the_players_already_in_the_game_barely_notice_a_join(daemon):
    """Player one is pressing away when player two joins. At this pin the
    join makes every clone again, so player one's input stops for a moment;
    that moment is measured and bounded, and said on every run."""
    with _pads(2) as (first, second):
        room = _open(daemon)
        _seat_alone(room, first)
        if not _wait_clone(1, PUBLISH_WITHIN):
            pytest.fail("player one was seated and has no clone")

        pressed = time.monotonic()
        second.down(BTN_SOUTH)
        try:
            arrived, appeared = _tap_through(first, 1, 2, HOLD + 2.5)
        finally:
            second.up(BTN_SOUTH)

        assert appeared is not None, "player two never joined"
        assert appeared - pressed <= HOLD + CLAIM_SLACK + PUBLISH_WITHIN, "the join took too long to appear"
        around = [at for at in arrived if pressed <= at]
        assert len(around) > 40, f"only {len(around)} of player one's presses arrived at all"
        gaps = [b - a for a, b in zip(around, around[1:], strict=False)]
        worst = max(gaps)
        print(f"\nplayer one's longest silence while player two joined: {worst * 1000:.0f} ms")
        assert worst <= REJOIN_WITHIN, f"player one's presses stopped for {worst:.2f} s while player two joined"


def test_a_join_leaves_the_players_already_in_the_game_plugged_in(daemon):
    """A game that opened player one's device keeps it when player two joins.
    Plenty of emulators never reopen a controller, and to them a clone made
    again is a controller unplugged."""
    with _pads(2) as (first, second):
        room = _open(daemon)
        _seat_alone(room, first)
        before = _wait_clone(1, PUBLISH_WITHIN)
        if not before:
            pytest.fail("player one was seated and has no clone")
        fd = os.open(before[0], os.O_RDONLY | os.O_NONBLOCK)
        try:
            if not _reaches(first, fd):
                pytest.fail("player one's presses did not reach its clone even before the join")
            _seat_alone(room, second)
            after = _clone_info(1)
            assert after is not None and after[1] == before[1], (
                f"player two joining made player one's clone again: {before[1]} -> {after and after[1]}"
            )
            assert _reaches(first, fd), "the game's handle on player one went dead when player two joined"
        finally:
            os.close(fd)


# --- performance ------------------------------------------------------------------


def _join_one_by_one(room: Room, pads: list[FakePad]) -> tuple[list[float], list[float], list[float]]:
    """Seat each pad alone, in turn: (claim over the hold, claim -> state,
    claim -> the new player's clone) per seat, printed."""
    late, to_state, to_clone = [], [], []
    for player, pad in enumerate(pads, 1):
        start = room.now()
        _press(room, pad)()
        room.run(start + HOLD + 2.0, stop=lambda pad=pad, start=start: room.claim(pad, start) is not None)
        claim = room.claim(pad, start)
        if claim is None:
            pad.up(BTN_SOUTH)
            pytest.fail(f"{pad.name} held alone for {HOLD + 2.0} s and was not seated")
        late.append(claim[0] - start - HOLD)
        clone = _wait_clone(player, PUBLISH_WITHIN)
        to_clone.append(room.now() - claim[0] if clone else float("inf"))
        room.listening(claim)
        to_state.append(room.seated_by(player, claim[0]) - claim[0])
        pad.up(BTN_SOUTH)
        room.settle(0.2)
    print(
        "\nhold -> claim over the hold: "
        + ", ".join(f"{x * 1000:.0f}" for x in late)
        + " ms; claim -> state: "
        + ", ".join(f"{x * 1000:.0f}" for x in to_state)
        + " ms; claim -> clone: "
        + ", ".join(f"{x * 1000:.0f}" for x in to_clone)
        + " ms"
    )
    return late, to_state, to_clone


def test_a_hold_claims_at_its_length_and_is_published_promptly(daemon):
    """Four people, one after another, nothing else in flight: each claim is
    the hold plus a little, the new player's clone follows at once, and the
    first seat's `state` promptly. How later seats' `state` grows is the
    next test's, and a known defect."""
    with _pads(4) as pads:
        room = _open(daemon)
        late, to_state, to_clone = _join_one_by_one(room, pads)
        assert min(late) >= -EARLY, f"a hold claimed before its length: {min(late) * 1000:+.0f} ms"
        assert max(late) <= CLAIM_SLACK, f"a claim came {max(late) * 1000:.0f} ms after its hold"
        assert statistics.median(late) <= CLAIM_SLACK_TYPICAL, (
            f"claims typically {statistics.median(late) * 1000:.0f} ms after their hold"
        )
        assert to_state[0] <= PUBLISH_WITHIN, f"the first seat's state took {to_state[0]:.2f} s"
        assert max(to_clone) <= PUBLISH_WITHIN, f"a claim's clone took {max(to_clone):.2f} s"


@JOIN_COST
def test_a_join_costs_the_same_however_full_the_room(daemon):
    """The fourth person's seat reaches the screen as fast as the first's.

    On the pod at the pin: 274, 594, 869, 1462 ms from claim to `state`, while
    the new player's own clone appeared 1, 75, 165, 230 ms after the claim --
    the whole room's clones made again one by one (the new player's last),
    then every seated player's autoconfig and SDL mapping written, and only
    then `state`. Until it arrives the picker's strip does not show the seat,
    and seating is not listening to anybody else.
    """
    with _pads(4) as pads:
        room = _open(daemon)
        _, to_state, _ = _join_one_by_one(room, pads)
        assert to_state[-1] - to_state[0] <= STATE_GROWTH, (
            f"the fourth seat's state came {(to_state[-1] - to_state[0]) * 1000:.0f} ms later than the first's"
        )


def test_four_holds_at_once_are_each_read_often_enough(daemon):
    """Four rings on screen at once, each drawn from its own readings. The
    longest silence for any pad has to stay inside the 0.5 s after which the
    overlay and the picker take a fill down (`NAMED_STALE`); the typical one
    has to be a few frames. And each fills towards the seat its press order
    gives it. The hold is long so nothing claims while this listens."""
    with _pads(4) as pads:
        order = list(reversed(pads))
        room = _open(daemon, hold=5.0)
        start = room.now() + 0.05
        listen = start + 3.3
        room.run(listen, [(start + 0.1 * i, _press(room, pad)) for i, pad in enumerate(order)])
        for pad in pads:
            pad.up(BTN_SOUTH)
        room.settle(0.3)
        if room.claims():
            pytest.fail(f"a 5 s hold claimed inside 3.3 s: {room.claims()}")

        report = []
        for seat, pad in enumerate(order, 1):
            pressed = room.pressed(pad)
            held = [(at, f, p) for at, f, p in room.readings(pad, pressed) if f > 0 and at <= listen]
            assert len(held) > 20, f"{pad.name}: only {len(held)} readings in {listen - pressed:.1f} s of holding"
            first = held[0][0] - pressed
            gaps = [b[0] - a[0] for a, b in zip(held, held[1:], strict=False)]
            report.append(f"{pad.name}: first {first * 1000:.0f}, median {statistics.median(gaps) * 1000:.0f}, "
                          f"worst {max(gaps) * 1000:.0f} ms")
            assert first <= FIRST_READING_WITHIN, f"{pad.name}'s ring took {first * 1000:.0f} ms to start"
            assert max(gaps) < NAMED_STALE, (
                f"{pad.name} went {max(gaps) * 1000:.0f} ms without a reading; the screen drops it at "
                f"{NAMED_STALE * 1000:.0f}"
            )
            assert statistics.median(gaps) <= CADENCE_MEDIAN, (
                f"{pad.name}'s readings typically {statistics.median(gaps) * 1000:.0f} ms apart"
            )
            fracs = [f for _, f, _ in held]
            assert fracs == sorted(fracs), f"{pad.name}'s fill went backwards"
            assert {p for _, _, p in held} == {seat}, f"{pad.name} pressed {seat}th, filling towards {held[0][2]}"
        print("\n" + "\n".join(report))


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="p95 under four holds went from ~15 ms (d2000c5) to ~16.9 ms (e0092be), past a frame; "
    "docs/requests/holds-cost-a-seated-pad-nothing.md",
)
def test_four_holds_at_once_cost_a_seated_pad_nothing(daemon):
    """Somebody is playing while four others pick up pads and hold. Their
    holds are four readings a tick on the daemon's loop; the player's presses
    share that loop and must not feel it."""
    with _pads(5) as pads:
        playing, others = pads[0], pads[1:]
        room = _open(daemon)
        _seat_alone(room, playing)
        clone = _wait_clone(1, PUBLISH_WITHIN)
        if not clone:
            pytest.fail("the player was seated and has no clone")
        # A long hold now, so the four fill for the whole measurement and none
        # claims -- a claim republishes, which is a different test. Ten
        # seconds is padmap's longest (HOLD_RANGE); anything longer is quietly
        # taken as its 0.25 s default, which is how this once measured a
        # republish tearing the player's clone down instead.
        daemon.send({"cmd": "seating", "open": True, "players": 4, "hold": 10.0})
        room.settle(0.5)
        fd = os.open(clone[0], os.O_RDONLY | os.O_NONBLOCK)
        try:
            quiet = _tap_latencies(playing, fd, taps=60)
            start = room.now()
            for pad in others:
                _press(room, pad)()
            room.settle(0.3)
            busy = _tap_latencies(playing, fd, taps=60)
            room.settle(0.2)
        finally:
            for pad in others:
                pad.up(BTN_SOUTH)
            os.close(fd)

        if room.claims(start):
            pytest.fail(f"a 10 s hold claimed during a 2 s measurement: {room.claims(start)}")
        filling = [pad.name for pad in others if any(f > 0 for _, f, _ in room.readings(pad, start))]
        if len(filling) != len(others):
            pytest.fail(f"only {filling} were filling -- the load this measures was not there")
        assert len(quiet) > 45 and len(busy) > 45, f"presses lost: {len(quiet)} quiet, {len(busy)} busy of 60"
        quiet50, busy50, busy95 = _percentile(quiet, 0.5), _percentile(busy, 0.5), _percentile(busy, 0.95)
        print(f"\na seated pad's press: quiet p50 {quiet50:.2f} ms; four holding p50 {busy50:.2f}, p95 {busy95:.2f} ms")
        assert busy95 < A_FRAME_MS, f"with four people holding, a press took {busy95:.1f} ms; a frame is 16.7"
        assert busy50 <= quiet50 + LOAD_SLACK_MS, (
            f"four holds put {busy50 - quiet50:.2f} ms on a seated pad's typical press"
        )
