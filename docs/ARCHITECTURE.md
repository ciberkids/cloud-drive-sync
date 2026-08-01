# Architecture

Cloud Drive Sync is a two-process system that runs on Linux, macOS, and Windows. A Python daemon performs all sync operations, and a Tauri/React desktop UI communicates with the daemon over a local IPC channel.

## System Overview

```mermaid
graph TB
    subgraph UI["UI (Tauri + React)"]
        direction TB
        subgraph Frontend["React Frontend"]
            StatusDashboard["Status Dashboard"]
            Settings
            ConflictDialog["Conflict Resolution"]
            ActivityLog["Activity Log"]
            AccountManager["Account Manager"]
        end
        Tray["System Tray Icon"]
        RustBackend["Rust Backend\n(DaemonBridge)"]
        Frontend --> RustBackend
    end

    subgraph Daemon["Daemon (Python 3.12)"]
        direction TB
        subgraph SyncEngine
            Planner
            Executor
            ConflictResolver["Conflict Resolver"]
        end
        Watcher["Watcher\n(watchdog)"]
        DB["SQLite DB\n(aiosqlite)"]
        DriveClient["DriveClient\n(API v3 wrapper)"]
        Watcher --> SyncEngine
        SyncEngine --> DB
        SyncEngine --> DriveClient
    end

    RustBackend <-->|"JSON-RPC 2.0\nUnix Socket"| SyncEngine
    DriveClient -->|"HTTPS"| GoogleDrive[("Google Drive\nAPI v3")]
```

### Front-ends

`RequestHandler` is the single backend. Everything that drives the daemon is a front-end onto it, so no front-end can reach behaviour the others cannot, and sync logic exists in exactly one place.

| Front-end | Transport | Enabled by | Consumers |
|---|---|---|---|
| IPC socket | JSON-RPC 2.0 over a Unix socket | always | CLI, desktop app (Tauri) |
| HTTP | REST at `/api/*` + the web UI at `/` | `--http-port` | browser, `curl`, scripts |
| MCP | Streamable HTTP at `/mcp` | `--mcp-port` | AI assistants (Claude Desktop/Code, any MCP client) |

The HTTP front-end serves the same compiled React UI as the desktop app, from inside the daemon process — headless deployments get the full interface with no separate web server. See [HTTP Server](Daemon#http-server-web-ui--rest-api).

The MCP front-end maps handler methods to tools. It is read-only by default and its state-changing tools require `--mcp-allow-writes`; a few handler methods (`shutdown`, the OAuth code exchange, proxy configuration, host filesystem browsing) are never exposed at any level. The tool catalogue and its gating live in `cloud_drive_sync/mcp/catalog.py` and deliberately import no MCP SDK, so the permission logic is testable without the optional extra installed; only `mcp/server.py` needs it. See [MCP Server](Daemon#mcp-server-for-ai-assistants).

> The HTTP and MCP front-ends each take an optional shared token (`--http-token` / `--mcp-token`); both are unauthenticated without one, and both bind all interfaces. See [Authentication](Daemon#authentication).

## Component Breakdown

### Daemon (`daemon/`)

The daemon is a Python asyncio application that runs as a background service.

| Component | Module | Responsibility |
|---|---|---|
| **SyncEngine** | `sync/engine.py` | Top-level orchestrator. Manages pair lifecycles, wires watcher + poller + planner + executor. |
| **SyncPlanner** | `sync/planner.py` | Diffs local vs remote state and produces `SyncAction` lists (upload, download, delete, conflict, noop). |
| **SyncExecutor** | `sync/executor.py` | Executes planned actions with a concurrency-limited semaphore. |
| **ConflictResolver** | `sync/conflict.py` | Three-way conflict detection and resolution (keep_both, newest_wins, ask_user). |
| **DriveClient** | `drive/client.py` | Thin async wrapper around Google Drive API v3. All calls run in a thread pool via `asyncio.to_thread`. |
| **FileOperations** | `drive/operations.py` | Higher-level upload/download/delete with resumable transfers and progress callbacks. |
| **ChangePoller** | `drive/changes.py` | Polls the Drive Changes API for remote modifications. |
| **DirectoryWatcher** | `local/watcher.py` | Uses watchdog to detect local filesystem changes with debounced event coalescing. |
| **Database** | `db/database.py` | Async SQLite wrapper (aiosqlite) for sync state, conflicts, activity log, and change tokens. |
| **IpcServer** | `ipc/server.py` | Unix domain socket server accepting JSON-RPC 2.0 requests. Newline-delimited. |
| **RequestHandler** | `ipc/handlers.py` | Dispatches JSON-RPC methods to handler functions. |
| **Config** | `config.py` | Loads and saves TOML configuration. |
| **Daemon** | `daemon.py` | Main process class — initializes all components, handles signals, manages PID file. |

### UI (`ui/`)

The UI is a Tauri v2 application with a React frontend and Rust backend.

| Component | File | Responsibility |
|---|---|---|
| **DaemonBridge** | `src-tauri/src/ipc_bridge.rs` | Rust client that connects to the daemon's Unix socket. |
| **Tauri Commands** | `src-tauri/src/commands.rs` | Tauri invoke handlers that proxy calls through the bridge. |
| **System Tray** | `src-tauri/src/tray.rs` | Tray icon with status indicators and context menu. |
| **SyncStatus** | `src/components/SyncStatus.tsx` | Status dashboard with sync controls. |
| **Settings** | `src/components/Settings.tsx` | Sync pair management and conflict strategy. |
| **ConflictDialog** | `src/components/ConflictDialog.tsx` | Conflict list with per-file and batch resolution. |
| **ActivityLog** | `src/components/ActivityLog.tsx` | Filterable, paginated activity feed. |
| **AccountManager** | `src/components/AccountManager.tsx` | Google account login/logout. |
| **IPC Client** | `src/lib/ipc.ts` | TypeScript wrappers around `invoke()` for all Tauri commands. |
| **React Hooks** | `src/lib/hooks.ts` | `useStatus`, `useSyncPairs`, `useConflicts`, `useActivityLog`, `useDaemonEvent`. |

## Sync Algorithm

### Initial Sync

When a sync pair starts for the first time (no stored state):

```mermaid
flowchart TD
    A["1. Scan local directory recursively"] --> B["2. Fetch remote file list from Drive"]
    B --> C{"3. Plan (three-way diff)"}
    C -->|File only local| U[UPLOAD]
    C -->|File only remote| D[DOWNLOAD]
    C -->|Same MD5| N[NOOP — mark synced]
    C -->|Different MD5| K[CONFLICT]
    U --> R{"4. Resolve conflicts"}
    D --> R
    N --> R
    K --> R
    R -->|keep_both| R1[Rename local + download remote]
    R -->|newest_wins| R2[Compare mtime, keep newer]
    R -->|ask_user| R3[Notify UI, defer]
    R1 --> E["5. Execute actions (concurrency-limited)"]
    R2 --> E
    R3 --> E
    E --> F["6. Notify UI (sync_complete)"]
    F --> G["7. Store Drive change token"]
    G --> H["8. Start continuous sync loops"]
```

### Continuous Sync

After initial sync, two loops run concurrently:

```mermaid
flowchart LR
    subgraph Local["Local Watcher Loop"]
        direction TB
        L1[watchdog detects change] --> L2[Debounce 1s + batch]
        L2 --> L3[Compute MD5 of changed files]
        L3 --> L4["plan_continuous_sync()"]
        L4 --> L5[Execute actions concurrently]
        L5 --> L6[Update stored state]
    end
    subgraph Remote["Remote Poller Loop"]
        direction TB
        R1[Poll Drive Changes API every N sec] --> R2[Map changed file IDs to stored paths]
        R2 --> R3["plan_continuous_sync()"]
        R3 --> R4[Execute actions concurrently]
        R4 --> R5[Update stored state + change token]
    end
```

The `plan_continuous_sync()` function uses three-way comparison:

- Compare the **new state** (from the change) against the **stored base state**
- If only one side changed relative to base → propagate the change
- If both sides changed relative to base → CONFLICT

### Sync Modes

Each sync pair has a configurable `sync_mode` that filters planned actions:

| Mode | Allowed Actions | Use Case |
|---|---|---|
| `two_way` | All (upload, download, delete local/remote) | Full bidirectional sync (default) |
| `upload_only` | Upload, delete remote | Backup local files to Drive |
| `download_only` | Download, delete local | Mirror Drive contents locally |

Mode filtering is applied by `filter_actions_by_mode()` after planning but before execution.

### Hidden File Filtering

Each sync pair has an `ignore_hidden` setting (default: `true`) that controls whether dotfiles and dot-directories are synced. When enabled, files and directories whose name starts with `.` are excluded at multiple levels:

| Component | Filtering Point |
|---|---|
| **Scanner** (`local/scanner.py`) | `scan_directory()` skips paths where any component starts with `.` |
| **Watcher** (`local/watcher.py`) | `_EventHandler._enqueue()` drops filesystem events for hidden paths |
| **Planner** (`sync/planner.py`) | `plan_initial_sync()` skips hidden paths during initial diff |

The setting is toggled per-pair via the `set_ignore_hidden` IPC method, which persists to `config.toml`. The UI exposes this as a "Hide dotfiles" checkbox in Settings.

### Stale Data Cleanup

When the sync engine starts, it compares the set of active pair IDs (derived from the current config) against pair IDs found in the database. Any data belonging to pairs that no longer exist in the config is cleaned up via `Database.cleanup_stale_pairs()`. This prevents orphaned data from removed sync pairs from accumulating in the database or appearing in activity logs.

### FileState Transitions

```mermaid
stateDiagram-v2
    [*] --> UNKNOWN
    UNKNOWN --> PENDING_UPLOAD
    UNKNOWN --> PENDING_DOWNLOAD
    PENDING_UPLOAD --> UPLOADING
    PENDING_DOWNLOAD --> DOWNLOADING
    UPLOADING --> SYNCED
    DOWNLOADING --> SYNCED
    SYNCED --> CONFLICT
    SYNCED --> ERROR
    CONFLICT --> PENDING_UPLOAD : resolved
    CONFLICT --> PENDING_DOWNLOAD : resolved
    ERROR --> PENDING_UPLOAD : retry
    ERROR --> PENDING_DOWNLOAD : retry
```

States are defined in `db/models.py:FileState`:
- `UNKNOWN` — initial state, not yet evaluated
- `SYNCED` — both sides match
- `PENDING_UPLOAD` / `PENDING_DOWNLOAD` — queued for transfer
- `UPLOADING` / `DOWNLOADING` — transfer in progress
- `CONFLICT` — both sides changed, awaiting resolution
- `ERROR` — transfer or operation failed

## IPC Protocol

The daemon and UI communicate via **JSON-RPC 2.0** with newline-delimited messages over a local transport.

### Transport

- **Linux / macOS**: Unix domain socket at `$XDG_RUNTIME_DIR/cloud-drive-sync/cloud-drive-sync.sock` (Linux, typically `/run/user/1000/cloud-drive-sync/cloud-drive-sync.sock`) or `~/Library/Application Support/cloud-drive-sync/cloud-drive-sync.sock` (macOS). Permissions set to `0600` (user-only read/write).
- **Windows**: TCP connection to `127.0.0.1` on a dynamic port. The port number is written to a lock file at `%LOCALAPPDATA%\cloud-drive-sync\daemon.lock`.
- **Framing**: each message is a single JSON object terminated by `\n`

### Message Flow

```mermaid
sequenceDiagram
    participant UI as UI (Tauri Rust)
    participant D as Daemon (Python)
    UI->>D: JSON-RPC Request<br/>{"method":"get_status","id":1}
    D-->>UI: JSON-RPC Response<br/>{"id":1,"result":{...}}
    D-)UI: Notification (no id)<br/>{"method":"sync_progress","params":{...}}
```

The daemon supports 16 RPC methods including sync control (`force_sync`, `pause_sync`, `resume_sync`), configuration (`add_sync_pair`, `remove_sync_pair`, `set_conflict_strategy`, `set_sync_mode`, `set_ignore_hidden`), data queries (`get_status`, `get_sync_pairs`, `get_activity_log`, `get_conflicts`), authentication (`start_auth`, `logout`), and Drive browsing (`list_remote_folders`).

> **Docker:** The IPC socket can be bind-mounted from the container to allow CLI management from the host. Set `XDG_RUNTIME_DIR=/run/cloud-drive-sync` in the container and mount that volume.

See [API Reference](API.md) for the full list of methods and notifications.

## Database Schema

The daemon stores sync state in an SQLite database at `~/.local/share/cloud-drive-sync/state.db`. WAL journal mode is enabled for concurrent reads.

### Tables

#### `schema_version`

Tracks the database schema version for migrations.

| Column | Type | Description |
|---|---|---|
| `version` | INTEGER | Schema version number |

#### `sync_state`

Tracks the sync state of every known file.

| Column | Type | Description |
|---|---|---|
| `path` | TEXT | Relative file path (part of PK) |
| `pair_id` | TEXT | Sync pair identifier (part of PK) |
| `local_md5` | TEXT | MD5 hash of the local file |
| `remote_md5` | TEXT | MD5 hash from Drive metadata |
| `remote_id` | TEXT | Google Drive file ID |
| `state` | TEXT | File state (unknown, synced, pending_upload, etc.) |
| `local_mtime` | REAL | Local modification time (Unix timestamp) |
| `remote_mtime` | REAL | Remote modification time (Unix timestamp) |
| `last_synced` | TEXT | ISO 8601 timestamp of last successful sync |

**Primary key**: `(path, pair_id)`
**Indexes**: `idx_sync_state_pair(pair_id)`, `idx_sync_state_state(state)`

#### `change_tokens`

Stores the Drive Changes API polling token per sync pair.

| Column | Type | Description |
|---|---|---|
| `pair_id` | TEXT | Sync pair identifier (PK) |
| `token` | TEXT | Drive Changes API page token |
| `updated_at` | TEXT | ISO 8601 timestamp |

#### `conflicts`

Records detected conflicts for user review.

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER | Auto-incrementing ID (PK) |
| `path` | TEXT | Relative file path |
| `pair_id` | TEXT | Sync pair identifier |
| `local_md5` | TEXT | Local file MD5 at conflict time |
| `remote_md5` | TEXT | Remote file MD5 at conflict time |
| `local_mtime` | REAL | Local modification time |
| `remote_mtime` | REAL | Remote modification time |
| `detected_at` | TEXT | ISO 8601 timestamp |
| `resolved` | INTEGER | 0 = unresolved, 1 = resolved |
| `resolution` | TEXT | Resolution action taken (nullable) |

**Indexes**: `idx_conflicts_unresolved(resolved) WHERE resolved = 0`

#### `sync_log`

Activity log of all sync operations.

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER | Auto-incrementing ID (PK) |
| `timestamp` | TEXT | ISO 8601 timestamp |
| `action` | TEXT | Action type (upload, download, delete, conflict) |
| `path` | TEXT | Relative file path |
| `pair_id` | TEXT | Sync pair identifier |
| `status` | TEXT | Result status (success, error, skipped) |
| `detail` | TEXT | Human-readable detail message (nullable) |

**Indexes**: `idx_sync_log_ts(timestamp)`

## Security Model

### Authentication

- **OAuth2** with Google Drive API v3 scopes
- Credentials obtained via browser-based OAuth flow (`google-auth-oauthlib`)
- Tokens stored encrypted at the platform data directory (see File Path Conventions) in `credentials.enc` using `cryptography` (Fernet)
- A random salt is stored alongside in `token_salt`
- Encryption key is derived from a machine ID: `/etc/machine-id` on Linux, `IOPlatformUUID` via `ioreg` on macOS, `MachineGuid` from the Windows registry
- **Both files are `0600`** (owner only), as of v2.4.1. This is the control that actually matters, because the machine ID is not itself a secret — `/etc/machine-id` is world-readable by design — so a readable salt would be enough to derive the key

**Every provider is encrypted** as of v2.4.3. Where the ciphertext lives determines where its salt lives, and the two must not be separable:

| Provider | Credentials | Salt |
|---|---|---|
| Google Drive | `<data>/credentials-*.enc` | shared `<data>/token_salt` |
| Dropbox | `<data>/dropbox-credentials-*.enc` | shared `<data>/token_salt` |
| OneDrive, Box, Nextcloud | `<config>/…` | **beside each file**, `*.salt` |

The last row is not an inconsistency. Those three store credentials under the *config* directory while the shared salt lives under the *data* directory — separate volumes in a container. Encrypting them against the shared salt would put the ciphertext and its only key in different volumes, and restoring just the config volume would then hit salt creation, which mints a new salt rather than failing, leaving every account silently unrecoverable. A salt beside each file keeps them together while preserving the machine binding.

Credential files written before encryption are **upgraded on read**, not on save: for these providers `save_credentials` only runs when an account is added, so a migration hung off saving would never fire for an existing user. If the re-encrypt fails — read-only filesystem, ownership changed by a `PUID` remap — the credentials are still returned, because failing there would take every account offline to fix a storage detail.

> **What machine binding does and does not give you.** On a desktop install, copying the credential files to another machine is not enough to decrypt them: the third input is that machine's ID. **Inside the Docker image there is no `/etc/machine-id`**, so a published fallback constant is used instead, and the tokens are bound to nothing. For container deployments, treat the volumes as containing live credentials — encrypted, but decryptable by anyone who obtains them. Back them up accordingly, and do not commit them or share them in a bug report.

> **Headless auth:** All providers support headless authentication via `--headless` flag. Google Drive uses console flow, OneDrive uses device code flow, others print authorization URLs for manual completion.

### IPC Socket

- **Linux / macOS**: Unix domain socket with permissions `0600` (owner read/write only). No authentication — relies on filesystem permissions.
- **Windows**: TCP `127.0.0.1` bound to a dynamic port (port stored in lock file). Localhost-only binding prevents remote access.

### Daemon Process

- Runs as a user-level systemd service (no root)
- systemd hardening: `ProtectSystem=strict`, `PrivateTmp=true`, `NoNewPrivileges=true`
- PID file at `$XDG_RUNTIME_DIR/cloud-drive-sync/cloud-drive-sync.pid`

## File Path Conventions

Paths follow the [XDG Base Directory Specification](https://specifications.freedesktop.org/basedir-spec/latest/) on Linux, and native conventions on macOS and Windows.

| Purpose | Linux | macOS | Windows |
|---|---|---|---|
| Configuration | `~/.config/cloud-drive-sync/` | `~/Library/Application Support/cloud-drive-sync/` | `%APPDATA%\cloud-drive-sync\` |
| Data (DB, credentials) | `~/.local/share/cloud-drive-sync/` | `~/Library/Application Support/cloud-drive-sync/` | `%LOCALAPPDATA%\cloud-drive-sync\` |
| Socket / IPC | `$XDG_RUNTIME_DIR/cloud-drive-sync/cloud-drive-sync.sock` | `~/Library/Application Support/cloud-drive-sync/cloud-drive-sync.sock` | TCP `127.0.0.1` (port in `%LOCALAPPDATA%\cloud-drive-sync\daemon.lock`) |
| PID file | `$XDG_RUNTIME_DIR/cloud-drive-sync/cloud-drive-sync.pid` | `~/Library/Application Support/cloud-drive-sync/cloud-drive-sync.pid` | `%LOCALAPPDATA%\cloud-drive-sync\daemon.lock` |
