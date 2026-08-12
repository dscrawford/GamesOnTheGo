"""The contract every half of GOTG agrees on, written down exactly once.

The entry id, the platform slug, the member-name rules and the handler
vocabulary cross three boundaries: the indexer derives them from torrent
names, the service validates them at the door and serves them on the wire,
and the client turns them into local paths and recipes. Before this module
they were mirrored — the catalog's copy was literally commented "mirrored
from the importer" — and mirrored constants drift: the Game Boy library sat
unimported behind exactly that kind of split.

Stdlib only, and no imports from gotg.* — the platforms flake check imports
the indexer's planner on a bare python with nothing but PYTHONPATH, and this
module is on that path.

The bash client cannot import this. Its copies in src/client/lib/common.sh
are pinned to these by tests/indexer/test_contract.py, which greps the shell
source — the same drift-guard pattern that keeps rules.yaml and plan.py
agreeing.
"""

from __future__ import annotations

import re

# The entry id: <region>.<title_slug>[_<revision>]. slugify.py produces these;
# everything else recognizes them. slugify keeps its own verbatim copy (the
# file is diffable against its reference implementation) and a test asserts
# the two patterns are identical.
ENTRY_ID_RE = re.compile(r"^[a-z]{3,5}\.[a-z0-9][a-z0-9_]*$")

PLATFORM_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,15}$")

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# What a raw source *is*, which tells the client what recipe turns it into a
# runnable game. Only these six cross the wire; the classifier's internal
# verdicts (excluded, manual) never become catalog rows.
HANDLER_SINGLE_FILE = "single_file"
HANDLER_NO_INTRO_SET = "no_intro_set"
HANDLER_SCENE_ARCHIVE = "scene_archive"
HANDLER_SINGLE_ARCHIVE = "single_archive"
HANDLER_WIIU_DECRYPTED = "wiiu_decrypted"
HANDLER_WIIU_NUS = "wiiu_nus"

HANDLERS = frozenset(
    {
        HANDLER_SINGLE_FILE,
        HANDLER_NO_INTRO_SET,
        HANDLER_SCENE_ARCHIVE,
        HANDLER_SINGLE_ARCHIVE,
        HANDLER_WIIU_DECRYPTED,
        HANDLER_WIIU_NUS,
    }
)

# The handlers whose one member is the game itself, byte for byte — the ones
# the dual-publish diff compares by hash rather than by presence.
LINK_HANDLERS = frozenset({HANDLER_SINGLE_FILE, HANDLER_NO_INTRO_SET})


def _valid_segment(segment: str) -> bool:
    return 0 < len(segment) <= 255 and segment.isprintable() and not segment.startswith(".")


# A member name is a relative path: one segment for almost everything, nested
# for the trees an emulator reads in place (a WiiU dump's code/content/meta).
# No segment may start with a dot, which also rules out "..", and the depth
# cap means a hostile name cannot be a filesystem stress test.
def valid_filename(name: str) -> bool:
    if not 0 < len(name) <= 1024:
        return False
    segments = name.split("/")
    return len(segments) <= 8 and all(_valid_segment(s) for s in segments)
