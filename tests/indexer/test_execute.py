"""Execution: the half that writes. Invariants matter more than happy paths here."""

import os
from pathlib import Path

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
from gotg.indexer.plan import ACTION_ARCHIVE, ACTION_HARDLINK, ACTION_MANUAL, ACTION_SKIP, Op


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


# --- Archiving a decrypted WiiU title ----------------------------------------


def test_archive_packs_the_decrypted_dirs(cfg):
    title = cfg.source_root / "Zelda (USA)"
    for sub, name in (("code", "Zelda.rpx"), ("content", "data.bin"), ("meta", "meta.xml")):
        (title / sub).mkdir(parents=True)
        (title / sub / name).write_bytes(b"payload")

    op = Op(
        ACTION_ARCHIVE,
        "wiiu",
        str(title),
        f"{cfg.games_root}/wiiu/usa.zelda.zip",
        "usa.zelda",
        title="Zelda",
        type="file",
    )
    result = execute(op, cfg)

    if result.status == STATUS_ERROR and "zip" in result.message:
        pytest.skip("zip binary unavailable outside the nix build")
    assert result.status == STATUS_DONE
    dst = cfg.games_root / "wiiu" / "usa.zelda.zip"
    assert dst.is_file()
    assert execute(op, cfg).status == STATUS_NOOP

    import zipfile

    with zipfile.ZipFile(dst) as zf:
        assert "code/Zelda.rpx" in zf.namelist()


def test_archive_leaves_no_staging_directory_behind(cfg):
    title = cfg.source_root / "Zelda (USA)"
    (title / "code").mkdir(parents=True)
    (title / "code" / "Zelda.rpx").write_bytes(b"x")

    op = Op(ACTION_ARCHIVE, "wiiu", str(title), f"{cfg.games_root}/wiiu/usa.zelda.zip", "usa.zelda", type="file")
    result = execute(op, cfg)

    if result.status == STATUS_ERROR and "zip" in result.message:
        pytest.skip("zip binary unavailable outside the nix build")
    leftovers = [p for p in (cfg.games_root / "wiiu").iterdir() if p.name.startswith(".gotg-")]
    assert leftovers == []


def test_archive_of_a_directory_without_decrypted_output_fails_cleanly(cfg):
    title = cfg.source_root / "Zelda (USA)"
    title.mkdir()
    op = Op(ACTION_ARCHIVE, "wiiu", str(title), f"{cfg.games_root}/wiiu/usa.zelda.zip", "usa.zelda", type="file")

    result = execute(op, cfg)

    assert result.status == STATUS_ERROR
    assert not (cfg.games_root / "wiiu" / "usa.zelda.zip").exists()


# --- Extraction shortcut -----------------------------------------------------


def test_extract_hardlinks_an_already_unpacked_rom(cfg):
    from gotg.indexer.plan import ACTION_EXTRACT

    release = cfg.source_root / "Game_NSW-GRP"
    release.mkdir()
    (release / "v-game.rar").write_bytes(b"rar")
    inner = release / "v-game.nsp"
    inner.write_bytes(b"the actual rom")

    op = Op(
        ACTION_EXTRACT, "switch", str(release), f"{cfg.games_root}/switch/world.game.nsp", "world.game", type="file"
    )
    result = execute(op, cfg)

    assert result.status == STATUS_DONE
    dst = cfg.games_root / "switch" / "world.game.nsp"
    # Unpacking multiple gigabytes again would be pure waste when it is right there.
    assert dst.stat().st_ino == inner.stat().st_ino


def test_extract_without_archive_or_rom_fails_cleanly(cfg):
    from gotg.indexer.plan import ACTION_EXTRACT

    release = cfg.source_root / "Game_NSW-GRP"
    release.mkdir()
    (release / "readme.nfo").write_bytes(b"x")

    op = Op(
        ACTION_EXTRACT, "switch", str(release), f"{cfg.games_root}/switch/world.game.nsp", "world.game", type="file"
    )

    assert execute(op, cfg).status == STATUS_ERROR


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


def test_convert_normalizes_an_archived_image_and_leaves_the_source_alone(cfg, monkeypatch):
    """The Sunshine shape: a lone archive holding an image in the wrong format.

    dolphin-tool is stubbed — the conversion itself was verified against the real
    file (IMPORTER_SPEC.md §5a); what matters here is the staging discipline.
    """
    from gotg.indexer import execute as ex

    games = cfg.games_root
    src = cfg.source_root / "Some Game (USA).7z"
    src.write_bytes(b"pretend archive")

    calls: list[list[str]] = []

    def fake_run(cmd, cwd=None):
        calls.append(cmd)
        if cmd[0] == "7z":
            out = Path(cmd[3][2:])
            (out / "Some Game (USA).nkit.iso").write_bytes(b"image")
        else:  # dolphin-tool convert -o <staged>
            Path(cmd[cmd.index("-o") + 1]).write_bytes(b"converted")

    monkeypatch.setattr(ex, "_run", fake_run)
    monkeypatch.setattr(ex, "_require_space", lambda *a: None)

    op = Op(ex.ACTION_CONVERT, "gamecube", str(src), str(games / "gamecube" / "usa.some_game.rvz"), "usa.some_game")
    result = ex.execute(op, cfg, checksum=False)

    assert result.status == ex.STATUS_DONE
    assert (games / "gamecube" / "usa.some_game.rvz").read_bytes() == b"converted"
    # The archive keeps seeding, untouched.
    assert src.read_bytes() == b"pretend archive"
    # And no staging directory survives the run.
    assert not list((games / "gamecube").glob(".gotg-extract-*"))
    assert calls[0][0] == "7z" and calls[1][0] == "dolphin-tool"


# --- updates and DLC, and lone archives ---------------------------------------


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


def test_extract_unpacks_a_lone_archive_with_7z(cfg, monkeypatch):
    from gotg.indexer import execute as ex
    from gotg.indexer.plan import ACTION_EXTRACT

    archive = cfg.source_root / "Game (World).7z"
    archive.write_bytes(b"7z" * 100)
    calls = []

    def fake_run(cmd, cwd=None):
        calls.append(cmd)
        out = Path(next(a for a in cmd if a.startswith("-o"))[2:])
        (out / "Game (World).xci").write_bytes(b"cartridge")
        (out / "readme.nfo").write_bytes(b"x")

    monkeypatch.setattr(ex, "_run", fake_run)
    monkeypatch.setattr(ex, "_require_space", lambda root, needed: None)

    op = Op(ACTION_EXTRACT, "switch", str(archive), f"{cfg.games_root}/switch/world.game.xci", "world.game")
    result = execute(op, cfg)

    assert result.status == STATUS_DONE
    assert calls[0][:2] == ["7z", "x"]
    assert (cfg.games_root / "switch" / "world.game.xci").read_bytes() == b"cartridge"
    assert not list((cfg.games_root / "switch").glob(".gotg-extract-*"))


def test_extract_of_a_lone_archive_that_yields_no_game_is_an_error(cfg, monkeypatch):
    from gotg.indexer import execute as ex
    from gotg.indexer.plan import ACTION_EXTRACT

    archive = cfg.source_root / "Game (World).7z"
    archive.write_bytes(b"7z" * 100)

    def fake_run(cmd, cwd=None):
        out = Path(next(a for a in cmd if a.startswith("-o"))[2:])
        (out / "readme.nfo").write_bytes(b"x")

    monkeypatch.setattr(ex, "_run", fake_run)
    monkeypatch.setattr(ex, "_require_space", lambda root, needed: None)

    op = Op(ACTION_EXTRACT, "switch", str(archive), f"{cfg.games_root}/switch/world.game.xci", "world.game")
    result = execute(op, cfg)

    assert result.status == STATUS_ERROR
    assert "produced no .xci" in result.message
    assert not list((cfg.games_root / "switch").glob(".gotg-extract-*"))
