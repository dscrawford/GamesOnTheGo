"""The split of the suite across nodes; see shards.py. No devices needed."""

from __future__ import annotations

import json

from shards import key, load, parse, split


def test_a_share_is_read_as_index_over_count():
    assert parse("0/3") == (0, 3)
    assert parse("2/3") == (2, 3)


def test_a_test_is_known_by_its_file_and_name_wherever_pytest_was_rooted():
    assert key("tests/e2e/test_pairing.py::test_x") == "test_pairing.py::test_x"
    assert key("test_pairing.py::test_x") == "test_pairing.py::test_x"


def test_anything_that_is_not_a_real_share_means_the_whole_suite():
    for value in ("", "3/3", "-1/3", "0/1", "0/0", "a/b", "1"):
        assert parse(value) is None, value


def test_every_test_runs_exactly_once_across_the_shares():
    ids = [f"t{n}" for n in range(17)]
    shares = split(ids, 3, {"t3": 40.0, "t9": 5.0})
    assert sorted(i for share in shares for i in share) == sorted(ids)


def test_the_long_tests_are_spread_rather_than_stacked():
    durations = {"a": 40.0, "b": 40.0, "c": 40.0, "d": 1.0, "e": 1.0, "f": 1.0}
    shares = split(list(durations), 3, durations)
    assert all(sum(durations[i] for i in share) == 41.0 for share in shares)


def test_each_share_keeps_the_order_the_tests_were_collected_in():
    ids = ["z", "a", "m", "b"]
    for share in split(ids, 2, {}):
        assert share == [i for i in ids if i in share]


def test_every_pod_arrives_at_the_same_split():
    ids = [f"t{n}" for n in range(30)]
    durations = {f"t{n}": float(n % 7) for n in range(30)}
    assert split(ids, 3, durations) == split(list(ids), 3, dict(durations))


def test_a_test_with_no_timing_counts_as_a_typical_one():
    # Three long tests on record and a new one: the new one is not free, so
    # it does not all pile onto whichever share looks empty.
    durations = {"a": 30.0, "b": 30.0, "c": 30.0}
    shares = split(["a", "b", "c", "new"], 2, durations)
    loads = [sum(durations.get(i, 30.0) for i in share) for share in shares]
    assert loads == [60.0, 60.0]


def test_an_unreadable_file_is_no_timings(tmp_path):
    assert load(str(tmp_path / "missing.json")) == {}
    broken = tmp_path / "broken.json"
    broken.write_text("{")
    assert load(str(broken)) == {}
    mixed = tmp_path / "mixed.json"
    mixed.write_text(json.dumps({"a": 1.5, "b": "slow"}))
    assert load(str(mixed)) == {"a": 1.5}
