"""The contract is one module; everything that must agree with it is pinned.

slugify keeps a verbatim copy of the entry-id pattern (the file is diffable
against its reference), the classifier re-exports the wire-legal handlers,
and the bash client cannot import Python at all — so each is asserted here,
the same pattern that already keeps rules.yaml and plan.py from drifting.
"""

import re
from pathlib import Path

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
    match = re.search(rf"^{name}='([^']+)'", source, re.M)
    assert match, f"{name} not found in {SHELL_COMMON}"
    return match.group(1)


def test_the_shell_client_carries_the_same_patterns():
    assert _shell_var("GOTG_ID_RE") == contract.ENTRY_ID_RE.pattern
    assert _shell_var("GOTG_PLATFORM_RE") == contract.PLATFORM_RE.pattern
