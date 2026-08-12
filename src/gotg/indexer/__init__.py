"""GOTG importer — organizes completed game torrents into the canonical /Games tree.

The pipeline is deliberately split so everything except one thin I/O layer is pure
and testable off-cluster:

    scan (I/O)  ->  classify  ->  plan  ->  execute (I/O)

``slugify`` and ``plan`` are the validated reference implementation and are kept
verbatim; see ``Kubernetes/games/IMPORTER_SPEC.md`` for the full contract.
"""

__version__ = "0.1.0"
