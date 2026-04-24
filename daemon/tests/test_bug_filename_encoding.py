"""Regression tests for issues #30 and #31.

#30: % in filename → double-encoding on WebDAV PUT (400 Bad Request).
#31: \\r in filename → Invalid non-printable ASCII character in URL.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock as MM, patch as _patch

import pytest

from cloud_drive_sync.providers.nextcloud.client import NextcloudClient
from cloud_drive_sync.providers.nextcloud.operations import NextcloudFileOps, _sanitise_name


# ── _sanitise_name unit tests ─────────────────────────────────────────────────

def test_sanitise_name_removes_carriage_return():
    assert _sanitise_name("foo\rbar") == "foobar"


def test_sanitise_name_removes_newline():
    assert _sanitise_name("foo\nbar") == "foobar"


def test_sanitise_name_removes_null():
    assert _sanitise_name("foo\x00bar") == "foobar"


def test_sanitise_name_passthrough_percent():
    # % is not a control char — _sanitise_name must leave it intact
    assert _sanitise_name("invoice 75%.pdf") == "invoice 75%.pdf"


def test_sanitise_name_passthrough_clean():
    name = "Prämiern_2024.pdf"
    assert _sanitise_name(name) == name


# ── URL encoding in upload_file ───────────────────────────────────────────────

def _make_client() -> NextcloudClient:
    nc = MM()
    nc.user = "testuser"
    return NextcloudClient(nc, "https://cloud.example.com", username="testuser", app_password="secret")


def _make_ops() -> NextcloudFileOps:
    return NextcloudFileOps(_make_client())


def _fake_httpx_module():
    mock_response = MM()
    mock_response.raise_for_status.return_value = None
    mock_http_client = MM()
    mock_http_client.__enter__ = MM(return_value=mock_http_client)
    mock_http_client.__exit__ = MM(return_value=False)
    mock_http_client.put.return_value = mock_response
    fake = ModuleType("httpx")
    fake.Client = MM(return_value=mock_http_client)  # type: ignore[attr-defined]
    return fake, mock_http_client


async def _run_upload(ops, filename):
    fake_httpx, mock_http_client = _fake_httpx_module()

    result_node = MM()
    result_node.name = filename
    result_node.is_dir = False

    async def fake_to_thread(fn, *args, **kwargs):
        return fn()

    with (
        _patch("asyncio.to_thread", side_effect=fake_to_thread),
        _patch.dict(sys.modules, {"httpx": fake_httpx}),
    ):
        ops._client._nc.files.listdir.return_value = [result_node]
        ops._client._file_to_dict = MM(return_value={"id": f"/Documents/{filename}", "name": filename})
        ops._client._resolve_path = MM(return_value="/Documents")

        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as f:
            f.write(b"dummy")
            tmp = f.name
        try:
            result = await ops.upload_file(
                local_path=Path(tmp),
                remote_parent="/Documents",
                remote_name=filename,
            )
        finally:
            os.unlink(tmp)

    return result, mock_http_client


@pytest.mark.asyncio
async def test_percent_in_filename_encoded_in_url():
    """Issue #30: % in filename must be percent-encoded to %25 in the PUT URL."""
    ops = _make_ops()
    filename = "Mietvertrags-Anderung per 01.04.2024 auf 1.75%.pdf"
    _, mock_http_client = await _run_upload(ops, filename)

    call_args = mock_http_client.put.call_args
    url_used = call_args[0][0] if call_args[0] else call_args[1].get("url", "")
    # The raw % must have been encoded to %25
    assert "%" not in url_used.split("/remote.php/dav/files/", 1)[1].replace("%25", "").replace("%20", "").replace("%2F", ""), (
        f"Unencoded % found in URL: {url_used}"
    )
    assert "%25" in url_used, f"Expected %25 (encoded %) in URL but got: {url_used}"


@pytest.mark.asyncio
async def test_carriage_return_stripped_from_filename():
    """Issue #31: \\r in filename must be stripped before URL construction."""
    ops = _make_ops()
    filename_raw = "Scan\r2024.pdf"
    filename_clean = "Scan2024.pdf"

    result_node = MM()
    result_node.name = filename_clean
    result_node.is_dir = False

    fake_httpx, mock_http_client = _fake_httpx_module()

    async def fake_to_thread(fn, *args, **kwargs):
        return fn()

    with (
        _patch("asyncio.to_thread", side_effect=fake_to_thread),
        _patch.dict(sys.modules, {"httpx": fake_httpx}),
    ):
        ops._client._nc.files.listdir.return_value = [result_node]
        ops._client._file_to_dict = MM(return_value={"id": f"/Documents/{filename_clean}", "name": filename_clean})
        ops._client._resolve_path = MM(return_value="/Documents")

        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as f:
            f.write(b"dummy")
            tmp = f.name
        try:
            result = await ops.upload_file(
                local_path=Path(tmp),
                remote_parent="/Documents",
                remote_name=filename_raw,
            )
        finally:
            os.unlink(tmp)

    call_args = mock_http_client.put.call_args
    url_used = call_args[0][0] if call_args[0] else call_args[1].get("url", "")
    assert "\r" not in url_used, f"Raw \\r found in PUT URL: {url_used!r}"
    assert result["name"] == filename_clean
