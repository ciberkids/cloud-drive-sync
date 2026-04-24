"""Unit tests for NextcloudClient.list_all_recursive and find_child_folder — issues #22 and #23."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from cloud_drive_sync.providers.nextcloud.client import NextcloudClient


def _make_client() -> NextcloudClient:
    nc = MagicMock()
    return NextcloudClient(nc, "https://cloud.example.com")


def _dir_item(name: str, path: str) -> dict:
    return {"id": path, "name": name, "mimeType": "httpd/unix-directory",
            "md5Checksum": "", "modifiedTime": "", "size": 0}


def _file_item(name: str, path: str) -> dict:
    return {"id": path, "name": name, "mimeType": "text/plain",
            "md5Checksum": "", "modifiedTime": "", "size": 0}


class _NC404(Exception):
    status_code = 404


class _NC500(Exception):
    status_code = 500


@pytest.mark.asyncio
async def test_subfolder_404_is_skipped():
    """A 404 on a subfolder is skipped; sibling items still returned."""
    client = _make_client()

    async def fake_list_files(folder_id="root", page_token=None, page_size=100, query=None):
        if folder_id in ("root", "/"):
            return {"files": [
                _file_item("readme.txt", "/readme.txt"),
                _dir_item("§-folder", "/§-folder"),
                _dir_item("good-folder", "/good-folder"),
            ]}
        if folder_id == "/good-folder":
            return {"files": [_file_item("inside.txt", "/good-folder/inside.txt")]}
        raise _NC404(f"404 on {folder_id}")

    client.list_files = fake_list_files  # type: ignore[method-assign]

    result = await client.list_all_recursive("root")
    names = {r["name"] for r in result}
    assert "readme.txt" in names
    assert "§-folder" in names       # dir itself included before recursing
    assert "good-folder" in names
    assert "inside.txt" in names     # children of good-folder still traversed


@pytest.mark.asyncio
async def test_subfolder_non_404_propagates():
    """Non-404 errors from subfolder listing still propagate."""
    client = _make_client()

    async def fake_list_files(folder_id="root", page_token=None, page_size=100, query=None):
        if folder_id in ("root", "/"):
            return {"files": [_dir_item("broken", "/broken")]}
        raise _NC500("server error")

    client.list_files = fake_list_files  # type: ignore[method-assign]

    with pytest.raises(_NC500):
        await client.list_all_recursive("root")


@pytest.mark.asyncio
async def test_top_level_404_returns_empty():
    """A 404 on the root folder returns an empty list instead of raising."""
    client = _make_client()

    async def fake_list_files(folder_id="root", page_token=None, page_size=100, query=None):
        raise _NC404("root folder not found")

    client.list_files = fake_list_files  # type: ignore[method-assign]

    result = await client.list_all_recursive("root")
    assert result == []


@pytest.mark.asyncio
async def test_multiple_404_subfolders_all_skipped():
    """Multiple 404 subfolders are all skipped independently."""
    client = _make_client()

    async def fake_list_files(folder_id="root", page_token=None, page_size=100, query=None):
        if folder_id in ("root", "/"):
            return {"files": [
                _dir_item("a", "/a"),
                _dir_item("b", "/b"),
                _dir_item("c", "/c"),
            ]}
        raise _NC404(f"404 on {folder_id}")

    client.list_files = fake_list_files  # type: ignore[method-assign]

    result = await client.list_all_recursive("root")
    # All three dirs appear (added before recursing), no error raised
    assert len(result) == 3
    assert {r["name"] for r in result} == {"a", "b", "c"}


# ---------------------------------------------------------------------------
# find_child_folder — issue #23
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_find_child_folder_returns_normalised_path():
    """find_child_folder must return a path with a leading slash (issue #23)."""
    client = _make_client()

    dir_node = MagicMock()
    dir_node.name = "Scanned"
    dir_node.is_dir = True
    dir_node.user_path = "Documents/ManuAndI/Scanned"  # raw — no leading slash

    client._nc.files.listdir.return_value = [dir_node]

    result = await client.find_child_folder("/Documents/ManuAndI", "Scanned")
    assert result == "/Documents/ManuAndI/Scanned", (
        "find_child_folder must normalise user_path to have a leading /"
    )


@pytest.mark.asyncio
async def test_find_child_folder_returns_none_when_not_found():
    """find_child_folder returns None when the named subfolder is absent."""
    client = _make_client()
    client._nc.files.listdir.return_value = []
    result = await client.find_child_folder("/Documents", "Missing")
    assert result is None


@pytest.mark.asyncio
async def test_find_child_folder_returns_none_on_404():
    """find_child_folder returns None when parent listing returns 404."""
    client = _make_client()
    client._nc.files.listdir.side_effect = _NC404("parent not found")
    result = await client.find_child_folder("/gone", "child")
    assert result is None
