"""The contract is one module; everything that must agree with it is pinned.

slugify keeps a verbatim copy of the entry-id pattern (the file is diffable
against its reference), the classifier re-exports the wire-legal handlers,
and the bash client cannot import Python at all — so each is asserted here,
the same pattern that already keeps rules.yaml and plan.py from drifting.

String equality alone would miss a Python-only construct (\\d, (?:...), a
lazy quantifier) that compiles in re but not in bash's POSIX ERE — so the
shared strings are also run through bash's own engine against samples both
sides must judge identically.
"""

import os
import re
import subprocess
from pathlib import Path

import pytest

from gotg import contract
from gotg.indexer import classify, slugify

SHELL_COMMON = Path(__file__).resolve().parents[2] / "src" / "client" / "lib" / "common.sh"


def test_slugify_keeps_the_contract_pattern():
    assert slugify.ENTRY_RE.pattern == contract.ENTRY_ID_RE.pattern


def test_the_wire_legal_handlers_are_exactly_the_contract():
    publishable = {
        classify.HANDLER_SINGLE_FILE,
        classify.HANDLER_NO_INTRO_SET,
        classify.HANDLER_SCENE_ARCHIVE,
        classify.HANDLER_SINGLE_ARCHIVE,
        classify.HANDLER_WIIU_DECRYPTED,
        classify.HANDLER_WIIU_NUS,
    }
    assert publishable == contract.HANDLERS
    assert contract.LINK_HANDLERS < contract.HANDLERS
    assert classify.HANDLER_EXCLUDED not in contract.HANDLERS
    assert classify.HANDLER_MANUAL not in contract.HANDLERS


def _shell_var(name: str) -> str:
    source = SHELL_COMMON.read_text()
    # Either quote style: the pin is the value, not common.sh's formatting.
    match = re.search(rf"""^{name}=(['"])([^'"]+)\1""", source, re.M)
    assert match, f"{name} not found in {SHELL_COMMON}"
    return match.group(2)


def test_the_shell_client_carries_the_same_patterns():
    assert _shell_var("GOTG_ID_RE") == contract.ENTRY_ID_RE.pattern
    assert _shell_var("GOTG_PLATFORM_RE") == contract.PLATFORM_RE.pattern


def test_the_advertised_version_matches_the_package():
    import tomllib

    from gotg.service import app

    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    version = tomllib.loads(pyproject.read_text())["project"]["version"]
    assert app.USER_AGENT == f"gotg-proxy/{version}"


def _bash_matches(sample: str, pattern: str) -> bool:
    proc = subprocess.run(
        ["bash", "-c", '[[ "$1" =~ $2 ]]', "bash", sample, pattern],
        env={**os.environ, "LC_ALL": "C"},
    )
    # 2 means the pattern itself did not compile as an ERE — a drift bug.
    assert proc.returncode in (0, 1), f"bash rejected the pattern: {pattern!r}"
    return proc.returncode == 0


@pytest.mark.parametrize(
    "sample, valid",
    [
        ("usa.zelda", True),
        ("world.super_metroid", True),
        ("japan.mario_64", True),
        ("eur.a", True),
        ("us.zelda", False),  # region below the 3-letter floor
        ("usaxzelda", False),  # matches iff \. drifts into a wildcard dot
        ("usa.Zelda", False),
        ("usa._slug", False),
        ("usa.", False),
        ("usa.zelda!", False),
    ],
)
def test_bash_and_python_agree_on_entry_ids(sample, valid):
    pattern = _shell_var("GOTG_ID_RE")
    assert bool(contract.ENTRY_ID_RE.match(sample)) is valid
    assert _bash_matches(sample, pattern) is valid


@pytest.mark.parametrize(
    "sample, valid",
    [
        ("n64", True),
        ("gamecube", True),
        ("a", True),
        ("abcdefgh12345678", True),  # 16 chars: the cap
        ("abcdefgh123456789", False),  # 17: over it
        ("N64", False),
        ("-nes", False),
        ("_nes", False),
        ("nes!", False),
        ("", False),
    ],
)
def test_bash_and_python_agree_on_platforms(sample, valid):
    pattern = _shell_var("GOTG_PLATFORM_RE")
    assert bool(contract.PLATFORM_RE.match(sample)) is valid
    assert _bash_matches(sample, pattern) is valid


#
# catalog.py, saves.py and publish.py each spelled "now" as the same strftime
# call. The catalog's since/seen_at comparisons are lexicographic, so the spelling
# is part of the wire contract: second resolution, UTC, a literal Z.


def test_utc_now_is_the_fixed_width_stamp_the_catalog_sorts_on():
    assert re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", contract.utc_now())


@pytest.mark.parametrize(
    ("epoch", "expected"),
    [(0, "1970-01-01T00:00:00Z"), (1700000000, "2023-11-14T22:13:20Z"), (1700000000.9, "2023-11-14T22:13:20Z")],
)
def test_utc_now_of_a_given_instant_is_that_instant_in_utc(epoch, expected):
    assert contract.utc_now(epoch) == expected


def test_utc_now_follows_the_clock():
    import time

    before = time.time()
    stamp = contract.utc_now()
    assert contract.utc_now(before) <= stamp <= contract.utc_now(time.time() + 1)
