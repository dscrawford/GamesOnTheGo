"""Execution: the half that writes. Invariants matter more than happy paths here."""

import os

import pytest

from gotg.indexer.config import load as load_config
from gotg.indexer.execute import (
    STATUS_DONE,
    STATUS_ERROR,
    STATUS_MANUAL,
    STATUS_NOOP,
    STATUS_SKIP,
    execute,
    sha256_file,
    write_sidecar,
)
from gotg.indexer.plan import (
    ACTION_ARCHIVE,
    ACTION_CONVERT,
    ACTION_EXTRACT,
    ACTION_HARDLINK,
    ACTION_MANUAL,
    ACTION_SKIP,
    Op,
)


@pytest.fixture
def cfg(tmp_path):
    games = tmp_path / "Games"
    source = tmp_path / "Torrents"
    games.mkdir()
    source.mkdir()
    return load_config(
        {
            "GAMES_ROOT": str(games),
            "SOURCE_ROOT": str(source),
            "STATE_DIR": str(tmp_path / "state"),
        },
        require_qbit=False,
    )


def rom(cfg, name="Zelda (USA).z64", data=b"rom-bytes"):
    path = cfg.source_root / name
    path.write_bytes(data)
    return path


def hardlink_op(cfg, src, entry="usa.zelda", ext="z64"):
    return Op(ACTION_HARDLINK, "n64", str(src), f"{cfg.games_root}/n64/{entry}.{ext}", entry, title="Zelda")


# --- Hardlinks: zero space, seeding safe -------------------------------------


def test_hardlink_shares_the_inode_with_the_seed(cfg):
    src = rom(cfg)
    result = execute(hardlink_op(cfg, src), cfg)

    assert result.status == STATUS_DONE
    dst = cfg.games_root / "n64" / "usa.zelda.z64"
    assert dst.stat().st_ino == src.stat().st_ino, "import must not copy the payload"
    assert src.exists(), "the seed must survive untouched"


def test_rerunning_is_a_clean_noop(cfg):
    src = rom(cfg)
    first = execute(hardlink_op(cfg, src), cfg)
    second = execute(hardlink_op(cfg, src), cfg)

    assert (first.status, second.status) == (STATUS_DONE, STATUS_NOOP)


def test_a_different_file_at_the_destination_is_never_clobbered(cfg):
    src = rom(cfg)
    dst = cfg.games_root / "n64" / "usa.zelda.z64"
    dst.parent.mkdir(parents=True)
    dst.write_bytes(b"something else")

    result = execute(hardlink_op(cfg, src), cfg)

    assert result.status == STATUS_ERROR
    assert dst.read_bytes() == b"something else"


def test_writes_outside_the_games_root_are_refused(cfg, tmp_path):
    src = rom(cfg)
    escape = Op(ACTION_HARDLINK, "n64", str(src), str(tmp_path / "escaped.z64"), "usa.zelda")

    result = execute(escape, cfg)

    assert result.status == STATUS_ERROR
    assert "outside" in result.message
    assert not (tmp_path / "escaped.z64").exists()


def test_traversal_in_the_destination_is_refused(cfg):
    src = rom(cfg)
    sneaky = Op(ACTION_HARDLINK, "n64", str(src), f"{cfg.games_root}/n64/../../escaped.z64", "usa.zelda")

    assert execute(sneaky, cfg).status == STATUS_ERROR


# --- Checksums ---------------------------------------------------------------


def test_sidecar_is_bare_hex_and_written_once(cfg):
    src = rom(cfg, data=b"abc")
    execute(hardlink_op(cfg, src), cfg)

    sidecar = cfg.games_root / "n64" / "usa.zelda.z64.sha256"
    content = sidecar.read_text()
    assert content.strip() == sha256_file(src)
    assert " " not in content.strip(), "client expects bare hex, not sha256sum output"

    sidecar.write_text("pinned\n")
    write_sidecar(cfg.games_root / "n64" / "usa.zelda.z64")
    assert sidecar.read_text() == "pinned\n", "existing sidecars are left alone"


def test_checksums_can_be_skipped(cfg):
    src = rom(cfg)
    result = execute(hardlink_op(cfg, src), cfg, checksum=False)

    assert result.entry.sha256 is None
    assert not (cfg.games_root / "n64" / "usa.zelda.z64.sha256").exists()


# --- Manifest entries --------------------------------------------------------


def test_entry_uses_the_server_relative_path(cfg):
    src = rom(cfg, data=b"1234567890")
    entry = execute(hardlink_op(cfg, src), cfg).entry

    assert entry.path == "/Games/n64/usa.zelda.z64"
    assert entry.size_bytes == 10
    assert entry.platform == "n64"
    assert entry.game_id == "usa.zelda"


# --- Non-writing actions -----------------------------------------------------


@pytest.mark.parametrize(
    "action, status",
    [(ACTION_SKIP, STATUS_SKIP), (ACTION_MANUAL, STATUS_MANUAL)],
)
def test_skip_and_manual_write_nothing(cfg, action, status):
    op = Op(action, "wiiu", str(cfg.source_root / "whatever"), "", "", reason="because")
    result = execute(op, cfg)

    assert result.status == status
    assert list(cfg.games_root.iterdir()) == []


# --- The client's actions: published, never materialized ---------------------


@pytest.mark.parametrize("action", [ACTION_EXTRACT, ACTION_CONVERT, ACTION_ARCHIVE])
def test_unpacking_and_converting_are_the_clients_and_write_nothing_here(cfg, action):
    """A scene rar set, a 7z disc image, a decrypted WiiU tree: the catalog
    names the raw members and a recipe on the client does the rest. The
    server ships what it has and makes no second copy."""
    release = cfg.source_root / "Game_NSW-GRP"
    release.mkdir()
    (release / "g.rar").write_bytes(b"rar")

    op = Op(action, "switch", str(release), f"{cfg.games_root}/switch/world.game.xci", "world.game", type="file")
    result = execute(op, cfg)

    assert result.status == STATUS_DONE and result.ok
    assert result.entry is None
    assert "client" in result.message
    assert list(cfg.games_root.iterdir()) == []


def test_an_unknown_action_is_an_error_not_a_write(cfg):
    op = Op("teleport", "switch", str(cfg.source_root / "x"), f"{cfg.games_root}/switch/x.nsp", "world.x")
    result = execute(op, cfg)
    assert result.status == STATUS_ERROR
    assert list(cfg.games_root.iterdir()) == []


def test_execute_never_raises_on_a_missing_source(cfg):
    op = hardlink_op(cfg, cfg.source_root / "does-not-exist.z64")
    result = execute(op, cfg)

    assert result.status == STATUS_ERROR
    assert not result.ok


def test_source_tree_is_never_modified(cfg):
    src = rom(cfg)
    before = {p: (p.stat().st_mtime, p.stat().st_size) for p in cfg.source_root.rglob("*")}

    execute(hardlink_op(cfg, src), cfg)

    after = {p: (p.stat().st_mtime, p.stat().st_size) for p in cfg.source_root.rglob("*")}
    assert before == after
    assert os.listdir(cfg.source_root) == ["Zelda (USA).z64"]


# --- updates and DLC ----------------------------------------------------------


def test_attach_writes_nothing_and_succeeds(cfg):
    from gotg.indexer.plan import ACTION_ATTACH

    release = cfg.source_root / "Game_Update_v1.1_NSW-GRP"
    release.mkdir()
    (release / "g.rar").write_bytes(b"rar")
    op = Op(ACTION_ATTACH, "switch", str(release), "", "world.game", role="update", version="1.1")

    result = execute(op, cfg)

    assert result.status == STATUS_DONE
    assert result.entry is None
    assert list(cfg.games_root.iterdir()) == []


# --- hashing gives its cache back ---------------------------------------------


def test_hashing_a_large_file_gives_its_cache_back(tmp_path, monkeypatch):
    from gotg.indexer import execute as ex

    calls = []
    monkeypatch.setattr(ex.os, "posix_fadvise", lambda fd, off, ln, advice: calls.append(advice))
    monkeypatch.setattr(ex, "CACHE_DROP_BYTES", 8)
    big = tmp_path / "big.bin"
    big.write_bytes(b"z" * 64)

    assert sha256_file(big) == __import__("hashlib").sha256(b"z" * 64).hexdigest()
    assert calls and all(a == ex.os.POSIX_FADV_DONTNEED for a in calls)
