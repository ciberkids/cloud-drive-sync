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

Manages sync pairs, notifications, and conflict strategy. Shows provider badges for non-Google pairs.

```
+-----------------------------------------------------------------+
|                                                                  |
|   Settings                                                       |
|                                                                  |
|   --- Sync Folders ------------------------------------------    |
|                                                                  |
|   +---------------------------------------------------------+   |
|   |  ~/Documents/work                  [Google Drive]        |   |
|   |  Remote: My Drive       [Two-way v]          [Remove]    |   |
|   +---------------------------------------------------------+   |
|   |  ~/Pictures                        [Dropbox]             |   |
|   |  Remote: /Photos        [Upload only v]       [Remove]   |   |
|   +---------------------------------------------------------+   |
|   |  ~/Projects                        [Nextcloud]           |   |
|   |  Remote: /dev           [Two-way v]          [Remove]    |   |
|   +---------------------------------------------------------+   |
|                                                                  |
|   --- Remote Folders -----------------------------------------   |
|   [Browse & Add Sync Folder]                                     |
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

Multi-provider cloud account management.

### Multiple Accounts Connected

```
+-----------------------------------------------------------------+
|                                                                  |
|   Cloud Accounts                                                 |
|                                                                  |
|   +---------------------------------------------------------+   |
|   |  user@gmail.com            [Google Drive]  * Connected   |   |
|   |                                             [Remove]     |   |
|   +---------------------------------------------------------+   |
|   |  user@dropbox.com          [Dropbox]       * Connected   |   |
|   |                                             [Remove]     |   |
|   +---------------------------------------------------------+   |
|   |  user@outlook.com          [OneDrive]      * Connected   |   |
|   |                                             [Remove]     |   |
|   +---------------------------------------------------------+   |
|   |  user@nextcloud.example    [Nextcloud]     * Connected   |   |
|   |                                             [Remove]     |   |
|   +---------------------------------------------------------+   |
|                                                                  |
|   Provider: [ Google Drive v ]                                   |
|             +--------------------+                               |
|             | Google Drive       |                               |
|             | Dropbox            |                               |
|             | OneDrive           |                               |
|             | Nextcloud          |                               |
|             | Box                |                               |
|             | Proton (coming)    |                               |
|             +--------------------+                               |
|                                                                  |
|   [ Add Account ]                                                |
|                                                                  |
+-----------------------------------------------------------------+
```

### No Accounts

```
+-----------------------------------------------------------------+
|                                                                  |
|   Cloud Accounts                                                 |
|                                                                  |
|   No accounts configured. Add a cloud account to start syncing.  |
|                                                                  |
|   Provider: [ Google Drive v ]                                   |
|   [ Add Account ]                                                |
|                                                                  |
+-----------------------------------------------------------------+
```

### Authenticating

```
+-----------------------------------------------------------------+
|                                                                  |
|   Cloud Accounts                                                 |
|                                                                  |
|   ...existing accounts...                                        |
|                                                                  |
|   [ Waiting for browser... ]                                     |
|    (disabled)                                                    |
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
