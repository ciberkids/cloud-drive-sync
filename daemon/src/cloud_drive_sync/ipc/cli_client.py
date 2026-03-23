"""CLI client for communicating with the cloud-drive-sync daemon via IPC."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any


class CliClient:
    """JSON-RPC client that connects to the daemon via Unix socket or TCP."""

    def __init__(self) -> None:
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._request_id = 0

    async def connect(self, socket_path: str | Path | None = None) -> None:
        """Connect to the daemon via Unix socket (Linux/macOS) or TCP (Windows)."""
        if sys.platform == "win32":
            from cloud_drive_sync.util.paths import ipc_address

            host, port_file = ipc_address()
            if not port_file.exists():
                raise ConnectionError(
                    f"Daemon port file not found at {port_file}. "
                    "Is the daemon running? Start it with: cloud-drive-sync start"
                )
            port = int(port_file.read_text().strip())
            self._reader, self._writer = await asyncio.open_connection(host, port)
        else:
            if socket_path is None:
                from cloud_drive_sync.util.paths import ipc_address

                socket_path = ipc_address()
            socket_path = Path(socket_path)
            if not socket_path.exists():
                raise ConnectionError(
                    f"Daemon socket not found at {socket_path}. "
                    "Is the daemon running? Start it with: cloud-drive-sync start"
                )
            self._reader, self._writer = await asyncio.open_unix_connection(
                str(socket_path)
            )

    async def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Send a JSON-RPC request and return the result."""
        if self._writer is None or self._reader is None:
            raise ConnectionError("Not connected. Call connect() first.")

        self._request_id += 1
        request = {
            "jsonrpc": "2.0",
            "method": method,
            "id": self._request_id,
        }
        if params is not None:
            request["params"] = params

        data = json.dumps(request).encode() + b"\n"
        self._writer.write(data)
        await self._writer.drain()

        line = await self._reader.readline()
        if not line:
            raise ConnectionError("Daemon closed the connection.")

        response = json.loads(line)
        if "error" in response:
            err = response["error"]
            raise RuntimeError(f"RPC error ({err.get('code', '?')}): {err.get('message', 'Unknown error')}")

        return response.get("result")

    async def close(self) -> None:
        """Close the connection."""
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None
