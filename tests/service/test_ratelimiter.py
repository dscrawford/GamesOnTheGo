"""The upstream pace, against a clock the test owns.

The earlier tests in test_artcache.py ran on the wall clock and could only say
"a delay between 0 and half a second"; with the clock injected the refill
arithmetic can be asserted exactly.
"""

from __future__ import annotations

import pytest

from gotg.service.ratelimit import RateLimiter


class Clock:
    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_the_bucket_starts_full_and_a_burst_costs_nothing():
    clock = Clock()
    limiter = RateLimiter(rate=1.0, burst=3.0, now=clock)
    assert [limiter.reserve(0.0) for _ in range(3)] == [(True, 0.0)] * 3


def test_the_next_slot_is_a_whole_interval_away_when_the_bucket_is_empty():
    clock = Clock()
    limiter = RateLimiter(rate=2.0, burst=1.0, now=clock)
    limiter.reserve(0.0)
    assert limiter.reserve(10.0) == (True, pytest.approx(0.5))


def test_reservations_queue_rather_than_share_a_slot():
    clock = Clock()
    limiter = RateLimiter(rate=1.0, burst=1.0, now=clock)
    limiter.reserve(0.0)
    delays = [limiter.reserve(10.0)[1] for _ in range(3)]
    assert delays == [pytest.approx(1.0), pytest.approx(2.0), pytest.approx(3.0)]


def test_time_refills_the_bucket_at_the_rate_up_to_the_burst():
    clock = Clock()
    limiter = RateLimiter(rate=1.0, burst=2.0, now=clock)
    limiter.reserve(0.0)
    limiter.reserve(0.0)
    clock.advance(1.0)
    assert limiter.reserve(0.0) == (True, 0.0)
    assert limiter.reserve(0.0)[0] is False
    clock.advance(3600.0)  # an hour is still only a burst's worth
    assert [limiter.reserve(0.0) for _ in range(2)] == [(True, 0.0)] * 2
    assert limiter.reserve(0.0)[0] is False


def test_a_refused_reservation_takes_nothing():
    clock = Clock()
    limiter = RateLimiter(rate=1.0, burst=1.0, now=clock)
    limiter.reserve(0.0)
    assert limiter.reserve(0.5) == (False, pytest.approx(1.0))
    assert limiter.reserve(0.5) == (False, pytest.approx(1.0)), "asking again is not charged"
    clock.advance(1.0)
    assert limiter.reserve(0.0) == (True, 0.0)


def test_the_wait_the_caller_will_hold_is_inclusive():
    clock = Clock()
    limiter = RateLimiter(rate=1.0, burst=1.0, now=clock)
    limiter.reserve(0.0)
    assert limiter.reserve(1.0)[0] is True


def test_a_zero_rate_is_no_limit_at_all():
    clock = Clock()
    limiter = RateLimiter(rate=0.0, burst=1.0, now=clock)
    assert [limiter.reserve(0.0) for _ in range(50)] == [(True, 0.0)] * 50


def test_a_negative_rate_and_a_tiny_burst_are_clamped():
    limiter = RateLimiter(rate=-5.0, burst=0.0, now=Clock())
    assert limiter.rate == 0.0
    assert limiter.burst == 1.0


def test_the_default_clock_is_the_monotonic_one():
    limiter = RateLimiter(rate=1.0, burst=1.0)
    assert limiter.reserve(0.0) == (True, 0.0)
