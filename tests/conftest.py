import sys
from pathlib import Path

_src = Path(__file__).resolve().parent.parent / "src"
# The service and indexer package, then the UI, which is deliberately not part
# of it — src/gotg ships inside the internet-facing image and a UI wants a
# toolkit, so gotg_ui lives beside it rather than under it.
sys.path.insert(0, str(_src))
sys.path.insert(0, str(_src / "ui"))
