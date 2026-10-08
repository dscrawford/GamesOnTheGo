"""What is installed and what is behind, asked off the loop and landed as a value.

The seam is the answer's arrival: the question is two
subprocesses and a walk of the disk -- a quarter of a second on a desk, more on
a Deck, which is frames the grid would drop (CLAUDE.md's budget is 16.7 ms) --
so it is asked on one worker (`Asker`), and what to do with the answer when it
lands is a pure function (`land`) of the state so far and the answer.

No pygame; the worker's two questions are injected, so the tests ask nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass

from . import trace, updates
from .installed import installed_games

Answer = tuple[set[tuple[str, str]], "updates.Report | None"]


def ask_everything() -> Answer:
    """What is on disk, and what the client says is out of date. Runs on the
    worker: both are slow."""
    return installed_games(), updates.ask()


@dataclass(frozen=True)
class Badges:
    """What the loop has learned so far: the client's last report, and whether
    the check that refreshes its cache has been started. Once the client has
    answered it is known to speak `--check`; before that an older client would
    read `update` with arguments as the full rebuild."""

    report: updates.Report | None = None
    check_started: bool = False


@dataclass(frozen=True)
class Landed:
    """An answer applied: the new state, what the browser should now show, and
    whether the `--check` should be started behind the grid."""

    badges: Badges
    installed: set[tuple[str, str]]
    outdated: frozenset[tuple[str, str]]
    start_check: bool


def land(badges: Badges, here: set[tuple[str, str]], said: updates.Report | None) -> Landed:
    """Fold one answer into the state. A game the report names that is no
    longer installed is not outdated (`updates.outdated`)."""
    start = not badges.check_started and said is not None
    return Landed(
        Badges(said, badges.check_started or start),
        set(here),
        updates.outdated(said, here),
        start,
    )


class Asker:
    """One question in flight at a time, answered at most once.

    Asking again while one is pending replaces it -- the later question is the
    truer one. `poll()` never raises: a badge must never stop the grid.
    """

    def __init__(
        self,
        ask: Callable[[], Answer] = ask_everything,
        pool: ThreadPoolExecutor | None = None,
    ) -> None:
        self._ask = ask
        self._pool = pool or ThreadPoolExecutor(max_workers=1, thread_name_prefix="badges")
        self._pending: Future | None = None

    def refresh(self) -> None:
        self._pending = self._pool.submit(self._ask)

    def poll(self):
        """The answer, once, when it has landed; None while it has not, when
        nothing was asked, or when asking failed (a line in the trace)."""
        if self._pending is None or not self._pending.done():
            return None
        pending, self._pending = self._pending, None
        try:
            return pending.result()
        except Exception as error:  # noqa: BLE001 - a badge must never stop the grid
            trace.say("badges-failed", why=str(error))
            return None

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
