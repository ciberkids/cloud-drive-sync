# UI Screenshots & Wireframes

ASCII wireframe mockups for every page and state of the Cloud Drive Sync desktop application.

## Status Dashboard

The main view. Shows overall sync health, file counts, and control buttons.

### Idle (Up to Date)

```
+-----------------------------------------------------------------+
| +----------+                                                     |
| | Cloud    |  +---------------------------------------------+   |
| |  Drive   |  |                                             |   |
| |  Sync    |  |   Up to date                                |   |
| |  * ------+  |   Last sync: Jan 15, 2025 2:32:00 PM       |   |
| |          |  |                                             |   |
| | Status <-|  |   +-------------+  +------------------+    |   |
| | Settings |  |   |    247      |  |       0          |    |   |
| | Conflicts|  |   |  Files      |  |  Active          |    |   |
| | Activity |  |   |  synced     |  |  transfers       |    |   |
| | Account  |  |   +-------------+  +------------------+    |   |
| |          |  |                                             |   |
| |          |  |   [  Sync Now  ]   [  Pause  ]             |   |
| |          |  |                                             |   |
| +----------+  +---------------------------------------------+   |
+-----------------------------------------------------------------+
```

### Syncing

```
+-----------------------------------------------------------------+
|                                                                  |
|   Syncing...                                                     |
|   Last sync: Jan 15, 2025 2:32:00 PM                            |
|                                                                  |
|   +-------------+  +------------------+                          |
|   |    249      |  |       2          |                          |
|   |  Files      |  |  Active          |                          |
|   |  synced     |  |  transfers       |                          |
|   +-------------+  +------------------+                          |
|                                                                  |
|   ==================....................  (indeterminate)         |
|                                                                  |
|   [  Sync Now  ]   [  Pause  ]                                   |
|    (disabled)                                                    |
+-----------------------------------------------------------------+
```

### Paused

```
+-----------------------------------------------------------------+
|                                                                  |
|   ||  Paused                                                     |
|   Last sync: Jan 15, 2025 2:32:00 PM                            |
|                                                                  |
|   [  Sync Now  ]   [  Resume  ]                                  |
|                                                                  |
+-----------------------------------------------------------------+
```

### Error

```
+-----------------------------------------------------------------+
|                                                                  |
|   X  Error: API rate limit exceeded                              |
|                                                                  |
|   [  Sync Now  ]   [  Pause  ]                                   |
|                                                                  |
+-----------------------------------------------------------------+
```

### Disconnected

```
+-----------------------------------------------------------------+
|                                                                  |
|   o  Disconnected                                                |
|                                                                  |
|   [  Sync Now  ]   [  Pause  ]                                   |
|    (disabled)       (disabled)                                   |
|                                                                  |
+-----------------------------------------------------------------+
```

## Settings

Manages sync pairs (grouped by account), notifications, and conflict strategy.
Each account group has a colored left border matching its cloud provider.

```
+-----------------------------------------------------------------+
|                                                                  |
|   Settings                                                       |
|                                                                  |
|   --- Sync Folders ------------------------------------------    |
|                                                                  |
|   # Google Drive  user@gmail.com                           *     |
|   +---------------------------------------------------------+   |
|   |  ~/Documents                                             |   |
|   |  Remote: My Drive                                        |   |
|   |  [Two-way v]   [x] Hide dotfiles  [Ignore Pat.]  [Rm]   |   |
|   +---------------------------------------------------------+   |
|   |  ~/Photos                                                |   |
|   |  Remote: /vacation-2025                                  |   |
|   |  [Upload only v]  [x] Hide dotfiles  [Ignore P.]  [Rm]  |   |
|   +---------------------------------------------------------+   |
|                                                                  |
|   # Dropbox  user@dropbox.com                              *     |
|   +---------------------------------------------------------+   |
|   |  ~/Shared                                                |   |
|   |  Remote: /Team                                           |   |
|   |  [Download only v]  [x] Hide dotfiles  [Ign. P.]  [Rm]  |   |
|   +---------------------------------------------------------+   |
|                                                                  |
|   # Nextcloud  user@cloud.example.com                      *     |
|   +---------------------------------------------------------+   |
|   |  ~/Projects                                              |   |
|   |  Remote: /dev                                            |   |
|   |  [Two-way v]   [x] Hide dotfiles  [Ignore Pat.]  [Rm]   |   |
|   +---------------------------------------------------------+   |
|                                                                  |
|   --- Add Sync Folder ----------------------------------------   |
|   [Remote Folder Browser]                                        |
|                                                                  |
|   --- Notifications ------------------------------------------   |
|                                                                  |
|   [x] Sync complete    [x] Conflicts    [x] Errors              |
|                                                                  |
|   --- Conflict Resolution ------------------------------------   |
|                                                                  |
|   Strategy: [ Keep both (rename conflicting file) v ]            |
|                                                                  |
|   --- Storage ------------------------------------------------   |
|   Configuration: ~/.config/cloud-drive-sync/config.toml          |
|   Data & database: ~/.local/share/cloud-drive-sync/              |
|                                                                  |
+-----------------------------------------------------------------+
```

The `#` header rows represent colored group headers. Each shows the provider
name (with a colored dot), the account email, and a green/grey dot for
connection status. `*` = connected, `o` = disconnected.

## Conflicts

Lists unresolved conflicts with per-file and batch resolution controls.

### With Conflicts

```
+-----------------------------------------------------------------+
|                                                                  |
|   Conflicts (2)                                                  |
|   [Keep all local]  [Keep all remote]  [Keep all both]           |
|                                                                  |
|   +---------------------------------------------------------+   |
|   |  report.docx                                             |   |
|   |  +------------------+      +------------------+          |   |
|   |  | Local            |  vs  | Remote           |          |   |
|   |  | 245 KB           |      | 251 KB           |          |   |
|   |  | Jan 15 2:30 PM   |      | Jan 15 2:28 PM   |          |   |
|   |  +------------------+      +------------------+          |   |
|   |  [Keep local]  [Keep remote]  [Keep both]                |   |
|   +---------------------------------------------------------+   |
|   |  presentation.pptx                                       |   |
|   |  +------------------+      +------------------+          |   |
|   |  | Local            |  vs  | Remote           |          |   |
|   |  | 1.2 MB           |      | 1.3 MB           |          |   |
|   |  | Jan 15 1:00 PM   |      | Jan 15 12:45 PM  |          |   |
|   |  +------------------+      +------------------+          |   |
|   |  [Keep local]  [Keep remote]  [Keep both]                |   |
|   +---------------------------------------------------------+   |
|                                                                  |
+-----------------------------------------------------------------+
```

### No Conflicts

```
+-----------------------------------------------------------------+
|                                                                  |
|   Conflicts                                                      |
|                                                                  |
|   No unresolved conflicts.                                       |
|                                                                  |
+-----------------------------------------------------------------+
```

## Activity Log

Timestamped, filterable feed of all sync operations.

```
+-----------------------------------------------------------------+
|                                                                  |
|   Activity                                                       |
|   [All] [Upload] [Download] [Delete] [Conflict] [Error]         |
|                                                                  |
|   +---------------------------------------------------------+   |
|   |  ^ notes.md                              14:32 success  |   |
|   |  v budget.xlsx                           14:31 success  |   |
|   |  X old-draft.txt                         14:30 success  |   |
|   |  ! report.docx                           14:28 conflict |   |
|   |  v photo.jpg                             14:25 success  |   |
|   |  ^ readme.md                             14:20 success  |   |
|   |  x backup.zip                            14:15 error    |   |
|   +---------------------------------------------------------+   |
|                                                                  |
|   [ Load more ]                                                  |
|                                                                  |
+-----------------------------------------------------------------+
```

Event type icons:
- `^` Upload
- `v` Download
- `X` Delete
- `!` Conflict
- `x` Error

## Account Manager

Multi-provider cloud account management. Each account is displayed as a card
with a colored left border matching the provider. The card shows the account
email, connection status, and a summary of which folders are synced.

### Multiple Accounts Connected

```
+-----------------------------------------------------------------+
|                                                                  |
|   Cloud Accounts                                                 |
|                                                                  |
|   +=========================================================+   |
|   | # Google Drive                             * Connected   |   |
|   |   user@gmail.com                                         |   |
|   |                                                          |   |
|   |   Syncing 2 folders:                                     |   |
|   |   +------------------------------------------------------+  |
|   |   | ~/Documents       <->   My Drive         Two-way     |  |
|   |   | ~/Photos          -->   /vacation-2025   Upload only  |  |
|   |   +------------------------------------------------------+  |
|   |                                                          |   |
|   |                                      [Remove Account]    |   |
|   +=========================================================+   |
|                                                                  |
|   +=========================================================+   |
|   | # Dropbox                                  * Connected   |   |
|   |   user@dropbox.com                                       |   |
|   |                                                          |   |
|   |   Syncing 1 folder:                                      |   |
|   |   +------------------------------------------------------+  |
|   |   | ~/Shared          <--   /Team        Download only    |  |
|   |   +------------------------------------------------------+  |
|   |                                                          |   |
|   |                                      [Remove Account]    |   |
|   +=========================================================+   |
|                                                                  |
|   +=========================================================+   |
|   | # OneDrive                                 * Connected   |   |
|   |   user@outlook.com                                       |   |
|   |                                                          |   |
|   |   Syncing 1 folder:                                      |   |
|   |   +------------------------------------------------------+  |
|   |   | ~/Work            <->   root             Two-way     |  |
|   |   +------------------------------------------------------+  |
|   |                                                          |   |
|   |                                      [Remove Account]    |   |
|   +=========================================================+   |
|                                                                  |
|   +=========================================================+   |
|   | # Nextcloud                                o Disconnected|   |
|   |   user@cloud.example.com                                 |   |
|   |                                                          |   |
|   |   No sync folders configured for this account.           |   |
|   |                                                          |   |
|   |                                      [Remove Account]    |   |
|   +=========================================================+   |
|                                                                  |
|   [ Google Drive v ]  [ Add Account ]                            |
|                                                                  |
+-----------------------------------------------------------------+
```

Each card shows:
- **Provider name** with a colored dot (blue for Google, purple for Dropbox, etc.)
- **Connection status badge** (`* Connected` in green, `o Disconnected` in grey)
- **Account email**
- **Folder summary** with directional arrows:
  - `<->` = Two-way sync
  - `-->` = Upload only (local to cloud)
  - `<--` = Download only (cloud to local)
- **Remove button** at bottom-right

### No Accounts

```
+-----------------------------------------------------------------+
|                                                                  |
|   Cloud Accounts                                                 |
|                                                                  |
|   No accounts configured. Add a cloud account to start syncing.  |
|                                                                  |
|   [ Google Drive v ]  [ Add Account ]                            |
|                                                                  |
+-----------------------------------------------------------------+
```

### Authenticating

```
+-----------------------------------------------------------------+
|                                                                  |
|   Cloud Accounts                                                 |
|                                                                  |
|   ...existing account cards...                                   |
|                                                                  |
|   [ Google Drive v ]  [ Waiting for browser... ]                 |
|                        (disabled)                                |
|                                                                  |
|   A browser window should open for sign-in. Complete the         |
|   authorization there, then return here.                         |
|                                                                  |
+-----------------------------------------------------------------+
```

## Tray Icon States

The system tray icon reflects the current daemon status.

| State | Icon | Description |
|---|---|---|
| **Idle** | Green checkmark | All files synced, no pending operations |
| **Syncing** | Blue rotating arrows | Active file transfers in progress |
| **Error** | Red X | A sync error occurred |
| **Conflict** | Yellow warning triangle | Unresolved conflicts pending user action |
| **Disconnected** | Grey circle | Cannot connect to daemon |

The tray icon tooltip shows the current status text (e.g., "Cloud Drive Sync - Connected", "Cloud Drive Sync - Syncing 2 files").

Right-clicking the tray icon shows a context menu:
- **Show/Hide Window** -- toggle the main window
- **Sync Now** -- trigger immediate sync
- **Pause/Resume** -- toggle sync pausing
- **Quit** -- exit the application

## Notification Types

Desktop notifications are sent via `tauri-plugin-notification`:

| Type | Title | Body Example |
|---|---|---|
| **Sync Complete** | Sync Complete | Sync finished |
| **Conflict Detected** | Sync Conflict | Conflict detected: report.docx |
| **Error** | Sync Error | A sync error occurred |

Notifications can be individually toggled in Settings > Notifications.
