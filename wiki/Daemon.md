# Daemon

The Python daemon that performs bidirectional Google Drive synchronization.

## Overview

The daemon runs as a background process (or systemd user service) and handles:

- Watching local directories for changes (via watchdog)
- Polling Google Drive for remote changes
- Planning and executing sync operations (upload, download, delete)
- Detecting and resolving conflicts
- Serving an IPC interface over a Unix domain socket

## CLI Usage

```
gdrive-sync-daemon [OPTIONS] COMMAND
```

### Global Options

| Option | Description |
|---|---|
| `--config PATH` | Path to `config.toml` (default: `~/.config/gdrive-sync/config.toml`) |
| `--log-level LEVEL` | Override log level: `debug`, `info`, `warning`, `error` |

### Commands

#### `start`

Start the sync daemon.

```bash
gdrive-sync-daemon start              # Daemonize (fork to background)
gdrive-sync-daemon start --foreground  # Run in foreground (for development/systemd)
gdrive-sync-daemon start --demo        # Run with mock Drive API (no Google account needed)
```

| Flag | Description |
|---|---|
| `--foreground` | Run in the foreground instead of forking |
| `--demo` | Use mock Drive client with synthetic test data |
| `--config PATH` | Path to config file |

#### `stop`

Stop a running daemon by sending SIGTERM.

```bash
gdrive-sync-daemon stop
```

#### `status`

Check whether the daemon is running.

```bash
gdrive-sync-daemon status
# Output: "Daemon is running (PID 12345)" or "Daemon is not running."
```

#### `auth`

Run the OAuth2 authorization flow interactively.

```bash
gdrive-sync-daemon auth
# Output: "Authorization successful. Credentials stored and ready to use."
```

## Configuration Reference

The daemon reads configuration from `~/.config/gdrive-sync/config.toml` (or the path specified by `--config`). All values have sensible defaults.

### `[general]`

| Key | Type | Default | Description |
|---|---|---|---|
| `log_level` | string | `"info"` | Logging level: `debug`, `info`, `warning`, `error` |

### `[sync]`

| Key | Type | Default | Description |
|---|---|---|---|
| `poll_interval` | integer | `30` | Seconds between remote change polls |
| `conflict_strategy` | string | `"keep_both"` | How to handle conflicts: `keep_both`, `newest_wins`, `ask_user` |
| `max_concurrent_transfers` | integer | `4` | Max simultaneous upload/download operations |
| `debounce_delay` | float | `1.0` | Seconds to wait before processing a local change (coalesces rapid edits) |

### `[[sync.pairs]]`

Each `[[sync.pairs]]` entry defines a local-to-remote folder mapping.

| Key | Type | Default | Description |
|---|---|---|---|
| `local_path` | string | (required) | Absolute path to the local directory |
| `remote_folder_id` | string | `"root"` | Google Drive folder ID (`"root"` = My Drive top level) |
| `enabled` | boolean | `true` | Whether this pair should be synced |
| `sync_mode` | string | `"two_way"` | Sync direction: `"two_way"`, `"upload_only"`, or `"download_only"` |
| `ignore_hidden` | boolean | `true` | Whether to exclude hidden files/directories (names starting with `.`) from sync |

### Example Configuration

```toml
[general]
log_level = "info"

[sync]
poll_interval = 30
conflict_strategy = "keep_both"
max_concurrent_transfers = 4
debounce_delay = 1.0

[[sync.pairs]]
local_path = "/home/user/Documents"
remote_folder_id = "root"
enabled = true
sync_mode = "two_way"
ignore_hidden = true

[[sync.pairs]]
local_path = "/home/user/Pictures"
remote_folder_id = "0A3xRemoteFolderIdHere"
enabled = true
sync_mode = "upload_only"
ignore_hidden = true
```

## Demo Mode

Demo mode runs the full daemon with a mock Drive client instead of connecting to Google's API:

```bash
gdrive-sync-daemon start --foreground --demo
```

What demo mode does:

- Creates a temporary local sync directory with sample files
- Simulates remote files and changes
- Processes sync operations (upload/download/conflict) against the mock backend
- Responds to all IPC commands normally

This allows the UI to be fully tested without a Google account or network access.

## Development

### Setup

```bash
cd daemon
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Run in Development

```bash
# With real Drive API
python -m gdrive_sync --log-level debug start --foreground

# With demo mode
python -m gdrive_sync start --foreground --demo
```

### Run Tests

```bash
pytest -v
pytest --cov=gdrive_sync  # With coverage
```

### Lint

```bash
ruff check src/ tests/
```

## Architecture

The daemon is structured as a set of asyncio components:

```
Daemon
  ├── Config (TOML loader)
  ├── Database (async SQLite via aiosqlite)
  ├── DriveClient (Google API v3 wrapper)
  ├── SyncEngine
  │     ├── DirectoryWatcher (per pair, watchdog-based)
  │     ├── ChangePoller (per pair, Drive Changes API)
  │     ├── SyncPlanner (diff + action planning)
  │     ├── SyncExecutor (concurrent transfer runner)
  │     └── ConflictResolver (strategy dispatch)
  └── IpcServer (Unix socket, JSON-RPC 2.0)
        └── RequestHandler (method dispatch)
```

For full architectural details, see the [[Architecture]] page.

## Dependencies

| Package | Version | Purpose |
|---|---|---|
| google-api-python-client | >=2.100.0 | Google Drive API v3 |
| google-auth-oauthlib | >=1.1.0 | OAuth2 flow |
| google-auth-httplib2 | >=0.2.0 | HTTP transport for Google auth |
| watchdog | >=4.0.0 | Filesystem event monitoring |
| aiosqlite | >=0.19.0 | Async SQLite |
| aiofiles | >=23.2.0 | Async file I/O |
| tomli-w | >=1.0.0 | TOML writing |
| click | >=8.1.0 | CLI framework |
| cryptography | >=41.0.0 | Credential encryption |

### Dev Dependencies

| Package | Version | Purpose |
|---|---|---|
| pytest | >=7.4.0 | Test framework |
| pytest-asyncio | >=0.21.0 | Async test support |
| pytest-cov | >=4.1.0 | Coverage reporting |
| ruff | >=0.1.0 | Linter and formatter |
