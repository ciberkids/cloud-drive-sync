"""Tests for Unicode NFC/NFD normalisation in Nextcloud client/ops — issue #25."""

from __future__ import annotations

import unicodedata
from unittest.mock import MagicMock

import pytest

from cloud_drive_sync.providers.nextcloud.client import NextcloudClient


def _make_client() -> NextcloudClient:
    nc = MagicMock()
    return NextcloudClient(nc, "https://cloud.example.com")


# ── Helpers ──────────────────────────────────────────────────────────────────

NFD = unicodedata.normalize  # shorthand for tests

FOLDER_NAME_NFC = "Flat Rümlang_Schaeppi"              # NFC: ü = U+00FC
FOLDER_NAME_NFD = unicodedata.normalize("NFD", FOLDER_NAME_NFC)  # NFD: u + combining ¨


def _dir_node(name: str) -> MagicMock:
    node = MagicMock()
    node.name = name
    node.is_dir = True
    node.user_path = f"Documents/{name}"
    return node


def _file_node(name: str) -> MagicMock:
    node = MagicMock()
    node.name = name
    node.is_dir = False
    node.user_path = f"Documents/{name}"
    return node


# ── create_file (is_folder=True) ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_folder_finds_nfc_node_after_mkdir():
    """create_file normalises name to NFC; comparison succeeds when server returns NFC."""
    client = _make_client()

    nfc_node = _dir_node(FOLDER_NAME_NFC)  # server returns NFC
    client._nc.files.mkdir.return_value = None
    client._nc.files.listdir.return_value = [nfc_node]
    # Stub _file_to_dict to return something recognisable
    client._file_to_dict = MagicMock(return_value={"id": "/Documents/" + FOLDER_NAME_NFC, "name": FOLDER_NAME_NFC})

    result = await client.create_file(FOLDER_NAME_NFD, "/Documents", is_folder=True)

    assert result["name"] == FOLDER_NAME_NFC


@pytest.mark.asyncio
async def test_create_folder_nfd_input_normalised_to_nfc_in_path():
    """create_file normalises name before constructing the MKCOL target path."""
    client = _make_client()

    captured_path = []

    def fake_mkdir(path):
        captured_path.append(path)

    nfc_node = _dir_node(FOLDER_NAME_NFC)
    client._nc.files.mkdir.side_effect = fake_mkdir
    client._nc.files.listdir.return_value = [nfc_node]
    client._file_to_dict = MagicMock(return_value={"id": "/Documents/" + FOLDER_NAME_NFC})

    await client.create_file(FOLDER_NAME_NFD, "/Documents", is_folder=True)

    assert captured_path, "mkdir was not called"
    path_sent = captured_path[0]
    # Path must be NFC-normalised (no combining characters)
    assert path_sent == unicodedata.normalize("NFC", path_sent), (
        "MKCOL path contains NFD characters"
    )


# ── find_child_folder ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_find_child_folder_matches_nfc_node_with_nfd_name():
    """find_child_folder matches even when name is NFD but server stores NFC."""
    client = _make_client()
    client._nc.files.listdir.return_value = [_dir_node(FOLDER_NAME_NFC)]

    result = await client.find_child_folder("/Documents", FOLDER_NAME_NFD)

    assert result is not None, "Should have found the NFC-named folder"
    assert result.startswith("/")


@pytest.mark.asyncio
async def test_find_child_folder_matches_nfd_node_with_nfc_name():
    """find_child_folder handles the reverse: server (unexpectedly) stores NFD."""
    client = _make_client()
    client._nc.files.listdir.return_value = [_dir_node(FOLDER_NAME_NFD)]

    result = await client.find_child_folder("/Documents", FOLDER_NAME_NFC)

    assert result is not None, "Should have found the NFD-named folder via NFC normalisation"


# ── upload_file in operations.py ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_upload_file_finds_node_with_nfd_filename():
    """upload_file must find the uploaded node even when filename has diacritics."""
    from cloud_drive_sync.providers.nextcloud.operations import NextcloudFileOps

    client = _make_client()
    ops = NextcloudFileOps(client)

    file_name_nfd = unicodedata.normalize("NFD", "Prämienrechnung.pdf")
    file_name_nfc = unicodedata.normalize("NFC", file_name_nfd)

    # Server returns NFC node after upload
    result_node = _file_node(file_name_nfc)
    result_node.is_dir = False

    import os
    import sys
    import tempfile
    from pathlib import Path
    from types import ModuleType
    from unittest.mock import patch as _patch, MagicMock as MM

    async def fake_to_thread(fn, *args, **kwargs):
        return fn()

    # Build a minimal fake httpx module so the test works whether or not
    # httpx is installed in the test environment.
    mock_response = MM()
    mock_response.raise_for_status.return_value = None
    mock_http_client = MM()
    mock_http_client.__enter__ = MM(return_value=mock_http_client)
    mock_http_client.__exit__ = MM(return_value=False)
    mock_http_client.put.return_value = mock_response

    fake_httpx = ModuleType("httpx")
    fake_httpx.Client = MM(return_value=mock_http_client)  # type: ignore[attr-defined]

    with (
        _patch("asyncio.to_thread", side_effect=fake_to_thread),
        _patch.dict(sys.modules, {"httpx": fake_httpx}),
    ):
        client._nc.files.listdir.return_value = [result_node]
        client._file_to_dict = MM(return_value={"id": "/Documents/" + file_name_nfc, "name": file_name_nfc})
        client._resolve_path = MM(return_value="/Documents")  # type: ignore
        client._username = "testuser"
        client._app_password = "testpass"

        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as f:
            f.write(b"PDF content")
            tmp = f.name

        try:
            result = await ops.upload_file(
                local_path=Path(tmp),
                remote_parent="/Documents",
                remote_name=file_name_nfd,
            )
            assert result["name"] == file_name_nfc
        finally:
            os.unlink(tmp)
