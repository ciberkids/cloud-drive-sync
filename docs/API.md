# API Reference

GDrive Sync uses **JSON-RPC 2.0 over a Unix domain socket** for communication between the daemon and the UI.

- **Socket**: `$XDG_RUNTIME_DIR/gdrive-sync.sock`
- **Framing**: newline-delimited JSON (`\n` after each message)
- **Protocol**: JSON-RPC 2.0 — requests have an `id`, notifications do not

## IPC Methods (Client → Daemon)

### `get_status`

Get the current daemon and sync status.

**Params**: none

**Response**:

| Field | Type | Description |
|---|---|---|
| `connected` | boolean | Whether the daemon has valid credentials and an active sync engine |
| `syncing` | boolean | Whether any transfers are currently in progress |
| `paused` | boolean | Whether all pairs are paused |
| `error` | string\|null | Most recent error message, if any |
| `last_sync` | string\|null | ISO 8601 timestamp of most recent sync across all pairs |
| `files_synced` | integer | Total files with `synced` state across all pairs (queried from DB) |
| `active_transfers` | integer | Number of in-progress transfers across all pairs |

**Example**:

```json
// Request
{"jsonrpc": "2.0", "method": "get_status", "id": 1}

// Response
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "connected": true,
    "syncing": false,
    "paused": false,
    "error": null,
    "last_sync": "2025-01-15T14:32:00+00:00",
    "files_synced": 247,
    "active_transfers": 0
  }
}
```

---

### `get_sync_pairs`

List all configured sync folder pairs.

**Params**: none

**Response**: Array of sync pair objects:

| Field | Type | Description |
|---|---|---|
| `id` | string | Pair identifier (index as string) |
| `local_path` | string | Local directory path |
| `remote_folder_id` | string | Google Drive folder ID |
| `enabled` | boolean | Whether the pair is enabled |
| `sync_mode` | string | Sync direction: `"two_way"`, `"upload_only"`, or `"download_only"` |
| `ignore_hidden` | boolean | Whether hidden files (dotfiles) are excluded from sync |

**Example**:

```json
// Request
{"jsonrpc": "2.0", "method": "get_sync_pairs", "id": 2}

// Response
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": [
    {
      "id": "0",
      "local_path": "/home/user/Documents",
      "remote_folder_id": "root",
      "enabled": true,
      "sync_mode": "two_way",
      "ignore_hidden": true
    },
    {
      "id": "1",
      "local_path": "/home/user/Pictures",
      "remote_folder_id": "0A3xFolderIdHere",
      "enabled": true,
      "sync_mode": "upload_only",
      "ignore_hidden": true
    }
  ]
}
```

---

### `add_sync_pair`

Add a new sync folder pair.

**Params**:

| Field | Type | Required | Description |
|---|---|---|---|
| `local_path` | string | yes | Absolute path to local directory |
| `remote_folder_id` | string | no | Drive folder ID (default: `"root"`) |
| `ignore_hidden` | boolean | no | Whether to ignore hidden files/dotfiles (default: `true`) |

**Response**: The newly created sync pair object:

| Field | Type | Description |
|---|---|---|
| `id` | string | Pair identifier (index as string) |
| `local_path` | string | Local directory path |
| `remote_folder_id` | string | Google Drive folder ID |
| `enabled` | boolean | Always `true` for new pairs |
| `sync_mode` | string | Always `"two_way"` for new pairs |
| `ignore_hidden` | boolean | Whether hidden files are excluded |

**Errors**: Returns `-32602` (Invalid params) if the pair already exists (duplicate local path + remote folder ID).

**Example**:

```json
// Request
{
  "jsonrpc": "2.0",
  "method": "add_sync_pair",
  "params": {
    "local_path": "/home/user/Projects",
    "remote_folder_id": "root",
    "ignore_hidden": true
  },
  "id": 3
}

// Response
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "id": "2",
    "local_path": "/home/user/Projects",
    "remote_folder_id": "root",
    "enabled": true,
    "sync_mode": "two_way",
    "ignore_hidden": true
  }
}
```

---

### `remove_sync_pair`

Remove a sync folder pair by index.

**Params**:

| Field | Type | Required | Description |
|---|---|---|---|
| `index` | integer | yes | Index of the pair to remove |

**Response**:

| Field | Type | Description |
|---|---|---|
| `status` | string | `"removed"` |
| `local_path` | string | Path of the removed pair |

**Example**:

```json
// Request
{
  "jsonrpc": "2.0",
  "method": "remove_sync_pair",
  "params": {"index": 1},
  "id": 4
}

// Response
{
  "jsonrpc": "2.0",
  "id": 4,
  "result": {
    "status": "removed",
    "local_path": "/home/user/Pictures"
  }
}
```

---

### `set_conflict_strategy`

Change the global conflict resolution strategy.

**Params**:

| Field | Type | Required | Description |
|---|---|---|---|
| `strategy` | string | yes | One of: `"keep_both"`, `"newest_wins"`, `"ask_user"` |

**Response**:

| Field | Type | Description |
|---|---|---|
| `status` | string | `"ok"` |
| `strategy` | string | The strategy that was set |

**Example**:

```json
// Request
{
  "jsonrpc": "2.0",
  "method": "set_conflict_strategy",
  "params": {"strategy": "newest_wins"},
  "id": 5
}

// Response
{
  "jsonrpc": "2.0",
  "id": 5,
  "result": {
    "status": "ok",
    "strategy": "newest_wins"
  }
}
```

---

### `resolve_conflict`

Resolve a specific conflict by ID.

**Params**:

| Field | Type | Required | Description |
|---|---|---|---|
| `conflict_id` | integer | yes | ID of the conflict record |
| `resolution` | string | yes | One of: `"keep_local"`, `"keep_remote"`, `"keep_both"` |

**Response**:

| Field | Type | Description |
|---|---|---|
| `status` | string | `"ok"` |

**Example**:

```json
// Request
{
  "jsonrpc": "2.0",
  "method": "resolve_conflict",
  "params": {"conflict_id": 7, "resolution": "keep_local"},
  "id": 6
}

// Response
{
  "jsonrpc": "2.0",
  "id": 6,
  "result": {"status": "ok"}
}
```

---

### `force_sync`

Trigger an immediate full sync for a specific pair.

**Params**:

| Field | Type | Required | Description |
|---|---|---|---|
| `pair_id` | string | no | Pair identifier (e.g., `"pair_0"`). Defaults to `"pair_0"` if omitted. |

**Response**:

| Field | Type | Description |
|---|---|---|
| `status` | string | `"ok"` if pair found, `"not_found"` otherwise |

**Example**:

```json
// Request
{
  "jsonrpc": "2.0",
  "method": "force_sync",
  "params": {"pair_id": "pair_0"},
  "id": 7
}

// Response
{
  "jsonrpc": "2.0",
  "id": 7,
  "result": {"status": "ok"}
}
```

---

### `pause_sync`

Pause syncing for a specific pair. Changes are still detected but not processed.

**Params**:

| Field | Type | Required | Description |
|---|---|---|---|
| `pair_id` | string | no | Pair identifier. Defaults to `"pair_0"` if omitted. |

**Response**:

| Field | Type | Description |
|---|---|---|
| `status` | string | `"paused"` if pair found, `"not_found"` otherwise |

**Example**:

```json
// Request
{
  "jsonrpc": "2.0",
  "method": "pause_sync",
  "params": {"pair_id": "pair_0"},
  "id": 8
}

// Response
{
  "jsonrpc": "2.0",
  "id": 8,
  "result": {"status": "paused"}
}
```

---

### `resume_sync`

Resume syncing for a paused pair.

**Params**:

| Field | Type | Required | Description |
|---|---|---|---|
| `pair_id` | string | no | Pair identifier. Defaults to `"pair_0"` if omitted. |

**Response**:

| Field | Type | Description |
|---|---|---|
| `status` | string | `"resumed"` if pair found, `"not_found"` otherwise |

**Example**:

```json
// Request
{
  "jsonrpc": "2.0",
  "method": "resume_sync",
  "params": {"pair_id": "pair_0"},
  "id": 9
}

// Response
{
  "jsonrpc": "2.0",
  "id": 9,
  "result": {"status": "resumed"}
}
```

---

### `get_activity_log`

Retrieve recent sync activity entries.

**Params**:

| Field | Type | Required | Description |
|---|---|---|---|
| `limit` | integer | no | Max entries to return (default: 50) |
| `pair_id` | string | no | Filter by pair ID |

**Response**: Array of log entry objects:

| Field | Type | Description |
|---|---|---|
| `id` | integer | Log entry ID |
| `timestamp` | string | ISO 8601 timestamp |
| `event_type` | string | Normalized event type for UI filtering (see below) |
| `path` | string | Relative file path |
| `pair_id` | string | Sync pair identifier |
| `status` | string | Result status |
| `details` | string | Human-readable detail |

The `event_type` field is normalized for consistent UI filtering:
- If the entry has `status: "error"`, `event_type` is `"error"` regardless of the original action
- If the action starts with `"delete"`, `event_type` is `"delete"`
- Otherwise `event_type` matches the original action (e.g. `"upload"`, `"download"`, `"auth"`)

When no `pair_id` filter is specified, entries from removed sync pairs are automatically excluded (only entries for currently configured pairs and `_system` events are returned).

**Example**:

```json
// Request
{
  "jsonrpc": "2.0",
  "method": "get_activity_log",
  "params": {"limit": 10},
  "id": 10
}

// Response
{
  "jsonrpc": "2.0",
  "id": 10,
  "result": [
    {
      "id": 42,
      "timestamp": "2025-01-15T14:32:00+00:00",
      "event_type": "upload",
      "path": "notes.md",
      "pair_id": "pair_0",
      "status": "success",
      "details": "Uploaded 2.4 KB"
    },
    {
      "id": 41,
      "timestamp": "2025-01-15T14:31:00+00:00",
      "event_type": "download",
      "path": "budget.xlsx",
      "pair_id": "pair_0",
      "status": "success",
      "details": "Downloaded 156 KB"
    }
  ]
}
```

---

### `get_conflicts`

Get all unresolved conflicts.

**Params**:

| Field | Type | Required | Description |
|---|---|---|---|
| `pair_id` | string | no | Filter by pair ID |

**Response**: Array of conflict record objects:

| Field | Type | Description |
|---|---|---|
| `id` | integer | Conflict record ID |
| `path` | string | Relative file path |
| `pair_id` | string | Sync pair identifier |
| `local_md5` | string | Local file MD5 hash |
| `remote_md5` | string | Remote file MD5 hash |
| `detected_at` | string | ISO 8601 timestamp |

**Example**:

```json
// Request
{
  "jsonrpc": "2.0",
  "method": "get_conflicts",
  "params": {},
  "id": 11
}

// Response
{
  "jsonrpc": "2.0",
  "id": 11,
  "result": [
    {
      "id": 7,
      "path": "report.docx",
      "pair_id": "pair_0",
      "local_md5": "d41d8cd98f00b204e9800998ecf8427e",
      "remote_md5": "e99a18c428cb38d5f260853678922e03",
      "detected_at": "2025-01-15T14:28:00+00:00"
    }
  ]
}
```

---

### `start_auth`

Initiate the OAuth2 authorization flow. Opens a browser for the user to authorize.

**Params**: none

**Response**:

| Field | Type | Description |
|---|---|---|
| `status` | string | `"ok"` if auth succeeded, `"error"` on failure, `"no_auth_callback"` if no callback is set |
| `message` | string\|undefined | Error message (only present when `status` is `"error"`) |

Auth events (success and failure) are logged to the activity log with `action: "auth"` and `pair_id: "_system"`.

**Example**:

```json
// Request
{"jsonrpc": "2.0", "method": "start_auth", "id": 12}

// Response
{
  "jsonrpc": "2.0",
  "id": 12,
  "result": {"status": "ok"}
}
```

---

### `logout`

Clear stored OAuth credentials and sign out.

**Params**: none

**Response**:

| Field | Type | Description |
|---|---|---|
| `status` | string | `"logged_out"` |

**Example**:

```json
// Request
{"jsonrpc": "2.0", "method": "logout", "id": 13}

// Response
{
  "jsonrpc": "2.0",
  "id": 13,
  "result": {"status": "logged_out"}
}
```

---

### `list_remote_folders`

List folders in a given parent folder on Google Drive. Used by the UI for the remote folder browser.

**Params**:

| Field | Type | Required | Description |
|---|---|---|---|
| `parent_id` | string | no | Drive folder ID to list children of (default: `"root"`) |

**Response**:

| Field | Type | Description |
|---|---|---|
| `folders` | array | List of `{id, name}` objects |
| `parent_id` | string | The parent folder ID that was queried |
| `error` | string\|null | Error message if the request failed |

**Example**:

```json
// Request
{
  "jsonrpc": "2.0",
  "method": "list_remote_folders",
  "params": {"parent_id": "root"},
  "id": 14
}

// Response
{
  "jsonrpc": "2.0",
  "id": 14,
  "result": {
    "folders": [
      {"id": "0A3xFolderIdHere", "name": "Documents"},
      {"id": "0B4yAnotherFolder", "name": "Projects"}
    ],
    "parent_id": "root"
  }
}
```

---

### `set_sync_mode`

Change the sync direction for a specific pair.

**Params**:

| Field | Type | Required | Description |
|---|---|---|---|
| `pair_id` | string | yes | Pair identifier (index as string) |
| `sync_mode` | string | yes | One of: `"two_way"`, `"upload_only"`, `"download_only"` |

**Response**:

| Field | Type | Description |
|---|---|---|
| `status` | string | `"ok"` |
| `sync_mode` | string | The mode that was set |

**Example**:

```json
// Request
{
  "jsonrpc": "2.0",
  "method": "set_sync_mode",
  "params": {"pair_id": "0", "sync_mode": "upload_only"},
  "id": 15
}

// Response
{
  "jsonrpc": "2.0",
  "id": 15,
  "result": {"status": "ok", "sync_mode": "upload_only"}
}
```

---

### `set_ignore_hidden`

Toggle the hidden file filtering setting for a sync pair. When enabled, files and directories starting with a dot (e.g. `.git`, `.env`) are excluded from sync.

**Params**:

| Field | Type | Required | Description |
|---|---|---|---|
| `pair_id` | string | yes | Pair identifier (index as string) |
| `ignore_hidden` | boolean | yes | `true` to exclude hidden files, `false` to include them |

**Response**:

| Field | Type | Description |
|---|---|---|
| `status` | string | `"ok"` |
| `ignore_hidden` | boolean | The value that was set |

**Example**:

```json
// Request
{
  "jsonrpc": "2.0",
  "method": "set_ignore_hidden",
  "params": {"pair_id": "0", "ignore_hidden": false},
  "id": 16
}

// Response
{
  "jsonrpc": "2.0",
  "id": 16,
  "result": {"status": "ok", "ignore_hidden": false}
}
```

---

## Daemon Notifications (Daemon → Client)

Notifications are JSON-RPC 2.0 messages without an `id` field. They are sent to all connected clients.

### `sync_progress`

Sent during file transfers to report progress.

| Field | Type | Description |
|---|---|---|
| `pair_id` | string | Sync pair identifier |
| `path` | string | File being transferred |
| `progress` | integer | Bytes transferred so far |
| `total` | integer | Total bytes |

```json
{
  "jsonrpc": "2.0",
  "method": "sync_progress",
  "params": {
    "pair_id": "pair_0",
    "path": "video.mp4",
    "progress": 5242880,
    "total": 10485760
  }
}
```

### `sync_complete`

Sent when a sync cycle finishes.

| Field | Type | Description |
|---|---|---|
| `pair_id` | string | Sync pair identifier |
| `uploaded` | integer | Number of files uploaded |
| `downloaded` | integer | Number of files downloaded |
| `errors` | integer | Number of errors |

```json
{
  "jsonrpc": "2.0",
  "method": "sync_complete",
  "params": {
    "pair_id": "pair_0",
    "uploaded": 3,
    "downloaded": 1,
    "errors": 0
  }
}
```

### `conflict_detected`

Sent when a new conflict is detected (used with `ask_user` strategy).

| Field | Type | Description |
|---|---|---|
| `id` | integer | Conflict record ID |
| `path` | string | Conflicted file path |
| `local_md5` | string | Local file MD5 |
| `remote_md5` | string | Remote file MD5 |

```json
{
  "jsonrpc": "2.0",
  "method": "conflict_detected",
  "params": {
    "id": 7,
    "path": "report.docx",
    "local_md5": "d41d8cd98f00b204e9800998ecf8427e",
    "remote_md5": "e99a18c428cb38d5f260853678922e03"
  }
}
```

### `status_changed`

Sent when a sync pair's status changes.

| Field | Type | Description |
|---|---|---|
| `pair_id` | string | Sync pair identifier |
| `status` | string | New status description |

```json
{
  "jsonrpc": "2.0",
  "method": "status_changed",
  "params": {
    "pair_id": "pair_0",
    "status": "syncing"
  }
}
```

### `error`

Sent when an error occurs.

| Field | Type | Description |
|---|---|---|
| `message` | string | Error description |
| `pair_id` | string\|null | Pair ID if error is pair-specific |

```json
{
  "jsonrpc": "2.0",
  "method": "error",
  "params": {
    "message": "Drive API rate limit exceeded",
    "pair_id": "pair_0"
  }
}
```

---

## Tauri Commands (Frontend → Rust Backend)

The Tauri React frontend calls these commands via `invoke()`. The Rust backend proxies each command to the daemon over the Unix socket.

| Command | Parameters | Returns | Description |
|---|---|---|---|
| `get_status` | — | `DaemonStatus` | Get daemon connection and sync status |
| `get_sync_pairs` | — | `SyncPair[]` | List configured sync pairs |
| `add_sync_pair` | `localPath: string`, `remoteFolderId: string`, `ignoreHidden?: boolean` | `SyncPair` | Add a new sync pair |
| `remove_sync_pair` | `pairId: string` | — | Remove a sync pair |
| `set_conflict_strategy` | `strategy: string` | — | Set conflict resolution strategy |
| `resolve_conflict` | `conflictId: string`, `resolution: ConflictResolution` | — | Resolve a specific conflict |
| `force_sync` | `pairId?: string` | — | Trigger immediate sync |
| `pause_sync` | `pairId?: string` | — | Pause syncing |
| `resume_sync` | `pairId?: string` | — | Resume syncing |
| `get_activity_log` | `limit: number`, `offset: number` | `LogEntry[]` | Fetch activity log entries |
| `get_conflicts` | — | `ConflictRecord[]` | Get unresolved conflicts |
| `start_auth` | — | `unknown` | Start OAuth flow |
| `logout` | — | — | Clear credentials |
| `connect_daemon` | — | — | Reconnect to daemon socket |
| `set_sync_mode` | `pairId: string`, `syncMode: string` | — | Change sync direction for a pair |
| `set_ignore_hidden` | `pairId: string`, `ignoreHidden: boolean` | — | Toggle hidden file filtering for a pair |
| `list_remote_folders` | `parentId: string` | `{folders, parent_id, error?}` | Browse remote Drive folders |

### TypeScript Types

```typescript
type ConflictStrategy = "keep_both" | "newest_wins" | "ask_user";
type ConflictResolution = "keep_local" | "keep_remote" | "keep_both";
type SyncMode = "two_way" | "upload_only" | "download_only";

interface DaemonStatus {
  connected: boolean;
  syncing: boolean;
  paused: boolean;
  error: string | null;
  last_sync: string | null;
  files_synced: number;
  active_transfers: number;
}

interface SyncPair {
  id: string;
  local_path: string;
  remote_folder_id: string;
  enabled: boolean;
  sync_mode: SyncMode;
  ignore_hidden?: boolean;
}

interface ConflictRecord {
  id: string;
  path: string;
  local_mtime: string;
  remote_mtime: string;
  local_size: number;
  remote_size: number;
  detected_at: string;
}

interface LogEntry {
  id: number;
  timestamp: string;
  event_type: "upload" | "download" | "delete" | "conflict" | "error" | "auth";
  path: string;
  details: string;
  status: string;
}
```

### Tauri Events (Daemon → Frontend)

The Rust backend forwards daemon notifications as Tauri events. The frontend listens via `@tauri-apps/api/event`:

| Event Name | Payload | Triggered By |
|---|---|---|
| `daemon:sync_progress` | `{pair_id, path, progress, total}` | File transfer progress |
| `daemon:sync_complete` | `{pair_id, uploaded, downloaded, errors}` | Sync cycle finished |
| `daemon:conflict_detected` | `{id, path, local_md5, remote_md5}` | New conflict |
| `daemon:status_changed` | `{pair_id, status}` | Pair status change |
| `daemon:error` | `{message, pair_id?}` | Error occurred |
| `daemon-connected` | — | Successfully connected to daemon |
| `daemon-offline` | — | Failed to connect after retries |
| `tray-action` | `string` | System tray menu item clicked (`"force_sync"`, `"toggle_pause"`) |

---

## Error Handling

All IPC errors follow the JSON-RPC 2.0 error format:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "error": {
    "code": -32602,
    "message": "local_path is required",
    "data": null
  }
}
```

Standard error codes:

| Code | Name | Description |
|---|---|---|
| `-32700` | Parse error | Invalid JSON |
| `-32600` | Invalid request | Missing `method` field |
| `-32601` | Method not found | Unknown method name |
| `-32602` | Invalid params | Missing or invalid parameters |
| `-32603` | Internal error | Unhandled exception in handler |
