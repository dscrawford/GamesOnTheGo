"""Run the proxy, configured entirely from the environment.

Environment rather than a file, because that is what a Kubernetes Secret
mounts most naturally, and because a config file holding credentials is one
more thing to get the permissions of wrong.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

from ..catalog import CatalogStore
from ..saves import DEFAULT_KEEP, DEFAULT_MAX_BYTES, SavesStore
from ..tokens import TokenStore
from .app import Config, make_server


def config_from_env(env: Mapping[str, str] | None = None) -> Config:
    env = os.environ if env is None else env
    return Config(
        token=env.get("GOTG_PROXY_TOKEN", ""),
        steamgriddb_key=env.get("STEAMGRIDDB_API_KEY", ""),
        igdb_client_id=env.get("IGDB_CLIENT_ID", ""),
        igdb_client_secret=env.get("IGDB_CLIENT_SECRET", ""),
        index_token=env.get("GOTG_INDEX_TOKEN", ""),
        admin_token=env.get("GOTG_ADMIN_TOKEN", ""),
        auth_url=env.get("GOTG_AUTH_URL", "").rstrip("/"),
        files_url=env.get("GOTG_FILES_URL", "").rstrip("/"),
        files_port_file=env.get("GOTG_FILES_PORT_FILE", ""),
    ).validate()


def token_store_from_env(env: Mapping[str, str] | None = None) -> TokenStore | None:
    env = os.environ if env is None else env
    db = env.get("GOTG_TOKENS_DB", "")
    return TokenStore(db=Path(db)) if db else None


def catalog_from_env(env: Mapping[str, str] | None = None) -> CatalogStore | None:
    """The catalog, or None: a deployment with no library roots is one that
    serves only the proxy and saves halves, and says so with a 503."""
    env = os.environ if env is None else env
    db = env.get("GOTG_CATALOG_DB", "")
    roots = [Path(r) for r in env.get("GOTG_LIBRARY_ROOTS", "").split(":") if r]
    if not db and not roots:
        return None
    if not (db and roots):
        raise ValueError("GOTG_CATALOG_DB and GOTG_LIBRARY_ROOTS are set together or not at all")
    return CatalogStore(db=Path(db), roots=roots)


def store_from_env(env: Mapping[str, str] | None = None) -> SavesStore | None:
    """The saves store, or None: a deployment with no data directory is one
    that serves only the proxy half, and says so with a 503."""
    env = os.environ if env is None else env
    root = env.get("GOTG_SAVES_DIR", "")
    if not root:
        return None
    return SavesStore(
        root=Path(root),
        keep=int(env.get("GOTG_SAVES_KEEP", str(DEFAULT_KEEP))),
        max_bytes=int(env.get("GOTG_SAVES_MAX_BYTES", str(DEFAULT_MAX_BYTES))),
    )


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    port = int(os.environ.get("PORT", "8080"))
    try:
        config = config_from_env()
        catalog = catalog_from_env()
        store = store_from_env()
        token_store = token_store_from_env()
        # A catalog row grants read on its path, so the library must not be
        # able to name what the service itself writes.
        if store and catalog:
            saves_root = Path(os.path.realpath(store.root))
            for root in catalog.roots:
                if saves_root.is_relative_to(root):
                    raise ValueError(f"the saves directory {store.root} is inside library root {root}")
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    files_dir = Path(os.environ["GOTG_FILES_DIR"]) if os.environ.get("GOTG_FILES_DIR") else None
    try:
        stream_slots = int(os.environ.get("GOTG_STREAM_SLOTS", "4"))
        if stream_slots < 1:
            raise ValueError(stream_slots)
    except ValueError:
        print("error: GOTG_STREAM_SLOTS must be a positive integer", file=sys.stderr)
        return 1
    server = make_server(  # noqa: S104 — a container listens on all of its own
        "0.0.0.0",
        port,
        config,
        store,
        catalog,
        files_dir=files_dir,
        stream_slots=stream_slots,
        token_store=token_store,
    )
    held = [name for name, on in (("steamgriddb", config.steamgriddb_key), ("igdb", config.igdb_client_id)) if on]
    saves = f"saves under {store.root}" if store else "no saves store"
    games = f"catalog at {catalog.db}" if catalog else "no catalog"
    creds = ", ".join(held) or "nothing"
    if token_store:
        tokens = f"tokens at {token_store.db}"
    elif config.auth_url:
        tokens = f"tokens asked of {config.auth_url}"
    else:
        tokens = "legacy token only"
    print(f"gotg service on :{port}, holding credentials for: {creds}; {saves}; {games}; {tokens}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
