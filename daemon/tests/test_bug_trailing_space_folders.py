"""Regression tests for issue #32.

Legacy Nextcloud folders created with trailing spaces (a side-effect of issue
#26) must still be found by find_child_folder and create_file when the daemon
searches with the trimmed name.

Two failure modes are covered:
1. find_child_folder exact-match fails  → _ensure_remote_dirs can't resolve ID
2. _normalise_path strips trailing space → listdir 404 on recursive tree build

"""

from __future__ import annotations

import unicodedata
from unittest.mock import MagicMock

import pytest

from cloud_drive_sync.providers.nextcloud.client import NextcloudClient


def _make_client() -> NextcloudClient:
    nc = MagicMock()
    return NextcloudClient(nc, "https://cloud.example.com")


def _dir_node(name: str, user_path: str | None = None) -> MagicMock:
    node = MagicMock()
    node.name = name
    node.is_dir = True
    node.user_path = user_path or f"Documents/{name}"
    return node


# ── _normalise_path ───────────────────────────────────────────────────────────

def test_normalise_path_preserves_trailing_space():
    """Trailing space in a path segment must NOT be stripped (legacy #26 folders)."""
    client = _make_client()
    assert client._normalise_path("Documents/Kaufland ") == "/Documents/Kaufland "


def test_normalise_path_strips_trailing_slash_only():
    client = _make_client()
    assert client._normalise_path("/Documents/Kaufland/") == "/Documents/Kaufland"


def test_normalise_path_strips_leading_whitespace():
    client = _make_client()
    assert client._normalise_path("  /Documents/Kaufland") == "/Documents/Kaufland"


def test_normalise_path_root():
    client = _make_client()
    assert client._normalise_path("/") == "/"
    assert client._normalise_path("") == "/"


# ── find_child_folder ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_find_child_folder_matches_trailing_space_node():
    """Server has 'Kaufland ' — daemon searches for 'Kaufland' — must match."""
    client = _make_client()
    client._nc.files.listdir.return_value = [
        _dir_node("Kaufland ", "Documents/ManuAndI/Documents/Diverse Bills/Kaufland ")
    ]

    result = await client.find_child_folder("/Documents/ManuAndI/Documents/Diverse Bills", "Kaufland")

    assert result is not None, "Should have found the trailing-space folder"
    # Returned path must include the trailing space so listdir works
    assert result.endswith("Kaufland "), f"Expected trailing space preserved in path, got {result!r}"


@pytest.mark.asyncio
async def test_find_child_folder_nfc_plus_trailing_space():
    """NFC normalization and trailing-space stripping are both applied."""
    client = _make_client()
    folder_name_nfc = "Einbürgerung "  # NFC + trailing space
    folder_name_search = unicodedata.normalize("NFC", "Einbürgerung")  # clean search term
    client._nc.files.listdir.return_value = [
        _dir_node(folder_name_nfc, f"Documents/{folder_name_nfc}")
    ]

    result = await client.find_child_folder("/Documents", folder_name_search)

    assert result is not None, "Should have matched NFC + trailing-space folder"


@pytest.mark.asyncio
async def test_find_child_folder_exact_match_still_works():
    """Normal (no trailing space) folders must still be found."""
    client = _make_client()
    client._nc.files.listdir.return_value = [_dir_node("Warranties")]

    result = await client.find_child_folder("/Documents", "Warranties")

    assert result is not None


@pytest.mark.asyncio
async def test_find_child_folder_returns_none_for_different_name():
    """Non-matching folders must still return None."""
    client = _make_client()
    client._nc.files.listdir.return_value = [_dir_node("OtherFolder")]

    result = await client.find_child_folder("/Documents", "Kaufland")

    assert result is None


# ── create_file (is_folder=True, 405 path) ───────────────────────────────────

@pytest.mark.asyncio
async def test_create_file_mkdir_405_finds_trailing_space_node():
    """After 405, create_file must locate an existing trailing-space folder."""
    client = _make_client()

    already_exists = Exception("already exists")
    already_exists.status_code = 405  # type: ignore[attr-defined]
    client._nc.files.mkdir.side_effect = already_exists

    legacy_node = _dir_node("Kaufland ", "Documents/Kaufland ")
    client._nc.files.listdir.return_value = [legacy_node]

    result = await client.create_file("Kaufland", "/Documents", is_folder=True)

    # The returned dict must reference the actual (trailing-space) path
    assert result["id"].endswith("Kaufland "), (
        f"Expected ID to end with 'Kaufland ', got {result['id']!r}"
    )


# ── Integration: preserved path enables subsequent listdir ───────────────────

@pytest.mark.asyncio
async def test_find_child_folder_path_is_listable():
    """The path returned by find_child_folder must work as a listdir argument."""
    client = _make_client()
    trailing_node = _dir_node("Kaufland ", "Documents/Diverse Bills/Kaufland ")
    child_node = MagicMock()
    child_node.name = "receipt.pdf"
    child_node.is_dir = False
    child_node.user_path = "Documents/Diverse Bills/Kaufland /receipt.pdf"

    def _listdir(path):
        if path == "/Documents/Diverse Bills":
            return [trailing_node]
        if path == "/Documents/Diverse Bills/Kaufland ":
            return [child_node]
        raise FileNotFoundError(f"404: {path}")

    client._nc.files.listdir.side_effect = _listdir

    folder_path = await client.find_child_folder("/Documents/Diverse Bills", "Kaufland")
    assert folder_path == "/Documents/Diverse Bills/Kaufland "

    # Using the returned path directly for listdir must NOT raise 404
    result = client._nc.files.listdir(folder_path)
    assert len(result) == 1
    assert result[0].name == "receipt.pdf"
