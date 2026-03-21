"""Tests for RemoteChange data model and ChangePoller._parse_change."""

from __future__ import annotations

from cloud_drive_sync.drive.changes import ChangePoller, RemoteChange


class TestRemoteChange:
    def test_defaults(self):
        rc = RemoteChange(file_id="id1")
        assert rc.file_id == "id1"
        assert rc.file_name is None
        assert rc.mime_type is None
        assert rc.md5 is None
        assert rc.modified_time is None
        assert rc.removed is False
        assert rc.trashed is False
        assert rc.parents == []

    def test_full_construction(self):
        rc = RemoteChange(
            file_id="id1",
            file_name="doc.pdf",
            mime_type="application/pdf",
            md5="abc123",
            modified_time="2025-01-01T00:00:00Z",
            removed=False,
            trashed=False,
            parents=["parent1"],
        )
        assert rc.file_name == "doc.pdf"
        assert rc.md5 == "abc123"
        assert rc.parents == ["parent1"]

    def test_removed_change(self):
        rc = RemoteChange(file_id="id1", removed=True, trashed=True)
        assert rc.removed is True
        assert rc.trashed is True


class TestParseChange:
    def test_parse_normal_change(self):
        data = {
            "fileId": "abc",
            "removed": False,
            "file": {
                "id": "abc",
                "name": "report.pdf",
                "mimeType": "application/pdf",
                "md5Checksum": "hash123",
                "modifiedTime": "2025-06-01T12:00:00Z",
                "parents": ["root"],
                "trashed": False,
            },
        }
        change = ChangePoller._parse_change(data)
        assert change.file_id == "abc"
        assert change.file_name == "report.pdf"
        assert change.mime_type == "application/pdf"
        assert change.md5 == "hash123"
        assert change.modified_time == "2025-06-01T12:00:00Z"
        assert change.parents == ["root"]
        assert change.removed is False
        assert change.trashed is False

    def test_parse_removed_change(self):
        data = {
            "fileId": "xyz",
            "removed": True,
            "file": {},
        }
        change = ChangePoller._parse_change(data)
        assert change.file_id == "xyz"
        assert change.removed is True

    def test_parse_trashed_change(self):
        data = {
            "fileId": "t1",
            "removed": False,
            "file": {
                "name": "old.txt",
                "trashed": True,
            },
        }
        change = ChangePoller._parse_change(data)
        assert change.trashed is True

    def test_parse_missing_file_data(self):
        data = {
            "fileId": "nf",
            "removed": False,
        }
        change = ChangePoller._parse_change(data)
        assert change.file_id == "nf"
        assert change.file_name is None
        assert change.md5 is None
        assert change.parents == []

    def test_parse_missing_optional_fields(self):
        data = {
            "fileId": "partial",
            "file": {
                "name": "partial.txt",
            },
        }
        change = ChangePoller._parse_change(data)
        assert change.file_name == "partial.txt"
        assert change.md5 is None
        assert change.mime_type is None
        assert change.removed is False
