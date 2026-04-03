# Daemon

The Python daemon that performs bidirectional Google Drive synchronization.

## Overview

The daemon runs on Linux, macOS, and Windows as a background process (or systemd user service on Linux) and handles:

- Watching local directories for changes (via watchdog)
- Polling Google Drive for remote changes
- Planning and executing sync operations (upload, download, delete)
- Detecting and resolving conflicts
- Serving an IPC interface over a Unix domain socket

## CLI Usage

```
cloud-drive-sync-daemon [OPTIONS] COMMAND
```

### Global Options

| Option | Description |
|---|---|
| `--config PATH` | Path to `config.toml` (default: `~/.config/cloud-drive-sync/config.toml`) |
| `--log-level LEVEL` | Override log level: `debug`, `info`, `warning`, `error` |

### Commands

#### `start`

Start the sync daemon.

```bash
cloud-drive-sync-daemon start              # Daemonize (fork to background, Linux/macOS only)
cloud-drive-sync-daemon start --foreground  # Run in foreground (for development/systemd)
cloud-drive-sync-daemon start --demo        # Run with mock Drive API (no Google account needed)
```

| Flag | Description |
|---|---|
| `--foreground` | Run in the foreground instead of forking |
| `--demo` | Use mock Drive client with synthetic test data |
| `--config PATH` | Path to config file |

> **Windows note:** The daemon always runs in foreground mode on Windows (fork is not supported). The Tauri UI manages the daemon lifecycle as a sidecar process.

#### `stop`

Stop a running daemon by sending SIGTERM.

```bash
cloud-drive-sync-daemon stop
```

#### `status`

Check whether the daemon is running.

```bash
cloud-drive-sync-daemon status
# Output: "Daemon is running (PID 12345)" or "Daemon is not running."
```

#### `auth`

Run the OAuth2 authorization flow interactively.

```bash
cloud-drive-sync-daemon auth
# Output: "Authorization successful. Credentials stored and ready to use."
```

## Configuration Reference

The daemon reads configuration from a platform-specific path (or the path specified by `--config`). All values have sensible defaults.

| Platform | Default config path |
|---|---|
| Linux | `~/.config/cloud-drive-sync/config.toml` |
| macOS | `~/Library/Application Support/cloud-drive-sync/config.toml` |
| Windows | `%APPDATA%\cloud-drive-sync\config.toml` |

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
cloud-drive-sync-daemon start --foreground --demo
```

What demo mode does:

- Creates a temporary local sync directory with sample files
- Simulates remote files and changes
- Processes sync operations (upload/download/conflict) against the mock backend
- Responds to all IPC commands normally

This allows the UI to be fully tested without a Google account or network access.

## Headless Authentication

All providers support headless auth for servers and Docker containers:

```bash
# Google Drive (console flow — prints URL, paste authorization code)
cloud-drive-sync account add --provider gdrive --headless

# OneDrive (device code flow — prints code, authorize on another device)
cloud-drive-sync account add --provider onedrive --headless

# Dropbox (prints URL, paste authorization code)
cloud-drive-sync account add --provider dropbox --headless

# Nextcloud (prompts for server URL, username, app password)
cloud-drive-sync account add --provider nextcloud --headless

# Box (prints URL, paste authorization code)
cloud-drive-sync account add --provider box --headless
```

The `--headless` flag disables automatic browser opening. The daemon prints a URL or device code to the console, and you complete authorization on any device with a browser.

## Docker Deployment

The daemon runs headless in Docker with no GUI dependencies.

### Quick Start

```bash
docker run -d --name cloud-drive-sync \
  -p 8080:8080 \
  -v cloud-drive-sync-config:/root/.config/cloud-drive-sync \
  -v cloud-drive-sync-data:/root/.local/share/cloud-drive-sync \
  -v ~/Documents:/data/Documents \
  ghcr.io/ciberkids/cloud-drive-sync:latest

# Open http://localhost:8080/ for the web management UI

# Add account (interactive — prints auth URL)
docker exec -it cloud-drive-sync \
  python -m cloud_drive_sync account add --provider gdrive --headless

# Check status
docker exec cloud-drive-sync python -m cloud_drive_sync status
```

### HTTP REST API

The daemon can expose an HTTP REST API with a built-in web UI for headless and Docker management.

- Enable with the `--http-port` flag: `cloud-drive-sync-daemon start --foreground --http-port 8080`
- Docker containers enable it by default on port 8080.
- **Web UI**: http://localhost:8080/
- **REST API**: http://localhost:8080/api/*

#### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/status` | Daemon and sync status |
| GET | `/api/accounts` | List all accounts |
| POST | `/api/accounts` | Add a new account |
| GET | `/api/pairs` | List sync pairs |
| POST | `/api/pairs` | Add a sync pair |
| DELETE | `/api/pairs/:id` | Remove a sync pair |
| POST | `/api/sync` | Trigger an immediate sync |
| GET | `/api/activity` | Recent sync activity log |
| GET | `/api/conflicts` | List unresolved conflicts |
| GET | `/api/settings/:key` | Read a setting |
| PUT | `/api/settings/:key` | Update a setting |

#### Example curl commands

```bash
# Check status
curl http://localhost:8080/api/status

# List sync pairs
curl http://localhost:8080/api/pairs

# Trigger a sync
curl -X POST http://localhost:8080/api/sync

# List accounts
curl http://localhost:8080/api/accounts

# Get recent activity
curl http://localhost:8080/api/activity

# Update a setting
curl -X PUT http://localhost:8080/api/settings/poll_interval \
  -H "Content-Type: application/json" \
  -d '{"value": 60}'
```

### Docker Compose

See `docker/docker-compose.yml` for a ready-to-use compose file.

### Volumes

| Mount | Purpose |
|-------|---------|
| `/root/.config/cloud-drive-sync` | Config (config.toml) |
| `/root/.local/share/cloud-drive-sync` | Credentials, database |
| `/run/cloud-drive-sync` | IPC socket (for CLI from host) |
| `/data/*` | Sync folder mount points |

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `XDG_RUNTIME_DIR` | `/run/cloud-drive-sync` | IPC socket directory |
| `CDS_GOOGLE_CLIENT_ID` | (embedded) | Override Google OAuth client ID |
| `CDS_GOOGLE_CLIENT_SECRET` | (embedded) | Override Google OAuth client secret |

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
python -m cloud_drive_sync --log-level debug start --foreground

# With demo mode
python -m cloud_drive_sync start --foreground --demo
```

### Run Tests

```bash
pytest -v
pytest --cov=cloud_drive_sync  # With coverage
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
