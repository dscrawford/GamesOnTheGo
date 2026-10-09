"""The filters the picker had when it was last closed, kept on disk.

A platform narrowed to on Tuesday is the one wanted on Wednesday; opening on
the whole library every time was five presses before the first game. The
values are the browser's; this only writes them down and reads them back.
Nothing here is an error: a file that is missing, unreadable or from another
version leaves the picker where it started.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path

from . import trace
from .variants import state_dir

FIELDS = ("platform", "region", "presence", "search", "view")


def path() -> Path:
    return state_dir() / "picker.json"


def snapshot(browser) -> dict[str, str]:
    return {
        "platform": browser.platform,
        "region": browser.region,
        "presence": browser.presence,
        "search": browser.search,
        "view": browser.view,
    }


def apply(browser, data: dict) -> None:
    """Put the browser where the file says. Each setter already refuses a
    value it does not know, which is what makes a stale file harmless."""
    values = {k: v for k, v in data.items() if k in FIELDS and isinstance(v, str)}
    if "platform" in values:
        browser.set_platform(values["platform"])
    if "region" in values:
        browser.set_region(values["region"])
    if "presence" in values:
        browser.set_presence(values["presence"])
    if "search" in values:
        browser.set_search(values["search"])
    if "view" in values:
        browser.set_view(values["view"])


def load(where: Path) -> dict:
    try:
        data = json.loads(where.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(where: Path, data: dict) -> bool:
    """Written beside and renamed over, so a picker killed mid-write leaves
    the last file rather than half of this one."""
    tmp = where.with_suffix(".tmp")
    try:
        where.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, indent=1))
        os.replace(tmp, where)
    except OSError as error:
        trace.say("remember-failed", path=str(where), why=str(error))
        return False
    return True


@dataclass(frozen=True)
class Remembered:
    """What is on disk, so a frame can ask to keep the filters and it costs a
    write only when they changed."""

    where: Path
    last: dict

    @classmethod
    def open(cls, where: Path | None = None) -> Remembered:
        where = where or path()
        return cls(where, load(where))

    def keep(self, data: dict) -> Remembered:
        if data == self.last:
            return self
        save(self.where, data)
        return replace(self, last=data)
