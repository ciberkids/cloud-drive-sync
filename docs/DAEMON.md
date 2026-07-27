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

The `--headless` flag disables automatic browser opening. Instead, the daemon prints a URL or code to the console, and you complete authorization on any device with a browser (your phone, a laptop, etc.). This works over SSH, in Docker containers, and on servers without a display.

### Google Drive

```bash
cloud-drive-sync account add --provider gdrive --headless
```

What happens:
1. The daemon prints an authorization URL
2. Open that URL in **any browser** (on your phone, another computer, etc.)
3. Sign in with your Google account and click "Allow"
4. Google redirects to `http://localhost?code=...` — this page won't load (that's normal)
5. Copy the **full URL** from your browser's address bar and paste it back into the terminal
6. The daemon extracts the code and completes authorization

Output looks like:
```
Visit this URL to authorize:

  https://accounts.google.com/o/oauth2/auth?client_id=...&scope=...

Sign in, click 'Allow'.
Your browser will redirect to a localhost URL that won't load.
Copy the FULL URL from your browser's address bar and paste it here.

Paste the redirect URL (or just the code): http://localhost?code=4/0A...
```

#### Via the Web UI

When using the web UI (e.g., `http://localhost:8080/` or behind a reverse proxy):

1. Go to the **Accounts** tab and click **Add Account**
2. A "Sign in with Google" button appears — click it to open the auth page
3. Sign in and click "Allow"
4. Your browser redirects to a localhost page that won't load — that's expected
5. Copy the **entire URL** from your browser's address bar (it contains `?code=...`)
6. Paste it into the input field in the web UI and click **Complete Setup**

This works regardless of domain, port, or reverse proxy configuration.

### OneDrive

```bash
cloud-drive-sync account add --provider onedrive --headless
```

What happens:
1. The daemon prints a **device code** and a verification URL
2. Open `https://microsoft.com/devicelogin` on any device
3. Enter the code shown in the terminal
4. Sign in with your Microsoft account and approve
5. The daemon detects the approval automatically (polls in the background)

Output looks like:
```
To sign in, use a web browser to open https://microsoft.com/devicelogin
and enter the code ABCD-EFGH to authenticate.
```

This is the most Docker-friendly flow — no redirect needed.

### Dropbox

```bash
cloud-drive-sync account add --provider dropbox --headless
```

What happens:
1. The daemon prints an authorization URL
2. Open that URL in any browser and click "Allow"
3. Dropbox shows an authorization code on screen
4. Copy the code and paste it back into the terminal

Output looks like:
```
1. Go to: https://www.dropbox.com/oauth2/authorize?...
2. Click 'Allow' (you might have to log in first)
3. Copy the authorization code.

Enter the authorization code: _
```

### Nextcloud

**Via the UI (recommended):** Select "Nextcloud" in the Account Manager, fill in your server URL, username, and app password, then click Connect. No browser or terminal prompts needed.

**Via CLI:**
```bash
cloud-drive-sync account add --provider nextcloud --headless
```

The CLI prompts interactively for server URL, username, and app password (requires a TTY).

**Creating an app password:** In Nextcloud, go to Settings → Security → Devices & sessions → create a new app-specific password. Use that password — not your regular login password.

### Box

```bash
cloud-drive-sync account add --provider box --headless
```

What happens:
1. The daemon prints an authorization URL
2. Open that URL in any browser and sign in to Box
3. Box shows an authorization code
4. Paste it back into the terminal

### Docker Usage

The recommended way to add accounts in Docker is via the HTTP web UI at `http://localhost:8080`:
- **Google Drive / Dropbox / OneDrive / Box**: Click "Add Account", complete the OAuth browser flow using the provided URL, then paste the redirect URL back.
- **Nextcloud**: Select Nextcloud, fill in the server URL + username + app password form, click Connect. No TTY needed.

For Google Drive via CLI (requires `-it` for the OAuth URL prompt):
```bash
# Start the daemon
docker run -d --name cloud-drive-sync \
  -p 8080:8080 \
  -v cloud-drive-sync-config:/root/.config/cloud-drive-sync \
  -v cloud-drive-sync-data:/root/.local/share/cloud-drive-sync \
  -v ~/Documents:/data/Documents \
  ghcr.io/ciberkids/cloud-drive-sync:latest

# Add Google account via CLI (prints auth URL, paste redirect URL back)
docker exec -it cloud-drive-sync \
  python -m cloud_drive_sync account add --provider gdrive --headless

# Verify it worked
docker exec cloud-drive-sync python -m cloud_drive_sync account list
```

After adding accounts, the daemon syncs automatically — no restart needed.

## HTTP Server (Web UI + REST API)

Headless does not mean CLI-only. Started with `--http-port`, the daemon serves the **full web management UI** — the same React interface as the desktop app — plus a REST API, from the daemon process itself. There is no separate web server to run and nothing extra to install: the compiled UI ships inside the daemon package.

| | |
|---|---|
| Web UI | `http://<host>:<port>/` |
| REST API | `http://<host>:<port>/api/*` |

Everything the desktop UI does is available in the browser: adding accounts, creating and editing sync pairs, resolving conflicts, watching transfers, browsing remote folders, and reading the activity log. The HTTP server dispatches to the same request handler as the IPC socket, so the CLI, the desktop app and the browser all drive one daemon with no difference in behaviour.

### Enabling it

`--http-port` defaults to `0`, meaning **disabled**. It is opt-in everywhere except the container images.

**Foreground / manual**

```bash
cloud-drive-sync-daemon start --foreground --http-port 8080
```

**Docker and Quadlet** — already enabled. The image's default command is `start --foreground --http-port 8080`, so you only need to publish the port (`-p 8080:8080`). See [Docker](Docker) and [Quadlet](Quadlet).

**systemd (packaged install)** — **not** enabled. The shipped unit runs `start --foreground` with no HTTP port, so a `.deb` / `.rpm` / AppImage install on a headless server has no web UI until you add the flag:

```bash
systemctl --user edit cloud-drive-sync-daemon
```

```ini
[Service]
ExecStart=
ExecStart=/usr/bin/cloud-drive-sync-daemon start --foreground --http-port 8080
```

The bare `ExecStart=` is required: it clears the unit's original value, and without it systemd refuses to start a service with two `ExecStart` lines. Then reload and restart:

```bash
systemctl --user daemon-reload
systemctl --user restart cloud-drive-sync-daemon
```

Confirm it came up — the daemon logs the bound address on startup:

```
[INFO] HTTP server listening on http://0.0.0.0:8080
```

### Security

> ⚠️ **The HTTP server has no authentication and listens on all interfaces.** Anyone who can reach the port has full control of the daemon: they can list your files, add or remove cloud accounts, and change where data syncs to.

Three specifics worth knowing before you expose it:

- **It binds `0.0.0.0`,** and there is currently no option to change the bind address. Restricting exposure has to happen outside the daemon.
- **There is no login.** No token, no password, no session — every endpoint is open to whoever connects.
- **CORS is `Access-Control-Allow-Origin: *`.** If the port is reachable from a machine running a browser, a web page open in that browser can issue requests to the API.

Recommended deployments:

| Situation | Approach |
|---|---|
| Single machine, local use | Docker: publish as `-p 127.0.0.1:8080:8080` so only that host can connect |
| Remote server, occasional admin | Leave the port unpublished and use an SSH tunnel: `ssh -L 8080:localhost:8080 user@server`, then open `http://localhost:8080` |
| Permanent remote access | Reverse proxy (nginx, Caddy, Traefik) terminating TLS and enforcing authentication in front of it |
| Any | Firewall the port; never forward it from a router to the internet |

Treat a reachable port as equivalent to filesystem access to everything the daemon syncs.



### REST API reference

**Status**

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/status` | Daemon and sync status |

**Accounts**

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/accounts` | List all accounts |
| POST | `/api/accounts` | Add a new account (OAuth or Nextcloud credentials) |
| POST | `/api/accounts/auth-code` | Exchange OAuth authorization code |
| GET | `/api/accounts/oauth-callback` | OAuth redirect callback handler |
| DELETE | `/api/accounts/{email}` | Remove an account |
| PUT | `/api/accounts/{email}/max-transfers` | Set per-account max concurrent transfers |

**Sync Pairs**

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/pairs` | List sync pairs |
| POST | `/api/pairs` | Add a sync pair (`local_path`, `remote_folder_id`, `provider`, `account_id`, `sync_mode`) |
| DELETE | `/api/pairs/{pair_id}` | Remove a sync pair |
| PUT | `/api/pairs/{pair_id}/mode` | Set sync mode (`two_way` / `upload_only` / `download_only`) |
| PUT | `/api/pairs/{pair_id}/ignore-hidden` | Toggle dotfile exclusion |
| PUT | `/api/pairs/{pair_id}/ignore-patterns` | Set glob ignore patterns |
| GET | `/api/pairs/{pair_id}/rules` | Get advanced sync rules (size limit, regex filters) |
| PUT | `/api/pairs/{pair_id}/rules` | Set advanced sync rules |

**Sync Control**

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/sync` | Trigger an immediate sync (optional `pair_id`) |
| POST | `/api/sync/pause` | Pause sync (optional `pair_id`) |
| POST | `/api/sync/resume` | Resume sync (optional `pair_id`) |

**Conflicts & Activity**

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/conflicts` | List unresolved conflicts |
| POST | `/api/conflicts/{conflict_id}/resolve` | Resolve a conflict |
| GET | `/api/activity` | Recent sync activity log (`?limit=N&offset=N`) |

**Settings**

| Method | Path | Description |
|--------|------|-------------|
| GET/PUT | `/api/settings/notifications` | Notification preferences |
| GET/PUT | `/api/settings/bandwidth` | Upload/download bandwidth limits (kbps, 0 = unlimited) |
| GET/PUT | `/api/settings/proxy` | HTTP/HTTPS proxy settings |
| PUT | `/api/settings/conflict-strategy` | Conflict strategy (`keep_both` / `newest_wins` / `ask_user`) |

**File Browser (headless)**

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/remote-folders` | List remote folders (`?parent_id=&account_id=`) |
| POST | `/api/remote-folders` | Create a remote folder |
| GET | `/api/local-dirs` | List local directories (`?path=`) |
| POST | `/api/local-dirs` | Create a local directory |

### Adding accounts via Web UI

When you click **Add Account** in the web UI, the daemon runs the headless auth flow in the background. Since the auth prompts appear in the daemon's stdout (not in the browser), follow these steps:

1. Click **Add Account** in the web UI — the button shows "Authenticating..."
2. In another terminal, check the daemon logs for the authorization URL:
   ```bash
   # Docker
   docker logs -f cloud-drive-sync

   # Docker Compose
   docker compose logs -f daemon

   # Local
   # The URL prints directly in the terminal running the daemon
   ```
3. Open the authorization URL in your browser and complete sign-in
4. The web UI updates automatically when auth completes

### Example curl commands

```bash
# Check status
curl http://localhost:8080/api/status

# List accounts
curl http://localhost:8080/api/accounts

# Add account (headless)
curl -X POST http://localhost:8080/api/accounts \
  -H "Content-Type: application/json" \
  -d '{"provider": "gdrive", "headless": true}'

# List sync pairs
curl http://localhost:8080/api/pairs

# Trigger a sync
curl -X POST http://localhost:8080/api/sync

# Get recent activity
curl 'http://localhost:8080/api/activity?limit=20'

# Set bandwidth limits
curl -X PUT http://localhost:8080/api/settings/bandwidth \
  -H "Content-Type: application/json" \
  -d '{"max_upload_kbps": 1000, "max_download_kbps": 2000}'
```

## Docker Deployment

The daemon runs headless in Docker with no GUI dependencies.

### Quick Start

```bash
docker run -d --name cloud-drive-sync \
  -p 8080:8080 \
  -e PUID=$(id -u) -e PGID=$(id -g) \
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

### File Ownership (PUID / PGID)

By default the daemon runs as root inside the container, which causes synced files
in bind-mounted folders to be owned by root on the host.  Set `PUID` and `PGID` to
your host user's numeric IDs to have the daemon run as that user and write files
with the correct ownership:

```bash
docker run -d \
  -e PUID=$(id -u) \
  -e PGID=$(id -g) \
  ...
```

Or in `docker-compose.yml`:

```yaml
environment:
  - PUID=1000   # host user ID (id -u)
  - PGID=1000   # host group ID (id -g)
```

Config and data volumes (`/root/.config/cloud-drive-sync` and
`/root/.local/share/cloud-drive-sync`) are automatically chowned to PUID:PGID on
startup so no manual volume permission changes are needed.  Omitting `PUID`/`PGID`
(or setting `PUID=0`) preserves the legacy root behaviour.

### HTTP REST API

Enabled by default in the container images on port 8080 — publish it with `-p 8080:8080` and open `http://localhost:8080/`.

See [HTTP Server (Web UI + REST API)](#http-server-web-ui--rest-api) above for the endpoint reference and the security notes that apply when the port is reachable from other machines.

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

## Logging and Disk Usage

The daemon writes to `<data-dir>/cloud-drive-sync.log` and to stderr. Both are bounded, so neither can grow without limit:

| Limit | Value | Notes |
|---|---|---|
| Log file size | 10 MB | Rotated in place, oldest discarded |
| Rotated copies kept | 5 | Total log footprint therefore caps at ~60 MB |
| Maximum message length | 2000 characters | Longer messages are truncated with the original length appended |

Message truncation matters because provider exceptions embed the full failed request in their text. Without a cap, a single rejected WebDAV request could write a ~440 KB line, and repeated retries wrote it again each time. The diagnostic part of a message — action, path, status code — is always at the front, so truncation never removes it. Tracebacks are not truncated.

Rotated files are named `cloud-drive-sync.log.1` through `.5`. To keep long-term history, ship the log elsewhere (journald, a log collector, or a `logrotate` rule on a copy) rather than relying on the daemon's own files.

### Database maintenance

The sync state database at `<data-dir>/state.db` maintains itself, so it does not need manual `VACUUM`ing.

| Behaviour | Value |
|---|---|
| Activity log retention | 30 days |
| Maintenance interval | 6 hours |
| Startup reclaim threshold | 25% of the file free **and** at least 64 MB reclaimable |

Two things run automatically:

- **Every 6 hours**, activity-log rows older than 30 days are deleted and the freed pages are returned to the filesystem. Pruning is bounded per run, so the first pass on a large history is spread over several runs rather than blocking. Activity older than 30 days will therefore disappear from the **Activity** view — export it first if you need to keep it.
- **At startup**, if the file is mostly wasted space, it is rewritten to reclaim it. SQLite does not shrink a database when rows are deleted: freed pages go on an internal free list and get reused, so a long-running daemon's file only ever grows. Databases created before this behaviour existed could reach several GB while holding no rows at all; the first start after upgrading reclaims that and logs the before/after size.

The startup reclaim is deliberately rare because it rewrites the whole file and delays startup while it runs. New databases are created with incremental auto-vacuum enabled, so they give space back continuously and should never reach the threshold.

## Stub Repair

Stubs are incomplete sync-state entries that accumulate when a transfer is interrupted or the sync database is reset while remote files still exist. They show up as files tracked in the database that are missing on one side, causing repeated, fruitless sync attempts.

### Scanning for stubs

In the web UI, open **Settings**, expand a sync pair, and click **Scan for stubs**. The daemon checks the database against the live local and remote file lists and reports how many stale entries it found.

Via the REST API:

```bash
# Dry-run scan (no changes)
curl -X POST 'http://localhost:8080/api/pairs/0/repair?dry_run=true'

# Apply the repair (deletes stale DB entries)
curl -X POST 'http://localhost:8080/api/pairs/0/repair'
```

### When to use it

- After a container restart or database migration where the DB was re-created
- If the activity log shows repeated errors for the same file that does not exist locally
- After manually deleting files outside of the sync process
- After an `rclone bisync --resync` or equivalent baseline reset on the remote side

Stub repair only removes database records — it never deletes files from local storage or from the cloud.

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
