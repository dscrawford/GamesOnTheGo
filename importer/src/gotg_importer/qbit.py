"""qBittorrent work queue.

Tags on the torrent are the durable record of what happened to it:

``gotg-imported`` succeeded · ``gotg-manual`` needs a human · ``gotg-error`` failed

Nothing is ever silently dropped — every torrent the importer looks at leaves with
exactly one of those tags, so the queue doubles as the audit log.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import qbittorrentapi

from .config import Config

log = logging.getLogger("gotg-importer")

TAG_IMPORTED = "gotg-imported"
TAG_MANUAL = "gotg-manual"
TAG_ERROR = "gotg-error"
ALL_TAGS = (TAG_IMPORTED, TAG_MANUAL, TAG_ERROR)


class QbitError(Exception):
    """qBittorrent is unreachable or rejected us — a hard failure for the run."""


@dataclass(frozen=True)
class Torrent:
    infohash: str
    name: str
    content_path: str
    save_path: str
    tags: tuple[str, ...]

    def has_tag(self, tag: str) -> bool:
        return tag in self.tags


def resolve_payload(torrent: Torrent, source_root: Path) -> Path:
    """Find the torrent's payload on *our* mount.

    qBittorrent reports paths as its own container sees them, which need not match
    where this pod mounts the same volume. Fall back to re-rooting the path under
    SOURCE_ROOT before giving up.
    """
    candidates = []
    content = Path(torrent.content_path) if torrent.content_path else None
    if content:
        candidates.append(content)
        try:
            candidates.append(source_root / content.relative_to(torrent.save_path))
        except (ValueError, TypeError):
            pass
        candidates.append(source_root / content.name)
    candidates.append(source_root / torrent.name)

    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"payload for {torrent.name!r} not found; looked in {[str(c) for c in candidates]}")


class Queue:
    """The completed-torrent work queue for one category."""

    def __init__(self, client: qbittorrentapi.Client, category: str) -> None:
        self._client = client
        self.category = category

    @classmethod
    def connect(cls, cfg: Config) -> Queue:
        client = qbittorrentapi.Client(
            host=cfg.qbit_url,
            username=cfg.qbit_user,
            password=cfg.qbit_pass,
            REQUESTS_ARGS={"timeout": (10, 60)},
        )
        try:
            client.auth_log_in()
        except qbittorrentapi.APIError as exc:
            raise QbitError(f"cannot reach qBittorrent at {cfg.qbit_url}: {exc}") from exc
        return cls(client, cfg.qbit_category)

    def pending(self) -> list[Torrent]:
        """Completed torrents in the category that no previous run has resolved."""
        try:
            raw = self._client.torrents_info(status_filter="completed", category=self.category)
        except qbittorrentapi.APIError as exc:
            raise QbitError(f"listing torrents failed: {exc}") from exc

        torrents = []
        for item in raw:
            tags = tuple(t.strip() for t in (item.get("tags") or "").split(",") if t.strip())
            torrent = Torrent(
                infohash=item.get("hash", ""),
                name=item.get("name", ""),
                content_path=item.get("content_path", ""),
                save_path=item.get("save_path", ""),
                tags=tags,
            )
            if any(torrent.has_tag(t) for t in ALL_TAGS):
                continue
            torrents.append(torrent)
        return torrents

    def tag(self, torrent: Torrent, tag: str) -> None:
        try:
            self._client.torrents_add_tags(tags=tag, torrent_hashes=torrent.infohash)
        except qbittorrentapi.APIError as exc:
            # The state file still records the import, so this is not fatal.
            log.warning("could not tag %s with %s: %s", torrent.name, tag, exc)
