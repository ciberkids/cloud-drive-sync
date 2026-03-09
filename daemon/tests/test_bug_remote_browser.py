"""Tests for Bug 2: Remote folder browser shows wrong data (files with folder icons).

Bug: The list_remote_folders handler queries ONLY folders via the query parameter:
     "mimeType = 'application/vnd.google-apps.folder'"
     But MockDriveClient.list_files() completely IGNORES the query parameter
     and returns ALL files in the parent folder.

     This causes files (like .kate-swp, .txt files) to appear in the folder
     browser with folder icons.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gdrive_sync.config import Config
from gdrive_sync.drive.mock_client import MockDriveClient
from gdrive_sync.ipc.handlers import RequestHandler
from gdrive_sync.ipc.protocol import JsonRpcRequest


@pytest.fixture
def remote_dir(tmp_path: Path) -> Path:
    d = tmp_path / "remote"
    d.mkdir()
    return d


@pytest.fixture
def mock_client(remote_dir: Path) -> MockDriveClient:
    client = MockDriveClient(remote_dir)
    return client


@pytest.fixture
def config():
    return Config()


# ── Bug 2a: MockDriveClient.list_files ignores query parameter ────


@pytest.mark.asyncio
async def test_mock_client_list_files_ignores_query(mock_client: MockDriveClient, remote_dir: Path):
    """BUG: MockDriveClient.list_files() ignores the query parameter entirely.

    When called with a mimeType filter query, it should only return files
    matching that mimeType. Instead, it returns ALL children of the folder.
    """
    # Create a mix of folders and files
    await mock_client.create_file("Documents", "root", is_folder=True)
    await mock_client.create_file("Photos", "root", is_folder=True)
    (remote_dir / "test_file.txt").write_text("hello")
    (remote_dir / ".kate-swp").write_bytes(b"\x00" * 100)

    # Re-scan to pick up the files we manually created on disk
    mock_client._scan_existing()

    # Query for folders only (this is what list_remote_folders does)
    query = "'root' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    result = await mock_client.list_files(folder_id="root", query=query)

    files = result.get("files", [])

    # BUG: list_files returns ALL children, not just folders
    non_folder_files = [
        f for f in files
        if f["mimeType"] != "application/vnd.google-apps.folder"
    ]

    assert len(non_folder_files) == 0, (
        f"Found {len(non_folder_files)} non-folder files in folder-only query. "
        f"Names: {[f['name'] for f in non_folder_files]}. "
        "Bug: MockDriveClient.list_files() ignores the query parameter."
    )


@pytest.mark.asyncio
async def test_mock_client_list_files_respects_mime_type_filter(mock_client: MockDriveClient):
    """After fix, list_files should filter by mimeType when query includes it."""
    # Create folders and files
    await mock_client.create_file("Folder_A", "root", is_folder=True)
    await mock_client.create_file("Folder_B", "root", is_folder=True)
    await mock_client.create_file("document.txt", "root", content_path=None)
    await mock_client.create_file("image.png", "root", content_path=None)

    # Query for folders only
    query = "'root' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    result = await mock_client.list_files(folder_id="root", query=query)

    files = result.get("files", [])

    # All results should be folders
    for f in files:
        assert f["mimeType"] == "application/vnd.google-apps.folder", (
            f"Non-folder file '{f['name']}' (mimeType={f['mimeType']}) "
            f"returned by folder-only query"
        )

    # Should have exactly 2 folders
    assert len(files) == 2, f"Expected 2 folders, got {len(files)}: {[f['name'] for f in files]}"


# ── Bug 2b: list_remote_folders handler returns files ──────────────


@pytest.mark.asyncio
async def test_list_remote_folders_returns_only_folders(mock_client: MockDriveClient, config: Config):
    """BUG: The list_remote_folders handler returns non-folder files because
    the mock client ignores the query filter.

    The handler correctly builds a query with mimeType filter, but the mock
    client doesn't apply it.
    """
    # Create a mix of folders and files
    await mock_client.create_file("My Documents", "root", is_folder=True)
    await mock_client.create_file("readme.txt", "root", content_path=None)
    await mock_client.create_file(".matteo.txt.kate-swp", "root", content_path=None)
    await mock_client.create_file("from_cloud.txt", "root", content_path=None)

    handler = RequestHandler(engine=None, config=config)
    handler.set_drive_client(mock_client)

    req = JsonRpcRequest(method="list_remote_folders", params={"parent_id": "root"}, id=1)
    resp = await handler.handle(req)

    assert resp.error is None
    folders = resp.result["folders"]

    # BUG: The response includes txt files and swap files as "folders"
    folder_names = [f["name"] for f in folders]

    # After fix, only "My Documents" should be returned
    assert "readme.txt" not in folder_names, (
        "readme.txt appears in folder listing. "
        "Bug: mock client returns all files, not just folders."
    )
    assert ".matteo.txt.kate-swp" not in folder_names, (
        ".kate-swp file appears in folder listing. "
        "Bug: mock client returns all files, not just folders."
    )
    assert "from_cloud.txt" not in folder_names, (
        "from_cloud.txt appears in folder listing. "
        "Bug: mock client returns all files, not just folders."
    )

    assert len(folders) == 1, (
        f"Expected 1 folder, got {len(folders)}: {folder_names}. "
        "Bug: MockDriveClient.list_files doesn't filter by mimeType."
    )
    assert folders[0]["name"] == "My Documents"


@pytest.mark.asyncio
async def test_list_remote_folders_nested(mock_client: MockDriveClient, config: Config):
    """Browsing into a subfolder should also only show folders, not files."""
    # Create parent folder
    parent = await mock_client.create_file("Parent", "root", is_folder=True)
    parent_id = parent["id"]

    # Create child folder and files inside parent
    await mock_client.create_file("Subfolder", parent_id, is_folder=True)
    await mock_client.create_file("notes.txt", parent_id, content_path=None)
    await mock_client.create_file("data.csv", parent_id, content_path=None)

    handler = RequestHandler(engine=None, config=config)
    handler.set_drive_client(mock_client)

    req = JsonRpcRequest(
        method="list_remote_folders",
        params={"parent_id": parent_id},
        id=2,
    )
    resp = await handler.handle(req)

    assert resp.error is None
    folders = resp.result["folders"]

    # BUG: Returns notes.txt and data.csv along with Subfolder
    assert len(folders) == 1, (
        f"Expected 1 subfolder, got {len(folders)}: {[f['name'] for f in folders]}. "
        "Bug: non-folder files returned in nested folder browsing."
    )
    assert folders[0]["name"] == "Subfolder"


@pytest.mark.asyncio
async def test_list_remote_folders_excludes_trashed(mock_client: MockDriveClient, config: Config):
    """Trashed folders should not appear in the folder browser."""
    await mock_client.create_file("Active Folder", "root", is_folder=True)
    trashed_folder = await mock_client.create_file("Trashed Folder", "root", is_folder=True)
    await mock_client.trash_file(trashed_folder["id"])

    handler = RequestHandler(engine=None, config=config)
    handler.set_drive_client(mock_client)

    req = JsonRpcRequest(method="list_remote_folders", params={"parent_id": "root"}, id=3)
    resp = await handler.handle(req)

    assert resp.error is None
    folders = resp.result["folders"]
    folder_names = [f["name"] for f in folders]

    assert "Trashed Folder" not in folder_names, "Trashed folder should not appear"
    assert "Active Folder" in folder_names
