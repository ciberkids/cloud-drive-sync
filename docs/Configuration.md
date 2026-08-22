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

### `[http]`

| Key | Type | Default | Description |
|---|---|---|---|
| `token` | string | generated on a fresh install | Shared token required on `/api/*` and the web UI. Empty or absent means no authentication. `--http-token` / `CDS_HTTP_TOKEN` override it — see [Authentication](Daemon#authentication) |

A **new** install generates this on first start and prints it to the log; an existing install is left alone, so an upgrade cannot lock you out. Delete the value to disable authentication again.

---

### `[sync]`

Global sync behavior. All settings here can be overridden per pair where noted.

| Key | Type | Default | Description |
|---|---|---|---|
| `poll_interval` | integer | `30` | Seconds between remote change polls |
| `stopped` | boolean | `false` | Emergency stop state; set by the UI/CLI, not meant for hand-editing — see [Emergency Stop](Daemon#emergency-stop) |
| `max_deletions_per_sync` | integer | `100` | Refuse deletions exceeding this many files in one direction within `deletion_window_seconds`, until confirmed. `0` disables the guard — see [Delete Protection](Daemon#delete-protection) |
| `deletion_window_seconds` | integer | `60` | Sliding window the deletion cap is counted over, across sync passes. `0` = per pass only |
| `conflict_strategy` | string | `"keep_both"` | Default conflict resolution strategy (see values below) |
| `max_concurrent_transfers` | integer | `4` | Parallel upload/download limit — see [Large File Uploads](Daemon#large-file-uploads) for how big files are chunked |
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
| `deletion_window_seconds` | integer | (inherits global) | Per-pair window for the delete cap |
| `force_polling` | boolean | `false` | Nextcloud only: skip `notify_push` and always walk the tree — see [Nextcloud Change Detection](Daemon#nextcloud-change-detection) |
| `uid` | string | (assigned automatically) | Stable identifier for this pair. Written by the daemon; you do not need to set it. See below. |

#### `uid` — the stable pair identifier

Assigned automatically. You never need to write it, and editing it by hand is a bad
idea — see below.

Internally a pair is identified by its **position** in this file: pair *N* is `pair_N`
in the engine and in the state database. That works because removing a pair renumbers
the stored rows to match, but the number is not stable across edits. `uid` is a stable
identity that does not move.

Behaviour worth knowing:

- **New pairs** get a random `uid` when they are created.
- **Pairs that predate the field** get one derived from `provider`, `account_id`,
  `local_path` and `remote_folder_id` the first time the config is loaded. It is
  written out the next time any setting is saved.
- Once written, the `uid` is fixed. Changing `local_path` afterwards does **not**
  change it — the derivation is a one-time bridge, not an ongoing function of those
  fields.
- Deleting the `uid` line causes a new one to be derived on the next load. If the pair
  has since been moved, the derived value will differ from the original, so anything
  keyed on the old value will see it as a different pair.

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

**Where credentials live.** Cloud credentials — OAuth tokens and Nextcloud app passwords — are **not** in `config.toml`. They are encrypted at rest in separate files, owner-readable only (`0600`); see [Authentication](Architecture#authentication) for the layout. No system keychain is involved, on any platform.

The one secret that *is* in `config.toml` is the web UI access token, under `[http] token`. That is why the file is written `0600` — it is the credential for `/api/*` and the whole UI. If you have copied your config somewhere less protected, treat that token as exposed and replace it.

---

### `[webhooks]`

Outbound HTTP callbacks. When something happens — a sync finishes, a conflict appears,
a mass deletion is refused — the daemon posts a JSON event to an endpoint you choose.

Configurable at two levels, which merge: **global** (`[webhooks]`) and **per pair**
(`[sync.pairs.webhooks]`). A pair can override a field of an inherited callback, switch
one off, or define one that exists nowhere else.

| Key | Type | Default | Description |
|---|---|---|---|
| `enabled` | boolean | `true` | Master switch for this level. `false` at a level disables every target at that level and below; a pair can set `true` to opt back in |
| `allow_private_addresses` | boolean | `true` | Global only. Set `false` on a shared deployment to refuse private and link-local endpoints. Not overridable per pair |

#### `[webhooks.defaults]`

Applied to every target that does not set the field itself. A target's own value always
wins.

| Key | Type | Default | Description |
|---|---|---|---|
| `timeout_seconds` | integer | `15` | Total request timeout. The connect timeout is fixed at 5s |
| `max_attempts` | integer | `5` | Attempts per event, including the first |
| `verify_tls` | boolean | `true` | Set `false` only for a self-signed endpoint on your own network |
| `include_paths` | boolean | `true` | When `false`, file paths are replaced by SHA-256 hashes and `local_path` is omitted. See [Paths are personal data](#paths-are-personal-data) |
| `max_files_per_event` | integer | `100` | Cap on path samples per event. `0` omits the lists entirely |

#### `[[webhooks.targets]]`

One block per callback.

| Key | Type | Default | Description |
|---|---|---|---|
| `name` | string | (required) | The merge key across levels. A pair overriding a callback names the same value |
| `define` | boolean | `false` | Required when introducing a **new** name, rejected for an existing one. See below |
| `url` | string | (required) | Where to POST |
| `events` | list of strings | (required) | Event names, or one glob segment (`conflict.*`, or `*` for everything). **Replaces** the inherited list |
| `events_add` | list of strings | `[]` | Add to the inherited list. Applied at this level only, never inherited further down |
| `events_remove` | list of strings | `[]` | Remove from the inherited list. Applied after `events_add`, at this level only |
| `headers` | table | `{}` | Extra request headers, merged per key with the inherited ones |
| `headers_remove` | list of strings | `[]` | Remove inherited header keys. This level only |
| `enabled` | boolean | (inherits) | `false` switches this target off for this level and below |
| `timeout_seconds` | integer | (inherits) | Overrides `[webhooks.defaults]` |
| `max_attempts` | integer | (inherits) | Overrides `[webhooks.defaults]` |
| `verify_tls` | boolean | (inherits) | Overrides `[webhooks.defaults]` |
| `include_paths` | boolean | (inherits) | Overrides `[webhooks.defaults]` |
| `max_files_per_event` | integer | (inherits) | Overrides `[webhooks.defaults]` |

Omit any key to inherit it from the level above. Omitting is not the same as setting a
default: an omitted key keeps tracking the level above when you later change it there.

#### `define` — why a new callback needs one extra word

`name` is how levels are merged, so without a declaration of intent the daemon cannot
tell "override the inherited callback" from "define my own". That ambiguity fails
silently in the worse direction: if you write a new callback whose name happens to
match a disabled one above, it inherits `enabled = false` and never fires, with a
perfectly valid URL and event list so nothing else looks wrong.

So: **`define = true` when the name is new**, and omit it when overriding. Getting it
the wrong way round is reported at startup and by `cloud-drive-sync webhook list`,
naming both levels.

#### `[webhooks.targets.auth]`

How to authenticate. `mode` is **required** whenever this block is present — the block
is replaced wholesale rather than merged, so a pair overriding just a token would
otherwise lose the inherited mode and silently downgrade to unauthenticated.

| Mode | Required keys | Header sent |
|---|---|---|
| `none` | — | none |
| `basic` | `username`, and `password` or `password_env` | `Authorization: Basic base64(user:pass)` |
| `bearer` | `token` or `token_env` | `Authorization: Bearer <token>` |
| `custom` | `header`, and `value` or `value_env` | `<header>: <value>` |

`custom` accepts `header = "Authorization"`, which covers schemes that are not modelled
(`Authorization: Token abc`).

Every secret has an `_env` twin — `token_env`, `password_env`, `value_env` — naming an
environment variable to read at request time. **Prefer it.** The value never touches the
config file, so a config backup carries no credential, and it is how a container or
systemd unit is meant to supply secrets. A literal wins over an `_env` reference if both
are set.

#### `[webhooks.targets.signature]`

Optional HMAC signature over the request body. Composes with any `auth` mode, including
`none` — which is the best combination if your receiver can verify signatures, because
then no long-lived credential is transmitted at all.

| Key | Type | Default | Description |
|---|---|---|---|
| `secret` / `secret_env` | string | (required) | The shared signing secret |
| `algorithm` | string | `"sha256"` | `"sha256"` or `"sha512"` |
| `header` | string | `"X-CDS-Signature"` | Header carrying `<algorithm>=<hex digest>` |
| `timestamp_header` | string | `"X-CDS-Timestamp"` | Header carrying the Unix timestamp |

The digest covers `"<timestamp>.<body>"`, so the timestamp is inside the signed
material and a captured body cannot be replayed later under a fresh one. Verify against
the **raw bytes** you received, not a re-serialised copy.

#### Events

| Event | When |
|---|---|
| `sync.completed` | A sync pass finished with something to report |
| `sync.failed` | A sync pass raised |
| `conflict.detected` | Both sides changed the same file |
| `deletion.blocked` | [Delete protection](Daemon#delete-protection) refused a batch |
| `activity.stopped` / `activity.resumed` | [Emergency stop](Daemon#emergency-stop) |
| `transfer.progress` | Per-chunk transfer progress. Very high volume; opt in deliberately |
| `webhook.test` | Sent by `cloud-drive-sync webhook test` |

`deletion.blocked` is the one worth wiring to something you actually watch. It fires
when the daemon has refused to delete files and is waiting for a human, and its payload
carries the full breach — including `ratio`, which is what distinguishes a wiped source
from an ordinary cleanup.

#### Paths are personal data

A webhook sends your filenames to another system. If that system is outside your
control, set `include_paths = false`: paths become SHA-256 hashes and `local_path` is
omitted from the payload, which is enough to count and correlate events but not to read
them.

#### Annotated example

```toml
[webhooks]
enabled = true

[webhooks.defaults]
timeout_seconds = 15
max_attempts = 5

# A callback for everything, authenticated with a token from the environment.
[[webhooks.targets]]
define = true
name = "ops-bus"
url = "https://ops.example.com/hooks/cds"
events = ["sync.completed", "sync.failed", "deletion.blocked"]
auth = { mode = "bearer", token_env = "CDS_OPS_TOKEN" }

# A Home Assistant automation on the LAN; no credential needed.
[[webhooks.targets]]
define = true
name = "home-assistant"
url = "http://ha.lan:8123/api/webhook/cds"
events = ["sync.completed", "conflict.detected"]
auth = { mode = "none" }

[[sync.pairs]]
uid = "3f7a1c68-2d4e-4f0b-9a11-8c5e6b0d2a94"
local_path = "/home/me/Documents"
account_id = "me@example.com"
provider = "gdrive"

  [sync.pairs.webhooks]

  # Only tell ops about refused deletions for this folder.
  [[sync.pairs.webhooks.targets]]
  name = "ops-bus"
  events = ["deletion.blocked"]

  # And do not notify Home Assistant about this folder at all.
  [[sync.pairs.webhooks.targets]]
  name = "home-assistant"
  enabled = false

  # A callback only this folder has. `define` because the name is new.
  [[sync.pairs.webhooks.targets]]
  define = true
  name = "photo-indexer"
  url = "http://nas.lan:9000/reindex"
  events = ["sync.completed"]
  auth = { mode = "custom", header = "X-API-Key", value_env = "NAS_KEY" }
```

Run `cloud-drive-sync webhook list --scope pair:<uid>` to see what that actually
resolves to, rather than working it out by hand.

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
