"""Splitting the controller suite across the cluster's nodes.

One pod ran all of it, one test at a time, for twelve and a half minutes.
The tests cannot run side by side in one pod -- every fake pad is a real
device on the node, and every daemon a test starts would see, and seat, the
pads of every other test -- but a node is a separate /dev/input. So the Job
runs one pod per node and each takes a share (`GOTG_E2E_SHARD=i/n`).

The shares are balanced by what each test cost last time, `durations.json`
beside this file, longest first onto the lightest shard. A test that is not
in the file counts as the median of those that are, so a new test lands
somewhere sensible before its first timing. Every pod computes the same split
from the same collection, which is what makes the shares add up to the suite
exactly once.
"""

from __future__ import annotations

import json
import os
import statistics
from collections.abc import Iterable

DURATIONS = os.path.join(os.path.dirname(__file__), "durations.json")

# What a test with no timing on record is assumed to cost, when nothing is.
UNKNOWN = 10.0


def key(nodeid: str) -> str:
    """A test's name without the directory: pytest roots the ids at the
    checkout on a desktop and at tests/e2e in the image, which has no
    pyproject to find."""
    path, sep, rest = nodeid.partition("::")
    return os.path.basename(path) + sep + rest


def parse(value: str) -> tuple[int, int] | None:
    """`i/n` -> (i, n), zero-based; None for anything else, including 0/1."""
    try:
        index, count = (int(part) for part in value.split("/"))
    except ValueError:
        return None
    if count < 2 or not 0 <= index < count:
        return None
    return index, count


def load(path: str = DURATIONS) -> dict[str, float]:
    try:
        with open(path) as file:
            data = json.load(file)
    except (OSError, ValueError):
        return {}
    return {str(k): float(v) for k, v in data.items() if isinstance(v, int | float)}


def split(nodeids: Iterable[str], count: int, durations: dict[str, float]) -> list[list[str]]:
    """Every id in exactly one of `count` shares, each in collection order."""
    ids = list(nodeids)
    known = [durations[i] for i in ids if i in durations]
    guess = statistics.median(known) if known else UNKNOWN
    cost = {i: durations.get(i, guess) for i in ids}
    loads = [0.0] * count
    owner: dict[str, int] = {}
    # Longest first, ties by id, so every pod arrives at the same answer.
    for nodeid in sorted(ids, key=lambda i: (-cost[i], i)):
        share = min(range(count), key=lambda s: (loads[s], s))
        owner[nodeid] = share
        loads[share] += cost[nodeid]
    return [[i for i in ids if owner[i] == share] for share in range(count)]
