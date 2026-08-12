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
    games_root: Path
    source_root: Path
    path_prefix: str
    state_dir: Path
    # The GOTG service, for dual-publishing catalog rows beside the /Games
    # tree. Both empty means the old world only — the CronJob keeps working
    # unchanged until its deployment gains these.
    api_url: str = ""
    index_token: str = ""

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


def load(env: dict[str, str] | None = None, **_compat) -> Config:
    """Build a Config from the environment."""
    env = dict(os.environ if env is None else env)

    games_root = _abs_dir(env, "GAMES_ROOT")
    source_root = _abs_dir(env, "SOURCE_ROOT")
    state_dir = _abs_dir(env, "STATE_DIR")

    if games_root == source_root:
        raise ConfigError("GAMES_ROOT and SOURCE_ROOT must differ; imports would overwrite seeds")

    path_prefix = env.get("PATH_PREFIX", "/Games").strip() or "/Games"
    if not path_prefix.startswith("/"):
        raise ConfigError(f"PATH_PREFIX must start with '/', got {path_prefix}")

    api_url = env.get("GOTG_API_URL", "").strip()
    index_token = env.get("GOTG_INDEX_TOKEN", "").strip()
    if api_url and not api_url.startswith(("http://", "https://")):
        raise ConfigError(f"GOTG_API_URL must be an http(s) URL, got {api_url}")
    if bool(api_url) != bool(index_token):
        raise ConfigError("GOTG_API_URL and GOTG_INDEX_TOKEN are set together or not at all")

    return Config(
        games_root=games_root,
        source_root=source_root,
        path_prefix=path_prefix,
        state_dir=state_dir,
        api_url=api_url,
        index_token=index_token,
    )
