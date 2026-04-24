"""Regression tests for issue #19 — NextcloudClient._resolve_path
must accept the relative user_path values returned by nc-py-api's
FsNode (e.g. "Documents", "Documents/ManuAndI") and treat them as
WebDAV paths rather than falling into the legacy-fileid branch and
raising silently.
"""
from unittest.mock import MagicMock

import pytest

from cloud_drive_sync.providers.nextcloud.client import NextcloudClient


@pytest.fixture
def client():
    # _resolve_path / _normalise_path don't touch _nc so a plain stub is fine.
    c = NextcloudClient.__new__(NextcloudClient)
    c._nc = MagicMock()
    return c


@pytest.mark.parametrize(
    "file_id,expected",
    [
        ("root", "/"),
        ("/", "/"),
        ("/Documents", "/Documents"),
        ("/Documents/", "/Documents"),
        # Relative user_path forms returned by nc-py-api FsNode.user_path.
        ("Documents", "/Documents"),
        ("Documents/ManuAndI", "/Documents/ManuAndI"),
        ("Documents/ManuAndI/Documents/AI Bills Folder",
         "/Documents/ManuAndI/Documents/AI Bills Folder"),
        # Path with a dot — should still be treated as a path, not a fileid.
        ("report.pdf", "/report.pdf"),
    ],
)
def test_resolve_path_returns_webdav_path(client, file_id, expected):
    assert client._resolve_path(file_id) == expected


def test_resolve_path_legacy_fileid_falls_back_to_by_id(client):
    # Legacy compound fileid: 8+ hex-style digits followed by a random suffix.
    legacy = "00000162ocmvvvbtlon4"
    fake_node = MagicMock()
    fake_node.user_path = "Documents"
    client._nc.files.by_id.return_value = fake_node

    assert client._resolve_path(legacy) == "Documents"
    client._nc.files.by_id.assert_called_once_with(162)
