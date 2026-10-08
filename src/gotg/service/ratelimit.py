"""The token bucket in front of an upstream.

Pure arithmetic over an injectable clock, tested without a server
(tests/service/test_ratelimiter.py); the handler only needs to know it as
`reserve(wait)`.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


class RateLimiter:
    """A token bucket in front of one upstream, shared by every thread.

    The proxy holds one key for a fleet, and warming the whole library is
    thousands of questions in a row: without a pace somewhere, one warm run
    is a burst against a quota that is not ours to spend, and the answer to
    that is a banned key rather than a slower client.

    Slots are *reserved* rather than waited for under the lock — the caller
    is told how long to sleep and sleeps on its own thread — so a paced
    request costs the proxy one idle thread, never the bucket.
    """

    def __init__(self, rate: float, burst: float, now: Callable[[], float] = time.monotonic):
        # `now` is a test seam: the refill arithmetic is asserted against a
        # clock the test advances, instead of a sleep and a tolerance.
        self.rate = max(rate, 0.0)
        self.burst = max(burst, 1.0)
        self._now = now
        self.tokens = self.burst
        self.updated = now()
        self.lock = threading.Lock()

    def reserve(self, wait: float) -> tuple[bool, float]:
        """(may it go, how long first). Refused when the wait would be longer
        than `wait`, which is a caller who should hear 429 and come back
        rather than hold a thread open for a minute."""
        if not self.rate:
            return True, 0.0
        now = self._now()
        with self.lock:
            self.tokens = min(self.burst, self.tokens + (now - self.updated) * self.rate)
            self.updated = now
            delay = 0.0 if self.tokens >= 1.0 else (1.0 - self.tokens) / self.rate
            if delay > wait:
                return False, delay
            # Into the negative on purpose: the slot is taken now and paid for
            # by the sleep, so two threads cannot reserve the same one.
            self.tokens -= 1.0
        return True, delay
