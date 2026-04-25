"""Unit tests for NextcloudClient._resolve_path — issue #20."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from cloud_drive_sync.providers.nextcloud.client import NextcloudClient


def _make_client() -> NextcloudClient:
    nc = MagicMock()
    return NextcloudClient(nc, "https://cloud.example.com")


@pytest.mark.parametrize("file_id,expected", [
    # Absolute paths — returned as-is (normalised)
    ("/", "/"),
    ("root", "/"),
    ("/Documents", "/Documents"),
    ("/Documents/ManuAndI", "/Documents/ManuAndI"),
    ("/Documents/ManuAndI/Scanned", "/Documents/ManuAndI/Scanned"),
    ("/Documents/ManuAndI/Documents/AI Bills Folder", "/Documents/ManuAndI/Documents/AI Bills Folder"),
    # Relative paths — nc-py-api returns user_path without leading slash
    ("Documents", "/Documents"),
    ("Documents/ManuAndI", "/Documents/ManuAndI"),
    ("Documents/ManuAndI/Scanned", "/Documents/ManuAndI/Scanned"),
    ("Documents/ManuAndI/Documents/AI Bills Folder", "/Documents/ManuAndI/Documents/AI Bills Folder"),
    # Trailing slash stripped
    ("/Documents/", "/Documents"),
    # Issue #32: trailing spaces in path segments are preserved (legacy #26 folders)
    # _normalise_path only strips trailing slashes, not trailing spaces in segments
    ("Documents/Taxes/Tax declaration documents /", "/Documents/Taxes/Tax declaration documents "),
    ("/Documents/Taxes/Tax declaration documents /", "/Documents/Taxes/Tax declaration documents "),
    ("Documents/Taxes/Tax declaration documents ", "/Documents/Taxes/Tax declaration documents "),
])
def test_resolve_path_normalises(file_id: str, expected: str) -> None:
    client = _make_client()
    assert client._resolve_path(file_id) == expected


def test_resolve_path_legacy_compound_fileid_calls_by_id() -> None:
    """A genuine compound fileid like '00000162ocmvvvbtlon' calls files.by_id."""
    client = _make_client()
    mock_node = MagicMock()
    mock_node.user_path = "Documents/ManuAndI"
    client._nc.files.by_id.return_value = mock_node

    result = client._resolve_path("00000162ocmvvvbtlon")

    client._nc.files.by_id.assert_called_once_with(162)
    assert result == "Documents/ManuAndI"


def test_resolve_path_legacy_not_found_raises() -> None:
    """by_id returning None raises FileNotFoundError."""
    client = _make_client()
    client._nc.files.by_id.return_value = None

    with pytest.raises(FileNotFoundError):
        client._resolve_path("00000162ocmvvvbtlon4")
