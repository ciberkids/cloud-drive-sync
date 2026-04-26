"""Regression tests for issue #33.

_build_etag_map (and the callers get_start_page_token / poll_changes) crashed
with ``AttributeError: 'FsNodeInfo' object has no attribute 'get'`` because
FsNodeInfo is a typed attribute object, not a dict.  The fix extracts all
needed values inside _build_etag_map using getattr(), so no caller ever
receives a FsNodeInfo object.

Tests use types.SimpleNamespace (no .get method) to reproduce the crash on
unfixed code and verify the fix.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from cloud_drive_sync.providers.nextcloud.changes import (
    NextcloudChangePoller,
    _build_etag_map,
)
from cloud_drive_sync.providers.nextcloud.client import NextcloudClient


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_fsnode_info(**kwargs) -> SimpleNamespace:
    """SimpleNamespace has no .get() — mirrors real FsNodeInfo behaviour."""
    return SimpleNamespace(**kwargs)


def _make_fsnode(name: str, user_path: str, is_dir: bool = False, **info_kwargs) -> SimpleNamespace:
    info_defaults = {
        "etag": "abc123",
        "fileid": 42,
        "mimetype": "application/pdf" if not is_dir else "",
        "last_modified": "2026-04-26T00:00:00Z",
        "size": 1024,
    }
    info_defaults.update(info_kwargs)
    node = SimpleNamespace(
        name=name,
        user_path=user_path,
        is_dir=is_dir,
        file_id=str(info_defaults["fileid"]),
        info=_make_fsnode_info(**info_defaults),
    )
    return node


def _make_client() -> NextcloudClient:
    nc = MagicMock()
    return NextcloudClient(nc, "https://cloud.example.com")


# ── _build_etag_map ───────────────────────────────────────────────────────────

def test_build_etag_map_file_node():
    """_build_etag_map must not call .get() on FsNodeInfo (issue #33)."""
    file_node = _make_fsnode("report.pdf", "Documents/report.pdf", is_dir=False)
    nc = MagicMock()
    nc.files.listdir.return_value = [file_node]

    result = _build_etag_map(nc, "/Documents")

    assert "Documents/report.pdf" in result
    entry = result["Documents/report.pdf"]
    assert entry["etag"] == "abc123"
    assert entry["fileid"] == "42"
    assert entry["name"] == "report.pdf"
    assert entry["is_dir"] is False
    assert entry["mimetype"] == "application/pdf"
    # "info" key must NOT be present — callers must not receive FsNodeInfo
    assert "info" not in entry


def test_build_etag_map_dir_node():
    """Directory nodes get mimetype httpd/unix-directory."""
    dir_node = _make_fsnode("Subfolder", "Documents/Subfolder", is_dir=True)
    child_node = _make_fsnode("file.txt", "Documents/Subfolder/file.txt", is_dir=False)

    nc = MagicMock()

    def _listdir(path):
        if path == "/Documents":
            return [dir_node]
        if path == "Documents/Subfolder":
            return [child_node]
        return []

    nc.files.listdir.side_effect = _listdir

    result = _build_etag_map(nc, "/Documents")

    assert result["Documents/Subfolder"]["mimetype"] == "httpd/unix-directory"
    assert result["Documents/Subfolder/file.txt"]["mimetype"] == "application/pdf"


def test_build_etag_map_empty_dir():
    nc = MagicMock()
    nc.files.listdir.return_value = []
    assert _build_etag_map(nc) == {}


def test_build_etag_map_listdir_exception():
    """A failing listdir returns an empty map (no crash)."""
    nc = MagicMock()
    nc.files.listdir.side_effect = Exception("network error")
    assert _build_etag_map(nc) == {}


# ── get_start_page_token ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_start_page_token_returns_json():
    """get_start_page_token must return a valid JSON token (not crash)."""
    client = _make_client()
    file_node = _make_fsnode("doc.pdf", "doc.pdf", is_dir=False)
    client._nc.files.listdir.return_value = [file_node]

    poller = NextcloudChangePoller(client)
    token = await poller.get_start_page_token()

    parsed = json.loads(token)
    assert "etags" in parsed
    assert isinstance(parsed["etags"], dict)


@pytest.mark.asyncio
async def test_get_start_page_token_empty_tree():
    client = _make_client()
    client._nc.files.listdir.return_value = []

    poller = NextcloudChangePoller(client)
    token = await poller.get_start_page_token()
    assert json.loads(token) == {"etags": {}}


# ── poll_changes ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_poll_changes_detects_new_file():
    """A file present in current but absent in old_token is a new-file change."""
    client = _make_client()
    file_node = _make_fsnode("new.pdf", "Documents/new.pdf", is_dir=False)
    client._nc.files.listdir.return_value = [file_node]

    poller = NextcloudChangePoller(client)
    old_token = json.dumps({"etags": {}})
    changes, new_token = await poller.poll_changes(old_token)

    assert len(changes) == 1
    assert changes[0].file_name == "new.pdf"
    assert changes[0].removed is False

    new_state = json.loads(new_token)
    assert "Documents/new.pdf" in new_state["etags"]


@pytest.mark.asyncio
async def test_poll_changes_detects_removed_file():
    """A file in old_token absent from current is a removal."""
    client = _make_client()
    client._nc.files.listdir.return_value = []

    poller = NextcloudChangePoller(client)
    old_token = json.dumps({"etags": {"Documents/gone.pdf": "etag1"}})
    changes, _ = await poller.poll_changes(old_token)

    assert len(changes) == 1
    assert changes[0].removed is True
    assert changes[0].file_name == "gone.pdf"


@pytest.mark.asyncio
async def test_poll_changes_detects_modified_file():
    """A file whose etag changed is reported as a modification."""
    client = _make_client()
    file_node = _make_fsnode("doc.pdf", "Documents/doc.pdf", etag="new_etag")
    client._nc.files.listdir.return_value = [file_node]

    poller = NextcloudChangePoller(client)
    old_token = json.dumps({"etags": {"Documents/doc.pdf": "old_etag"}})
    changes, _ = await poller.poll_changes(old_token)

    assert len(changes) == 1
    assert changes[0].file_name == "doc.pdf"
    assert changes[0].removed is False


@pytest.mark.asyncio
async def test_poll_changes_no_changes():
    """Unchanged etag → no changes reported."""
    client = _make_client()
    file_node = _make_fsnode("doc.pdf", "Documents/doc.pdf", etag="same_etag")
    client._nc.files.listdir.return_value = [file_node]

    poller = NextcloudChangePoller(client)
    old_token = json.dumps({"etags": {"Documents/doc.pdf": "same_etag"}})
    changes, _ = await poller.poll_changes(old_token)

    assert changes == []


# ── _extract_md5 ──────────────────────────────────────────────────────────────

def test_extract_md5_parses_checksum():
    assert NextcloudChangePoller._extract_md5("MD5:deadbeef") == "deadbeef"


def test_extract_md5_empty():
    assert NextcloudChangePoller._extract_md5("") == ""


def test_extract_md5_no_md5_prefix():
    assert NextcloudChangePoller._extract_md5("SHA1:abc") == ""
