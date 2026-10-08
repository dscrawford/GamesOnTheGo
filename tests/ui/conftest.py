"""Shared stand-ins for the tests of the picker."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

_SCRIPT = """#!{python}
import json, sys
root = {root!r}
with open(root + "/calls", "a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
best = None
for row in json.load(open(root + "/table")):
    prefix = row["argv"]
    if sys.argv[1:1 + len(prefix)] == prefix and (best is None or len(prefix) > len(best["argv"])):
        best = row
if best is None:
    sys.exit(0)
sys.stdout.write(best["stdout"])
sys.stderr.write(best["stderr"])
sys.exit(best["code"])
"""


@dataclass(frozen=True)
class FakeClient:
    """A `gotg` on GOTG_BIN that records argv and answers from a table.

    `answer(*prefix, stdout=..., code=..., stderr=...)` says what the client
    prints when its argv starts with `prefix` (the longest prefix wins; an
    argv nothing matches exits 0 with no output, as an older client does for a
    subcommand it does not know). It replaced a shell stub written out again
    in each test module, each with its own way of logging the arguments.
    """

    root: Path

    @property
    def path(self) -> str:
        return str(self.root / "gotg")

    def answer(self, *prefix: str, stdout: str = "", code: int = 0, stderr: str = "") -> None:
        table = self.root / "table"
        # The same prefix said twice is the later word: a test changes the
        # client's mind between two asks.
        rows = [row for row in json.loads(table.read_text()) if row["argv"] != list(prefix)]
        rows.append({"argv": list(prefix), "stdout": stdout, "code": code, "stderr": stderr})
        table.write_text(json.dumps(rows))

    @property
    def calls(self) -> list[list[str]]:
        log = self.root / "calls"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


@pytest.fixture
def fake_client(tmp_path, monkeypatch) -> FakeClient:
    root = tmp_path / "fake-client"
    root.mkdir()
    (root / "table").write_text("[]")
    script = root / "gotg"
    script.write_text(_SCRIPT.format(python=sys.executable, root=str(root)))
    script.chmod(0o755)
    monkeypatch.setenv("GOTG_BIN", str(script))
    return FakeClient(root)
