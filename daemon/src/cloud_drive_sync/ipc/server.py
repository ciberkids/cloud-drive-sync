"""Cross-platform IPC server using asyncio (Unix sockets or TCP localhost)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from cloud_drive_sync.ipc.handlers import RequestHandler
from cloud_drive_sync.ipc.protocol import (
    INVALID_REQUEST,
    PARSE_ERROR,
    JsonRpcNotification,
    JsonRpcResponse,
    parse_message,
    serialize_message,
)
from cloud_drive_sync.util.logging import get_logger
from cloud_drive_sync.util.paths import ipc_address, runtime_dir

log = get_logger("ipc.server")


class IpcServer:
    """Asyncio-based IPC server for JSON-RPC (Unix socket on Linux/macOS, TCP on Windows)."""

    def __init__(self, handler: RequestHandler, path: Path | None = None) -> None:
        self._handler = handler
        self._server: asyncio.Server | None = None
        self._clients: set[asyncio.StreamWriter] = set()

        if sys.platform == "win32":
            host, port_file = ipc_address()
            self._host = host
            self._port_file = port_file
            self._path: Path | None = None
        else:
            self._path = path or ipc_address()
            self._host = None
            self._port_file = None

    async def start(self) -> None:
        """Start listening on Unix socket (Linux/macOS) or TCP localhost (Windows)."""
        if sys.platform == "win32":
            self._port_file.parent.mkdir(parents=True, exist_ok=True)
            self._server = await asyncio.start_server(
                self._handle_client, self._host, 0
            )
            # Write the assigned port to the port file
            port = self._server.sockets[0].getsockname()[1]
            self._port_file.write_text(str(port))
            log.info("IPC server listening on %s:%d", self._host, port)
        else:
            # Clean up stale socket
            if self._path.exists():
                self._path.unlink()

            self._path.parent.mkdir(parents=True, exist_ok=True)

            self._server = await asyncio.start_unix_server(
                self._handle_client, path=str(self._path)
            )
            # Set socket permissions to user-only
            self._path.chmod(0o600)
            log.info("IPC server listening on %s", self._path)

    async def stop(self) -> None:
        """Stop the server and disconnect all clients."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()

        for writer in list(self._clients):
            writer.close()

        if sys.platform == "win32":
            if self._port_file and self._port_file.exists():
                self._port_file.unlink()
        else:
            if self._path and self._path.exists():
                self._path.unlink()

        log.info("IPC server stopped")

    async def notify_all(self, method: str, params: dict) -> None:
        """Send a notification to all connected clients."""
        notification = JsonRpcNotification(method=method, params=params)
        data = serialize_message(notification)

        disconnected: list[asyncio.StreamWriter] = []
        for writer in self._clients:
            try:
                writer.write(data)
                await writer.drain()
            except (ConnectionError, OSError):
                disconnected.append(writer)

        for writer in disconnected:
            self._clients.discard(writer)

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle a single client connection."""
        self._clients.add(writer)
        peer = writer.get_extra_info("peername", "unknown")
        log.debug("Client connected: %s", peer)

        try:
            while True:
                line = await reader.readline()
                if not line:
                    break  # Client disconnected

                line = line.strip()
                if not line:
                    continue

                request = parse_message(line)
                if request is None:
                    response = JsonRpcResponse.fail(None, PARSE_ERROR, "Parse error")
                    writer.write(serialize_message(response))
                    await writer.drain()
                    continue

                if not request.method:
                    response = JsonRpcResponse.fail(
                        request.id, INVALID_REQUEST, "Missing method"
                    )
                    writer.write(serialize_message(response))
                    await writer.drain()
                    continue

                response = await self._handler.handle(request)

                # Only send response for requests (with id), not notifications
                if request.id is not None:
                    writer.write(serialize_message(response))
                    await writer.drain()

        except (ConnectionError, OSError):
            log.debug("Client disconnected: %s", peer)
        except Exception:
            log.exception("Error handling client %s", peer)
        finally:
            self._clients.discard(writer)
            writer.close()
