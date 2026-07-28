# Configuration Reference

Complete reference for `config.toml` — the Cloud Drive Sync configuration file.

> **Not configured here:** the HTTP server (web UI + REST API) and the MCP server for AI assistants are enabled by command-line flags or environment variables, not by `config.toml` — they are per-invocation deployment choices rather than persistent settings. See [HTTP Server](Daemon#http-server-web-ui--rest-api), [MCP Server](Daemon#mcp-server-for-ai-assistants) and the [CLI options](CLI).

## File Location

The configuration file is created automatically on first run with sensible defaults. You do not need to create it manually.

| Platform | Default path |
|---|---|
| Linux | `~/.config/cloud-drive-sync/config.toml` |
| macOS | `~/Library/Application Support/cloud-drive-sync/config.toml` |
| Windows | `%APPDATA%\cloud-drive-sync\config.toml` |

To use a different path, pass `--config PATH` to any command:

```bash
cloud-drive-sync --config /etc/cloud-drive-sync/config.toml start --foreground
```

---

## Full Annotated Example

```toml
[general]
# Logging verbosity. One of: "debug", "info", "warning", "error"
log_level = "info"

[sync]
# How often (in seconds) to poll for remote changes
poll_interval = 30

# What to do when a file is modified on both sides simultaneously.
# "keep_both"    — rename the conflicting version and keep both files (default)
# "newest_wins"  — keep whichever file has the most recent modification time
# "ask_user"     — hold the conflict for manual resolution via UI or CLI
# "local_wins"   — always keep the local version; discard the remote change
# "remote_wins"  — always keep the remote version; discard the local change
conflict_strategy = "keep_both"

# Maximum number of simultaneous upload/download operations
max_concurrent_transfers = 4

# Seconds to wait after a local file change is detected before syncing.
# Coalesces rapid edits (e.g. autosaves) into a single sync action.
debounce_delay = 1.0

# Export Google Docs/Sheets/Slides to Office formats (.docx/.xlsx/.pptx)
# when downloading. Set to false to download as .gdoc stubs instead.
convert_google_docs = true

# Desktop notification settings
notify_sync_complete = true
notify_conflicts = true
notify_errors = true

# Bandwidth throttling in kilobytes per second. 0 = unlimited.
max_upload_kbps = 0
max_download_kbps = 0


# ── Sync Pair 1 ──────────────────────────────────────────────────────────────
[[sync.pairs]]
# Absolute path to the local directory to sync
local_path = "/home/alice/Documents"

# Remote folder identifier.
# Google Drive: folder ID from the URL, or "root" for My Drive top level
# Nextcloud: path relative to the WebDAV root, e.g. "/Documents"
# Dropbox: path, e.g. "/Documents"
# OneDrive: path, e.g. "/Documents"
# Box: folder ID from the URL
remote_folder_id = "root"

# Email address of the account to use for this pair
account_id = "alice@gmail.com"

# Provider for this pair
provider = "gdrive"

enabled = true
sync_mode = "two_way"     # "two_way", "upload_only", or "download_only"
ignore_hidden = true       # Exclude files/directories starting with "."

# Glob patterns to exclude (in addition to built-in defaults)
ignore_patterns = [
    "*.tmp",
    "*.log",
    "~$*",            # Office temp files
    "node_modules/",
]

# Per-pair conflict strategy — overrides the global setting for this pair only.
# Omit this key to inherit the global conflict_strategy.
# conflict_strategy = "newest_wins"


# ── Sync Pair 2 ──────────────────────────────────────────────────────────────
[[sync.pairs]]
local_path = "/home/alice/Pictures"
remote_folder_id = "0B3xRemoteFolderIdHere"
account_id = "alice@gmail.com"
provider = "gdrive"
enabled = true
sync_mode = "upload_only"
ignore_hidden = true
ignore_patterns = ["*.raw", "Lightroom/"]


# ── Sync Pair 3 (Nextcloud) ───────────────────────────────────────────────────
[[sync.pairs]]
local_path = "/home/alice/Work"
remote_folder_id = "/Work"
account_id = "alice@company.com"
provider = "nextcloud"
enabled = true
sync_mode = "two_way"
ignore_hidden = true


# ── Accounts ──────────────────────────────────────────────────────────────────
[[accounts]]
email = "alice@gmail.com"
display_name = "Alice (Google)"
provider = "gdrive"

[[accounts]]
email = "alice@company.com"
display_name = "Alice (Work Nextcloud)"
provider = "nextcloud"
server_url = "https://cloud.company.com"
```

---

## Section Reference

### `[general]`

| Key | Type | Default | Description |
|---|---|---|---|
| `log_level` | string | `"info"` | Logging verbosity: `"debug"`, `"info"`, `"warning"`, `"error"` |

---

### `[sync]`

Global sync behavior. All settings here can be overridden per pair where noted.

| Key | Type | Default | Description |
|---|---|---|---|
| `poll_interval` | integer | `30` | Seconds between remote change polls |
| `stopped` | boolean | `false` | Emergency stop state; set by the UI/CLI, not meant for hand-editing — see [Emergency Stop](Daemon#emergency-stop) |
| `max_deletions_per_sync` | integer | `100` | Refuse a sync pass deleting more than this many files in one direction, until confirmed. `0` disables the guard — see [Delete Protection](Daemon#delete-protection) |
| `conflict_strategy` | string | `"keep_both"` | Default conflict resolution strategy (see values below) |
| `max_concurrent_transfers` | integer | `4` | Parallel upload/download limit |
| `debounce_delay` | float | `1.0` | Seconds to wait after a local change before syncing |
| `convert_google_docs` | boolean | `true` | Export Google Docs/Sheets/Slides to Office formats on download |
| `notify_sync_complete` | boolean | `true` | Desktop notification when a sync cycle completes |
| `notify_conflicts` | boolean | `true` | Desktop notification when a conflict is detected |
| `notify_errors` | boolean | `true` | Desktop notification on sync errors |
| `max_upload_kbps` | integer | `0` | Upload bandwidth cap in KB/s; `0` = unlimited |
| `max_download_kbps` | integer | `0` | Download bandwidth cap in KB/s; `0` = unlimited |

#### Conflict strategy values

| Value | Behavior |
|---|---|
| `"keep_both"` | Rename the conflicting version (e.g. `file (conflict 2024-01-15).txt`) and keep both. Default. |
| `"newest_wins"` | Keep whichever version has the most recent modification time; discard the other. |
| `"ask_user"` | Pause the conflict for manual resolution. Visible in the UI under Conflicts and via `cloud-drive-sync conflicts`. |
| `"local_wins"` | Always keep the local copy; discard the remote change. |
| `"remote_wins"` | Always keep the remote copy; discard the local change. |

---

### `[[sync.pairs]]`

One `[[sync.pairs]]` section per sync pair. Multiple pairs are supported.

| Key | Type | Default | Description |
|---|---|---|---|
| `local_path` | string | (required) | Absolute path to the local directory |
| `remote_folder_id` | string | `"root"` | Remote folder — Drive folder ID, `"root"` for Drive root, or path for Nextcloud/Dropbox/OneDrive |
| `account_id` | string | (required) | Email of the account to use |
| `provider` | string | (required) | `"gdrive"`, `"nextcloud"`, `"dropbox"`, `"onedrive"`, or `"box"` |
| `enabled` | boolean | `true` | Whether this pair is active |
| `sync_mode` | string | `"two_way"` | `"two_way"`, `"upload_only"`, or `"download_only"` |
| `ignore_hidden` | boolean | `true` | Exclude files and directories whose names start with `.` |
| `ignore_patterns` | list of strings | `[]` | Glob patterns for files to exclude (see Selective Sync below) |
| `conflict_strategy` | string | (inherits global) | Per-pair conflict strategy; overrides `[sync].conflict_strategy` if set |
| `max_deletions_per_sync` | integer | (inherits global) | Per-pair delete cap; `0` disables delete protection for this pair |

#### `sync_mode` values

| Value | Description |
|---|---|
| `"two_way"` | Changes on either side are synced to the other. |
| `"upload_only"` | Local changes are uploaded; remote changes are ignored. |
| `"download_only"` | Remote changes are downloaded; local changes are ignored. |

---

### `[[accounts]]`

One `[[accounts]]` section per cloud account. Accounts are normally managed via `cloud-drive-sync account add` and written to this section automatically — you rarely need to edit this by hand.

| Key | Type | Description |
|---|---|---|
| `email` | string | Account identifier (email address) |
| `display_name` | string | Human-readable label shown in the UI |
| `provider` | string | `"gdrive"`, `"nextcloud"`, `"dropbox"`, `"onedrive"`, or `"box"` |
| `server_url` | string | Nextcloud only — base URL of the Nextcloud server, e.g. `https://cloud.example.com` |

Credentials (OAuth tokens, app passwords) are stored separately in the system keychain, not in `config.toml`.

---

## Selective Sync (Ignore Patterns)

There are two ways to exclude files from syncing.

### Method 1: `ignore_patterns` in config

Add glob patterns to the `ignore_patterns` list of a sync pair:

```toml
[[sync.pairs]]
local_path = "/home/alice/Documents"
# ...
ignore_patterns = [
    "*.tmp",
    "*.log",
    "~$*",
    "node_modules/",
    "build/",
    ".venv/",
]
```

Patterns are matched against file names (not full paths). A trailing `/` matches directories only.

### Method 2: `.cloud-drive-sync-ignore` file

Place a `.cloud-drive-sync-ignore` file in the root of any synced folder. It uses the same syntax as `.gitignore`:

```
# Ignore all log files
*.log

# Ignore temp files
*.tmp
~$*

# Ignore build output directories
build/
dist/
.cache/

# Ignore Python virtual environments
.venv/
__pycache__/
*.pyc

# Ignore a specific file
secrets.txt

# Ignore everything in a subdirectory except one file
private/*
!private/README.md
```

The `.cloud-drive-sync-ignore` file itself is never synced.

---

## Per-folder Ignore File

The `.cloud-drive-sync-ignore` file in a sync folder's root applies only to that folder. Subdirectories do not have their own ignore files — patterns from the root file apply recursively to the entire sync tree.

### Built-in defaults (always excluded)

The following are excluded from every sync pair regardless of configuration:

| Pattern | Reason |
|---|---|
| `.git/` | Git repository metadata — syncing this causes repository corruption |
| `__pycache__/` | Python bytecode cache — platform-specific, regenerated automatically |
| `.DS_Store` | macOS Finder metadata |
| `Thumbs.db` | Windows Explorer thumbnail cache |
| `.Trash-*/` | Linux trash directories |

These cannot be overridden — they are hardcoded as safety defaults to prevent common mistakes.
