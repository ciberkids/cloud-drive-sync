# API Reference

GDrive Sync uses **JSON-RPC 2.0 over a Unix domain socket** for communication between the daemon and the UI.

- **Socket**: `$XDG_RUNTIME_DIR/gdrive-sync.sock`
- **Framing**: newline-delimited JSON (`\n` after each message)
- **Protocol**: JSON-RPC 2.0 — requests have an `id`, notifications do not

## IPC Methods (Client → Daemon)

### `get_status`

Get the current sync status for all configured pairs.

**Params**: none

**Response**: A dictionary keyed by pair ID, each value containing:

| Field | Type | Description |
|---|---|---|
| `local_path` | string | Local directory path |
| `remote_folder_id` | string | Google Drive folder ID |
| `active` | boolean | Whether the pair is active |
| `paused` | boolean | Whether syncing is paused |
| `last_sync` | string\|null | ISO 8601 timestamp of last sync |
| `active_transfers` | integer | Number of in-progress transfers |
| `errors` | string[] | Last 5 error messages |

**Example**:

```json
// Request
{"jsonrpc": "2.0", "method": "get_status", "id": 1}

// Response
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "pair_0": {
      "local_path": "/home/user/Documents",
      "remote_folder_id": "root",
      "active": true,
      "paused": false,
      "last_sync": "2025-01-15T14:32:00+00:00",
      "active_transfers": 0,
      "errors": []
    }
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
| `index` | integer | Pair index in the config |
| `local_path` | string | Local directory path |
| `remote_folder_id` | string | Google Drive folder ID |
| `enabled` | boolean | Whether the pair is enabled |

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
      "index": 0,
      "local_path": "/home/user/Documents",
      "remote_folder_id": "root",
      "enabled": true
    },
    {
      "index": 1,
      "local_path": "/home/user/Pictures",
      "remote_folder_id": "0A3xFolderIdHere",
      "enabled": true
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

**Response**:

| Field | Type | Description |
|---|---|---|
| `status` | string | `"added"` |
| `index` | integer | Index of the new pair |

**Example**:

```json
// Request
{
  "jsonrpc": "2.0",
  "method": "add_sync_pair",
  "params": {
    "local_path": "/home/user/Projects",
    "remote_folder_id": "root"
  },
  "id": 3
}

// Response
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "status": "added",
    "index": 2
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
| `pair_id` | string | yes | Pair identifier (e.g., `"pair_0"`) |

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
| `pair_id` | string | yes | Pair identifier |

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
| `pair_id` | string | yes | Pair identifier |

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
| `action` | string | Action type (upload, download, delete, conflict) |
| `path` | string | Relative file path |
| `pair_id` | string | Sync pair identifier |
| `status` | string | Result status |
| `detail` | string\|null | Human-readable detail |

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
      "action": "upload",
      "path": "notes.md",
      "pair_id": "pair_0",
      "status": "success",
      "detail": "Uploaded 2.4 KB"
    },
    {
      "id": 41,
      "timestamp": "2025-01-15T14:31:00+00:00",
      "action": "download",
      "path": "budget.xlsx",
      "pair_id": "pair_0",
      "status": "success",
      "detail": "Downloaded 156 KB"
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
| `status` | string | `"ok"` if auth callback is set, `"no_auth_callback"` otherwise |

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
| `add_sync_pair` | `localPath: string`, `remoteFolderId: string` | `SyncPair` | Add a new sync pair |
| `remove_sync_pair` | `pairId: string` | — | Remove a sync pair |
| `set_conflict_strategy` | `strategy: string` | — | Set conflict resolution strategy |
| `resolve_conflict` | `conflictId: string`, `resolution: ConflictResolution` | — | Resolve a specific conflict |
| `force_sync` | — | — | Trigger immediate sync |
| `pause_sync` | — | — | Pause syncing |
| `resume_sync` | — | — | Resume syncing |
| `get_activity_log` | `limit: number`, `offset: number` | `LogEntry[]` | Fetch activity log entries |
| `get_conflicts` | — | `ConflictRecord[]` | Get unresolved conflicts |
| `start_auth` | — | `unknown` | Start OAuth flow |
| `logout` | — | — | Clear credentials |
| `connect_daemon` | — | — | Reconnect to daemon socket |

### TypeScript Types

```typescript
type ConflictStrategy = "keep_both" | "newest_wins" | "ask_user";
type ConflictResolution = "keep_local" | "keep_remote" | "keep_both";

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
  event_type: "upload" | "download" | "delete" | "conflict" | "error";
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
