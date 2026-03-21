"""Tests for Shared Drives support."""
import pytest
from unittest.mock import MagicMock, patch
from cloud_drive_sync.drive.client import DriveClient


class TestSharedDrivesFlags:
    """Verify supportsAllDrives is passed to API calls."""

    @pytest.fixture
    def mock_service(self):
        service = MagicMock()
        # Set up chainable mock
        for method_name in ['list', 'get', 'create', 'update', 'delete']:
            getattr(service.files(), method_name).return_value.execute.return_value = {
                "files": [], "id": "test", "name": "test"
            }
        service.changes().getStartPageToken.return_value.execute.return_value = {
            "startPageToken": "1"
        }
        service.changes().list.return_value.execute.return_value = {
            "changes": [], "newStartPageToken": "2"
        }
        service.drives().list.return_value.execute.return_value = {
            "drives": [{"id": "sd1", "name": "Team Drive 1"}]
        }
        return service

    @pytest.mark.asyncio
    async def test_list_files_passes_flag(self, mock_service):
        with patch.object(DriveClient, '__init__', lambda self, creds: None):
            client = DriveClient.__new__(DriveClient)
            client._service = mock_service
            import threading
            client._api_lock = threading.Lock()

            await client.list_files("root")
            mock_service.files().list.assert_called_once()
            call_kwargs = mock_service.files().list.call_args
            assert call_kwargs.kwargs.get('supportsAllDrives') is True or \
                   (call_kwargs[1] if len(call_kwargs) > 1 else {}).get('supportsAllDrives') is True

    @pytest.mark.asyncio
    async def test_list_shared_drives(self, mock_service):
        with patch.object(DriveClient, '__init__', lambda self, creds: None):
            client = DriveClient.__new__(DriveClient)
            client._service = mock_service
            import threading
            client._api_lock = threading.Lock()

            drives = await client.list_shared_drives()
            assert len(drives) == 1
            assert drives[0]["name"] == "Team Drive 1"
