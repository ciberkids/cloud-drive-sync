"""Unit tests for the mock Drive client and related components."""

from __future__ import annotations

from pathlib import Path

import pytest

from cloud_drive_sync.drive.mock_client import MockChangePoller, MockDriveClient, MockFileOperations


@pytest.fixture
def dirs(tmp_path: Path):
    local = tmp_path / "local"
    remote = tmp_path / "remote"
    local.mkdir()
    remote.mkdir()
    return local, remote


@pytest.fixture
def client(dirs):
    _, remote = dirs
    return MockDriveClient(remote)


@pytest.fixture
def ops(client):
    return MockFileOperations(client)


@pytest.fixture
def poller(client):
    return MockChangePoller(client)


# ── MockDriveClient ───────────────────────────────────────────


class TestMockDriveClient:
    @pytest.mark.asyncio
    async def test_create_folder(self, client: MockDriveClient):
        result = await client.create_file("my_folder", "root", is_folder=True)
        assert result["mimeType"] == "application/vnd.google-apps.folder"
        assert result["name"] == "my_folder"

    @pytest.mark.asyncio
    async def test_create_file_without_content(self, client: MockDriveClient, dirs):
        _, remote = dirs
        result = await client.create_file("empty.txt", "root")
        assert result["name"] == "empty.txt"
        assert (remote / "empty.txt").exists()

    @pytest.mark.asyncio
    async def test_list_files_empty(self, client: MockDriveClient):
        result = await client.list_files("root")
        assert result["files"] == []

    @pytest.mark.asyncio
    async def test_get_file_not_found(self, client: MockDriveClient):
        with pytest.raises(FileNotFoundError):
            await client.get_file("nonexistent_id")

    @pytest.mark.asyncio
    async def test_update_file_not_found(self, client: MockDriveClient):
        with pytest.raises(FileNotFoundError):
            await client.update_file("nonexistent_id")

    @pytest.mark.asyncio
    async def test_trash_file_not_found(self, client: MockDriveClient):
        with pytest.raises(FileNotFoundError):
            await client.trash_file("nonexistent_id")

    @pytest.mark.asyncio
    async def test_rename_file(self, client: MockDriveClient, dirs):
        local, remote = dirs
        src = local / "old_name.txt"
        src.write_text("content")
        created = await client.create_file("old_name.txt", "root", content_path=str(src))

        updated = await client.update_file(created["id"], new_name="new_name.txt")
        assert updated["name"] == "new_name.txt"
        assert (remote / "new_name.txt").exists()
        assert not (remote / "old_name.txt").exists()

    @pytest.mark.asyncio
    async def test_delete_removes_from_disk(self, client: MockDriveClient, dirs):
        local, remote = dirs
        src = local / "to_delete.txt"
        src.write_text("bye")
        created = await client.create_file("to_delete.txt", "root", content_path=str(src))

        await client.delete_file(created["id"])
        assert not (remote / "to_delete.txt").exists()
        assert created["id"] not in client._files

    @pytest.mark.asyncio
    async def test_delete_nonexistent_is_noop(self, client: MockDriveClient):
        # Should not raise
        await client.delete_file("nonexistent_id")

    @pytest.mark.asyncio
    async def test_export_file(self, client: MockDriveClient, dirs):
        local, _ = dirs
        src = local / "export.txt"
        src.write_text("export content")
        created = await client.create_file("export.txt", "root", content_path=str(src))

        data = await client.export_file(created["id"], "text/plain")
        assert data == b"export content"

    @pytest.mark.asyncio
    async def test_export_nonexistent(self, client: MockDriveClient):
        with pytest.raises(FileNotFoundError):
            await client.export_file("nonexistent", "text/plain")

    @pytest.mark.asyncio
    async def test_list_all_recursive(self, client: MockDriveClient, dirs):
        local, _ = dirs
        src = local / "file1.txt"
        src.write_text("data")
        await client.create_file("file1.txt", "root", content_path=str(src))

        result = await client.list_all_recursive("root")
        names = [r["name"] for r in result]
        assert "file1.txt" in names

    @pytest.mark.asyncio
    async def test_scan_existing_files(self, dirs):
        _, remote = dirs
        # Pre-populate remote dir before client init
        (remote / "pre_existing.txt").write_text("already here")

        client = MockDriveClient(remote)
        result = await client.list_files("root")
        names = [f["name"] for f in result["files"]]
        assert "pre_existing.txt" in names

    @pytest.mark.asyncio
    async def test_demo_seed_folders_visible_in_browser(self, client: MockDriveClient):
        """Verify that seeding folders like _setup_demo does makes them visible via folder query."""
        await client.create_file("Documents", "root", is_folder=True)
        await client.create_file("Photos", "root", is_folder=True)

        result = await client.list_files(
            folder_id="root",
            query="mimeType='application/vnd.google-apps.folder'",
        )
        names = sorted(f["name"] for f in result["files"])
        assert names == ["Documents", "Photos"]

    @pytest.mark.asyncio
    async def test_get_about(self, client: MockDriveClient):
        about = await client.get_about()
        assert about["user"]["displayName"] == "Demo User"
        assert about["user"]["emailAddress"] == "demo@cloud-drive-sync.local"
        assert "storageQuota" in about


# ── MockFileOperations ────────────────────────────────────────


class TestMockFileOperations:
    @pytest.mark.asyncio
    async def test_upload_creates_remote_file(self, ops: MockFileOperations, dirs):
        local, remote = dirs
        src = local / "upload.txt"
        src.write_text("upload data")

        result = await ops.upload_file(src, "root")
        assert (remote / "upload.txt").exists()
        assert result["name"] == "upload.txt"

    @pytest.mark.asyncio
    async def test_upload_with_custom_name(self, ops: MockFileOperations, dirs):
        local, remote = dirs
        src = local / "original.txt"
        src.write_text("data")

        result = await ops.upload_file(src, "root", remote_name="renamed.txt")
        assert result["name"] == "renamed.txt"

    @pytest.mark.asyncio
    async def test_download_creates_local_file(self, ops: MockFileOperations, dirs):
        local, remote = dirs
        src = local / "src.txt"
        src.write_text("source data")

        uploaded = await ops.upload_file(src, "root")
        dest = local / "dest.txt"
        await ops.download_file(uploaded["id"], dest)
        assert dest.read_text() == "source data"

    @pytest.mark.asyncio
    async def test_download_creates_parent_dirs(self, ops: MockFileOperations, dirs):
        local, _ = dirs
        src = local / "src.txt"
        src.write_text("data")

        uploaded = await ops.upload_file(src, "root")
        dest = local / "subdir" / "deep" / "downloaded.txt"
        await ops.download_file(uploaded["id"], dest)
        assert dest.exists()

    @pytest.mark.asyncio
    async def test_delete_remote_trash(self, ops: MockFileOperations, client: MockDriveClient, dirs):
        local, _ = dirs
        src = local / "trash.txt"
        src.write_text("data")

        uploaded = await ops.upload_file(src, "root")
        await ops.delete_remote(uploaded["id"], trash=True)

        meta = await client.get_file(uploaded["id"])
        assert meta["trashed"] is True

    @pytest.mark.asyncio
    async def test_delete_remote_permanent(self, ops: MockFileOperations, client: MockDriveClient, dirs):
        local, _ = dirs
        src = local / "permanent.txt"
        src.write_text("data")

        uploaded = await ops.upload_file(src, "root")
        await ops.delete_remote(uploaded["id"], trash=False)

        with pytest.raises(FileNotFoundError):
            await client.get_file(uploaded["id"])


# ── MockChangePoller ──────────────────────────────────────────


class TestMockChangePoller:
    @pytest.mark.asyncio
    async def test_get_start_page_token(self, poller: MockChangePoller):
        token = await poller.get_start_page_token()
        assert token == "1"

    @pytest.mark.asyncio
    async def test_no_changes(self, poller: MockChangePoller):
        token = await poller.get_start_page_token()
        changes, new_token = await poller.poll_changes(token)
        assert changes == []
        assert new_token != token

    @pytest.mark.asyncio
    async def test_detect_trashed_file(self, client: MockDriveClient, poller: MockChangePoller, dirs):
        local, _ = dirs
        src = local / "will_trash.txt"
        src.write_text("data")
        created = await client.create_file("will_trash.txt", "root", content_path=str(src))

        token = await poller.get_start_page_token()
        await client.trash_file(created["id"])
        changes, _ = await poller.poll_changes(token)

        trashed = [c for c in changes if c.file_id == created["id"] and c.removed]
        assert len(trashed) == 1
        assert trashed[0].trashed is True

    @pytest.mark.asyncio
    async def test_detect_deleted_from_disk(self, client: MockDriveClient, poller: MockChangePoller, dirs):
        local, remote = dirs
        src = local / "vanish.txt"
        src.write_text("data")
        created = await client.create_file("vanish.txt", "root", content_path=str(src))

        token = await poller.get_start_page_token()
        # Remove the file from disk but not via the client
        (remote / "vanish.txt").unlink()
        changes, _ = await poller.poll_changes(token)

        removed = [c for c in changes if c.file_id == created["id"] and c.removed]
        assert len(removed) == 1
