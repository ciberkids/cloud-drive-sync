"""Regression tests for issue #36: missing remote root folder causes sync failures.

When the configured remote sync root doesn't exist, list_all_recursive now raises
FileNotFoundError and the engine creates the folder path before retrying.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from cloud_drive_sync.providers.nextcloud.client import NextcloudClient


def _make_client() -> NextcloudClient:
    nc = MagicMock()
    return NextcloudClient(nc, "https://cloud.example.com")


class _NC404(Exception):
    status_code = 404


# ---------------------------------------------------------------------------
# list_all_recursive — root 404 raises FileNotFoundError
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_root_404_raises_file_not_found():
    """list_all_recursive raises FileNotFoundError when the root folder is missing."""
    client = _make_client()

    async def fake_list_files(folder_id="root", **_):
        raise _NC404("not found")

    client.list_files = fake_list_files  # type: ignore[method-assign]

    with pytest.raises(FileNotFoundError, match="Remote root folder not found"):
        await client.list_all_recursive("/Documents/ManuAndI")


@pytest.mark.asyncio
async def test_subfolder_404_still_skipped_after_change():
    """Subfolders that 404 are still silently skipped (regression guard)."""
    client = _make_client()

    async def fake_list_files(folder_id="root", **_):
        if folder_id in ("root", "/"):
            return {"files": [
                {"id": "/a", "name": "a", "mimeType": "httpd/unix-directory",
                 "md5Checksum": "", "modifiedTime": "", "size": 0},
            ]}
        raise _NC404("subfolder gone")

    client.list_files = fake_list_files  # type: ignore[method-assign]

    result = await client.list_all_recursive("root")
    assert len(result) == 1 and result[0]["name"] == "a"


# ---------------------------------------------------------------------------
# ensure_root_folder — walks path segments
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ensure_root_folder_creates_segments():
    """ensure_root_folder calls create_file for each path segment."""
    client = _make_client()

    created: list[tuple[str, str]] = []

    async def fake_create_file(name, parent_id, is_folder=False, **_):
        created.append((name, parent_id))
        return {"id": f"/{name}", "name": name, "mimeType": "httpd/unix-directory",
                "md5Checksum": "", "modifiedTime": "", "size": 0}

    client.create_file = fake_create_file  # type: ignore[method-assign]

    await client.ensure_root_folder("/Documents/ManuAndI")

    assert ("Documents", "/") in created
    assert ("ManuAndI", "/Documents") in created


@pytest.mark.asyncio
async def test_ensure_root_folder_root_noop():
    """ensure_root_folder does nothing for 'root' or '/'."""
    client = _make_client()
    client.create_file = AsyncMock()  # type: ignore[method-assign]

    await client.ensure_root_folder("root")
    await client.ensure_root_folder("/")

    client.create_file.assert_not_called()
