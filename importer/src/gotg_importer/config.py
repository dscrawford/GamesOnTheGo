"""Runtime configuration from the environment (IMPORTER_SPEC.md §10).

The Kubernetes CronJob depends on these exact knobs, so treat the field names as a
public interface. Validation is fail-fast: a misconfigured job should exit non-zero
immediately rather than half-import a library.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class ConfigError(Exception):
    """Configuration is missing or invalid — a hard failure, never a partial run."""


@dataclass(frozen=True)
class Config:
    qbit_url: str
    qbit_user: str
    qbit_pass: str
    qbit_category: str
    games_root: Path
    source_root: Path
    path_prefix: str
    state_dir: Path

    @property
    def manifest_path(self) -> Path:
        """Catalog the client fetches. Hidden dir keeps it out of the served listing."""
        return self.games_root / ".gotg" / "manifest.json"

    def server_path(self, dst: Path | str) -> str:
        """Local destination path -> the server-relative path written into the manifest.

        ``/data/Games/n64/usa.foo.z64`` -> ``/Games/n64/usa.foo.z64``
        """
        rel = Path(dst).relative_to(self.games_root)
        return f"{self.path_prefix.rstrip('/')}/{rel}"


def _require(env: dict[str, str], key: str) -> str:
    value = env.get(key, "").strip()
    if not value:
        raise ConfigError(f"{key} is required but unset or empty")
    return value


def _abs_dir(env: dict[str, str], key: str) -> Path:
    path = Path(_require(env, key))
    if not path.is_absolute():
        raise ConfigError(f"{key} must be an absolute path, got {path}")
    return path


def load(env: dict[str, str] | None = None, *, require_qbit: bool = True) -> Config:
    """Build a Config from the environment.

    ``require_qbit`` is False for ``--bootstrap`` runs, which walk explicit source
    directories and never talk to qBittorrent.
    """
    env = dict(os.environ if env is None else env)

    games_root = _abs_dir(env, "GAMES_ROOT")
    source_root = _abs_dir(env, "SOURCE_ROOT")
    state_dir = _abs_dir(env, "STATE_DIR")

    if games_root == source_root:
        raise ConfigError("GAMES_ROOT and SOURCE_ROOT must differ; imports would overwrite seeds")

    path_prefix = env.get("PATH_PREFIX", "/Games").strip() or "/Games"
    if not path_prefix.startswith("/"):
        raise ConfigError(f"PATH_PREFIX must start with '/', got {path_prefix}")

    if require_qbit:
        qbit_url = _require(env, "QBIT_URL")
        if not qbit_url.startswith(("http://", "https://")):
            raise ConfigError(f"QBIT_URL must be an http(s) URL, got {qbit_url}")
        qbit_user = _require(env, "QBIT_USER")
        qbit_pass = _require(env, "QBIT_PASS")
    else:
        qbit_url = env.get("QBIT_URL", "")
        qbit_user = env.get("QBIT_USER", "")
        qbit_pass = env.get("QBIT_PASS", "")

    return Config(
        qbit_url=qbit_url,
        qbit_user=qbit_user,
        qbit_pass=qbit_pass,
        qbit_category=env.get("QBIT_CATEGORY", "games").strip() or "games",
        games_root=games_root,
        source_root=source_root,
        path_prefix=path_prefix,
        state_dir=state_dir,
    )
