"""Regression test for issue #38: Nextcloud uploads create 0-byte files.

Root cause: httpx sends Transfer-Encoding: chunked when no Content-Length is
set. Nextcloud WebDAV silently accepts the PUT but writes 0 bytes.
Fix: explicitly set Content-Length so httpx uses a fixed-length PUT instead.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_upload_includes_content_length(tmp_path: Path):
    """The httpx PUT for a Nextcloud upload must include Content-Length."""
    # Write a test file with known content
    local_file = tmp_path / "test.pdf"
    local_file.write_bytes(b"hello world")  # 11 bytes

    captured_headers: dict = {}

    def fake_put(url, content, auth, headers=None):
        captured_headers.update(headers or {})
        # Consume the generator so _gen() runs fully
        if hasattr(content, "__iter__"):
            for _ in content:
                pass
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        return resp

    from cloud_drive_sync.providers.nextcloud.client import NextcloudClient
    from cloud_drive_sync.providers.nextcloud.operations import NextcloudFileOps

    nc = MagicMock()
    node = MagicMock()
    node.name = "test.pdf"
    node.user_path = "/remote/test.pdf"
    node.is_dir = False
    nc.files.listdir.return_value = [node]

    client = NextcloudClient(nc, "https://cloud.example.com", "user", "pass")
    client._username = "user"
    client._app_password = "pass"
    ops = NextcloudFileOps(client)

    with patch("httpx.Client") as mock_httpx:
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = lambda s: mock_ctx
        mock_ctx.__exit__ = MagicMock(return_value=False)
        mock_ctx.put = fake_put
        mock_httpx.return_value = mock_ctx

        await ops.upload_file(local_path=local_file, remote_parent="/remote")

    assert "Content-Length" in captured_headers, (
        "httpx PUT must include Content-Length to avoid Nextcloud writing 0-byte files"
    )
    assert captured_headers["Content-Length"] == "11"
