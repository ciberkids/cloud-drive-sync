"""Integration test: Nextcloud provider against a real container.

Run with:
    pytest tests/test_integration_nextcloud.py -m nextcloud -v

Requires a container runtime (docker or podman) on PATH.  The test spins up a
minimal Nextcloud instance (SQLite, auto-installed via env vars), runs the full
auth flow, and exercises the CloudClient methods against it.

Automatically skipped when no container runtime is found or the container fails
to start.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
import urllib.request
from collections.abc import Iterator

import pytest

pytestmark = pytest.mark.nextcloud

_IMAGE = "nextcloud:30-apache"
_ADMIN_USER = "admin"
_ADMIN_PASS = "testpassword123"  # test-only credential for a throwaway container
_PORT = 18080
_CONTAINER = "cds-test-nextcloud"


def _runtime() -> str | None:
    for cmd in ("docker", "podman"):
        if shutil.which(cmd):
            return cmd
    return None


@pytest.fixture(scope="module")
def nc_url() -> Iterator[str]:
    """Start a Nextcloud container and yield its base URL, then stop it."""
    rt = _runtime()
    if rt is None:
        pytest.skip("No container runtime (docker/podman) on PATH")

    # Remove any leftover container from a previous interrupted run
    subprocess.run([rt, "rm", "-f", _CONTAINER], capture_output=True, check=False)

    result = subprocess.run(
        [
            rt, "run", "-d",
            "--name", _CONTAINER,
            "-p", f"{_PORT}:80",
            "-e", f"NEXTCLOUD_ADMIN_USER={_ADMIN_USER}",
            "-e", f"NEXTCLOUD_ADMIN_PASSWORD={_ADMIN_PASS}",
            _IMAGE,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"Container start failed: {result.stderr.strip()}")

    url = f"http://localhost:{_PORT}"

    # Poll /status.php until Nextcloud finishes its first-run installation (up to 120 s)
    deadline = time.monotonic() + 120
    ready = False
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/status.php", timeout=5) as resp:
                if json.loads(resp.read()).get("installed"):
                    ready = True
                    break
        except Exception:
            pass
        time.sleep(3)

    if not ready:
        subprocess.run([rt, "rm", "-f", _CONTAINER], capture_output=True, check=False)
        pytest.skip("Nextcloud did not become ready within 120 s")

    yield url

    subprocess.run([rt, "rm", "-f", _CONTAINER], capture_output=True, check=False)


class TestNextcloudContainerAuth:
    def test_auth_flow_succeeds(self, nc_url: str) -> None:
        from cloud_drive_sync.providers.nextcloud.auth import NextcloudAuth

        auth = NextcloudAuth()
        creds = auth.run_auth_flow(
            extra={
                "server_url": nc_url,
                "username": _ADMIN_USER,
                "app_password": _ADMIN_PASS,
            }
        )
        assert creds["server_url"] == nc_url
        assert creds["username"] == _ADMIN_USER
        assert creds["app_password"] == _ADMIN_PASS

    def test_wrong_password_raises_runtime_error(self, nc_url: str) -> None:
        from cloud_drive_sync.providers.nextcloud.auth import NextcloudAuth

        auth = NextcloudAuth()
        with pytest.raises(RuntimeError, match="Failed to authenticate"):
            auth.run_auth_flow(
                extra={
                    "server_url": nc_url,
                    "username": _ADMIN_USER,
                    "app_password": "wrong_password_xyz",
                }
            )

    def test_trailing_slash_stripped_in_live_url(self, nc_url: str) -> None:
        from cloud_drive_sync.providers.nextcloud.auth import NextcloudAuth

        auth = NextcloudAuth()
        creds = auth.run_auth_flow(
            extra={
                "server_url": nc_url + "/",
                "username": _ADMIN_USER,
                "app_password": _ADMIN_PASS,
            }
        )
        assert not creds["server_url"].endswith("/")

    @pytest.mark.asyncio
    async def test_create_client_and_list_root(self, nc_url: str) -> None:
        from cloud_drive_sync.providers.nextcloud.auth import NextcloudAuth

        auth = NextcloudAuth()
        creds = auth.run_auth_flow(
            extra={
                "server_url": nc_url,
                "username": _ADMIN_USER,
                "app_password": _ADMIN_PASS,
            }
        )
        client = await auth.create_client(creds)
        result = await client.list_files(folder_id="root")
        assert "files" in result
        assert isinstance(result["files"], list)

    @pytest.mark.asyncio
    async def test_get_about_returns_user_info(self, nc_url: str) -> None:
        from cloud_drive_sync.providers.nextcloud.auth import NextcloudAuth

        auth = NextcloudAuth()
        creds = auth.run_auth_flow(
            extra={
                "server_url": nc_url,
                "username": _ADMIN_USER,
                "app_password": _ADMIN_PASS,
            }
        )
        client = await auth.create_client(creds)
        about = await client.get_about()
        assert "user" in about
        assert "storageQuota" in about
        user_info = about["user"]
        # Either displayName or emailAddress must be present
        assert user_info.get("displayName") or user_info.get("emailAddress")
