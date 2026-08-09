"""Run the proxy, configured entirely from the environment.

Environment rather than a file, because that is what a Kubernetes Secret
mounts most naturally, and because a config file holding credentials is one
more thing to get the permissions of wrong.
"""

from __future__ import annotations

import os
import sys

from .app import Config, make_server


def config_from_env(env: dict[str, str] | None = None) -> Config:
    env = os.environ if env is None else env
    return Config(
        token=env.get("GOTG_PROXY_TOKEN", ""),
        steamgriddb_key=env.get("STEAMGRIDDB_API_KEY", ""),
        igdb_client_id=env.get("IGDB_CLIENT_ID", ""),
        igdb_client_secret=env.get("IGDB_CLIENT_SECRET", ""),
    ).validate()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    port = int(os.environ.get("PORT", "8080"))
    try:
        config = config_from_env()
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    server = make_server("0.0.0.0", port, config)  # noqa: S104 — a container listens on all of its own
    held = [name for name, on in (("steamgriddb", config.steamgriddb_key),
                                  ("igdb", config.igdb_client_id)) if on]
    print(f"gotg-proxy on :{port}, holding credentials for: {', '.join(held) or 'nothing'}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
