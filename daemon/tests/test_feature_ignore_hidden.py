"""Tests for Bug 5: Hidden files toggle (NEW FEATURE).

This is a new feature that doesn't exist yet. Tests should define the expected
behavior for ignoring hidden files (dotfiles) in sync operations.

Expected behavior:
- SyncPair model: new `ignore_hidden` field (default True)
- Scanner: should filter dotfiles when ignore_hidden=True
- Watcher: should filter dotfile events when ignore_hidden=True
- Planner: should exclude hidden files from sync plans
- IPC handler: set_ignore_hidden or add_sync_pair accepts the flag
- Config persistence: saving/loading the toggle

These tests will FAIL until the feature is implemented (with ImportError,
AttributeError, or assertion failures).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cloud_drive_sync.config import Config, SyncConfig, SyncPair
from cloud_drive_sync.db.database import Database


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test_hidden.db")
    await database.open()
    yield database
    await database.close()


@pytest.fixture
def config(tmp_path: Path) -> Config:
    cfg = Config()
    cfg.sync = SyncConfig(
        pairs=[SyncPair(local_path=str(tmp_path / "sync_dir"), remote_folder_id="root")],
    )
    return cfg


# ── Feature 5a: SyncPair model should have ignore_hidden field ─────


def test_sync_pair_has_ignore_hidden_field():
    """SyncPair should have an ignore_hidden attribute (default True).

    FEATURE: This field doesn't exist yet on SyncPair.
    """
    pair = SyncPair(local_path="/tmp/test", remote_folder_id="root")

    # FEATURE: This will raise AttributeError until the field is added
    assert hasattr(pair, "ignore_hidden"), (
        "SyncPair is missing 'ignore_hidden' field. "
        "Feature: add ignore_hidden: bool = True to SyncPair dataclass."
    )
    assert pair.ignore_hidden is True, (
        "ignore_hidden should default to True (hide dotfiles by default)"
    )


def test_sync_pair_ignore_hidden_false():
    """When ignore_hidden is False, dotfiles should be synced."""
    # FEATURE: This will fail until the field is added to SyncPair
    try:
        pair = SyncPair(
            local_path="/tmp/test",
            remote_folder_id="root",
            ignore_hidden=False,
        )
        assert pair.ignore_hidden is False
    except TypeError as e:
        pytest.fail(
            f"SyncPair doesn't accept ignore_hidden parameter: {e}. "
            "Feature: add ignore_hidden field to SyncPair."
        )


# ── Feature 5b: Scanner should filter dotfiles ────────────────────


@pytest.mark.asyncio
async def test_scanner_excludes_dotfiles_by_default(tmp_path: Path):
    """Scanner should skip dotfiles/dotdirs when ignore_hidden=True."""
    from cloud_drive_sync.local.scanner import scan_directory

    scan_dir = tmp_path / "scan_test"
    scan_dir.mkdir()

    # Normal files
    (scan_dir / "readme.txt").write_text("hello")
    (scan_dir / "data.csv").write_text("a,b,c")

    # Hidden files (dotfiles)
    (scan_dir / ".hidden_config").write_text("secret")
    (scan_dir / ".env").write_text("API_KEY=xxx")

    # Hidden directory with files
    hidden_dir = scan_dir / ".hidden_dir"
    hidden_dir.mkdir()
    (hidden_dir / "inside.txt").write_text("hidden content")

    # FEATURE: scan_directory should accept ignore_hidden parameter
    # Currently it doesn't have this parameter
    try:
        result = await scan_directory(scan_dir, ignore_hidden=True)
    except TypeError:
        # Fallback: try without the parameter and check default behavior
        result = await scan_directory(scan_dir)

    paths = set(result.keys())

    # Currently dotfiles ARE included (no filtering)
    # After feature: dotfiles should be excluded by default
    assert ".hidden_config" not in paths, (
        ".hidden_config was included in scan results. "
        "Feature: scanner should exclude dotfiles when ignore_hidden=True."
    )
    assert ".env" not in paths, (
        ".env was included in scan results. "
        "Feature: scanner should exclude dotfiles."
    )
    hidden_dir_files = [p for p in paths if p.startswith(".hidden_dir")]
    assert len(hidden_dir_files) == 0, (
        f"Files in .hidden_dir were included: {hidden_dir_files}. "
        "Feature: scanner should exclude files in hidden directories."
    )

    # Normal files should still be included
    assert "readme.txt" in paths, "Normal files should be included"
    assert "data.csv" in paths, "Normal files should be included"


@pytest.mark.asyncio
async def test_scanner_includes_dotfiles_when_not_hidden(tmp_path: Path):
    """When ignore_hidden=False, dotfiles should be included in scan."""
    from cloud_drive_sync.local.scanner import scan_directory

    scan_dir = tmp_path / "scan_all"
    scan_dir.mkdir()
    (scan_dir / ".dotfile").write_text("visible")
    (scan_dir / "normal.txt").write_text("also visible")

    # FEATURE: scan_directory should accept ignore_hidden=False
    try:
        result = await scan_directory(scan_dir, ignore_hidden=False)
    except TypeError:
        pytest.fail(
            "scan_directory doesn't accept ignore_hidden parameter. "
            "Feature: add ignore_hidden parameter to scan_directory."
        )

    paths = set(result.keys())
    assert ".dotfile" in paths, "Dotfiles should be included when ignore_hidden=False"
    assert "normal.txt" in paths


# ── Feature 5c: Watcher should filter dotfile events ──────────────


@pytest.mark.asyncio
async def test_watcher_filters_dotfile_events(tmp_path: Path):
    """Watcher should ignore filesystem events for dotfiles when
    ignore_hidden=True.

    FEATURE: DirectoryWatcher doesn't currently have ignore_hidden support.
    """
    import asyncio
    from cloud_drive_sync.local.watcher import DirectoryWatcher

    watch_dir = tmp_path / "watch_test"
    watch_dir.mkdir()

    # FEATURE: DirectoryWatcher should accept ignore_hidden parameter
    try:
        watcher = DirectoryWatcher(watch_dir, debounce_delay=0.1, ignore_hidden=True)
    except TypeError:
        pytest.fail(
            "DirectoryWatcher doesn't accept ignore_hidden parameter. "
            "Feature: add ignore_hidden support to DirectoryWatcher."
        )

    await watcher.start()
    try:
        # Create a dotfile - should be ignored
        (watch_dir / ".hidden_new").write_text("hidden")
        # Create a normal file - should be detected
        (watch_dir / "visible.txt").write_text("visible")

        # Wait for debounce
        await asyncio.sleep(0.5)

        # Collect changes
        changes = []
        while not watcher.changes.empty():
            change = watcher.changes.get_nowait()
            changes.append(change)

        change_paths = [c.path for c in changes]

        assert ".hidden_new" not in change_paths, (
            "Watcher reported change for dotfile .hidden_new. "
            "Feature: watcher should ignore dotfile events."
        )
        assert "visible.txt" in change_paths, "Normal file changes should be reported"
    finally:
        await watcher.stop()


# ── Feature 5d: Config persistence ────────────────────────────────


def test_config_save_load_ignore_hidden(tmp_path: Path):
    """ignore_hidden setting should persist through config save/load cycle."""
    config_file = tmp_path / "config.toml"

    # Create config with ignore_hidden=False
    cfg = Config()
    try:
        cfg.sync.pairs.append(SyncPair(
            local_path="/tmp/test",
            remote_folder_id="root",
            ignore_hidden=False,
        ))
    except TypeError:
        pytest.fail(
            "SyncPair doesn't accept ignore_hidden. "
            "Feature: add ignore_hidden to SyncPair and config serialization."
        )

    cfg.save(config_file)

    # Load and verify
    loaded = Config.load(config_file)
    assert len(loaded.sync.pairs) > 0
    assert hasattr(loaded.sync.pairs[0], "ignore_hidden"), "ignore_hidden should persist"
    assert loaded.sync.pairs[0].ignore_hidden is False, (
        "ignore_hidden=False should survive save/load cycle"
    )


# ── Feature 5e: IPC handler for setting ignore_hidden ─────────────


@pytest.mark.asyncio
async def test_add_sync_pair_accepts_ignore_hidden(config: Config, db: Database, tmp_path: Path):
    """add_sync_pair handler should accept ignore_hidden parameter."""
    from cloud_drive_sync.ipc.handlers import RequestHandler
    from cloud_drive_sync.ipc.protocol import JsonRpcRequest

    config_file = tmp_path / "config.toml"
    config.save(config_file)
    original_save = config.save
    config.save = lambda path=None: original_save(config_file)

    handler = RequestHandler(engine=None, config=config)
    handler.set_db(db)

    new_folder = str(tmp_path / "new_folder")
    req = JsonRpcRequest(
        method="add_sync_pair",
        params={
            "local_path": new_folder,
            "remote_folder_id": "root",
            "ignore_hidden": False,
        },
        id=1,
    )
    resp = await handler.handle(req)
    assert resp.error is None, f"add_sync_pair should accept ignore_hidden: {resp.error}"

    # Find the newly added pair
    added_pair = config.sync.pairs[-1]
    assert hasattr(added_pair, "ignore_hidden"), (
        "Added pair should have ignore_hidden attribute"
    )
    assert added_pair.ignore_hidden is False, (
        "ignore_hidden should be set to the value passed in params"
    )


@pytest.mark.asyncio
async def test_set_ignore_hidden_handler(config: Config, db: Database, tmp_path: Path):
    """There should be a set_ignore_hidden IPC handler to toggle the setting."""
    from cloud_drive_sync.ipc.handlers import RequestHandler
    from cloud_drive_sync.ipc.protocol import JsonRpcRequest

    config_file = tmp_path / "config.toml"
    config.save(config_file)
    original_save = config.save
    config.save = lambda path=None: original_save(config_file)

    handler = RequestHandler(engine=None, config=config)
    handler.set_db(db)

    # FEATURE: set_ignore_hidden method should exist
    req = JsonRpcRequest(
        method="set_ignore_hidden",
        params={"pair_id": "0", "ignore_hidden": False},
        id=2,
    )
    resp = await handler.handle(req)

    # BUG/FEATURE: This will return METHOD_NOT_FOUND until implemented
    assert resp.error is None, (
        f"set_ignore_hidden handler returned error: {resp.error}. "
        "Feature: add set_ignore_hidden IPC handler."
    )


# ── Feature 5f: Planner excludes hidden files ─────────────────────


def test_planner_excludes_hidden_files_in_initial_sync():
    """Initial sync planner should exclude hidden files when ignore_hidden=True."""
    from cloud_drive_sync.local.scanner import LocalFileInfo
    from cloud_drive_sync.sync.planner import ActionType, plan_initial_sync

    local_files = {
        "readme.txt": LocalFileInfo(md5="aaa", mtime=100.0, size=10),
        ".hidden": LocalFileInfo(md5="bbb", mtime=100.0, size=5),
        ".config/settings.json": LocalFileInfo(md5="ccc", mtime=100.0, size=20),
        "docs/guide.md": LocalFileInfo(md5="ddd", mtime=100.0, size=15),
    }

    remote_files = []  # Empty remote - all should be uploaded

    actions = plan_initial_sync(local_files, remote_files)
    upload_paths = {a.path for a in actions if a.action == ActionType.UPLOAD}

    # Currently ALL files get upload actions including hidden ones
    # After feature: hidden files should be excluded from sync plan
    assert ".hidden" not in upload_paths, (
        ".hidden was included in sync plan. "
        "Feature: planner should exclude dotfiles when ignore_hidden=True."
    )
    assert ".config/settings.json" not in upload_paths, (
        ".config/settings.json was included in sync plan. "
        "Feature: planner should exclude files in hidden directories."
    )
    assert "readme.txt" in upload_paths, "Normal files should be in sync plan"
    assert "docs/guide.md" in upload_paths, "Normal nested files should be in sync plan"
