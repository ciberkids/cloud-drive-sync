# Cloud Drive Sync UI

Desktop application for monitoring and managing Cloud Drive Sync, built with Tauri v2 and React.

## Overview

The UI is a native Linux desktop application that provides:

- Real-time sync status dashboard
- Sync folder pair management
- Conflict resolution interface
- Activity log viewer
- Google account management
- System tray icon with status indicators

## Prerequisites

- **Node.js 18+**
- **Rust toolchain** (install via [rustup](https://rustup.rs/))
- **System libraries** for Tauri:

  Debian/Ubuntu:
  ```bash
  sudo apt install libwebkit2gtk-4.1-dev libgtk-3-dev libayatana-appindicator3-dev
  ```

  Fedora:
  ```bash
  sudo dnf install webkit2gtk4.1-devel gtk3-devel libayatana-appindicator-gtk3-devel
  ```

## Development

```bash
# Install dependencies
npm install

# Start development server (Vite + Tauri hot-reload)
npm run tauri dev
```

The dev server runs at `http://localhost:1420` with the Tauri window opening automatically.

## Building

```bash
# Build production bundle
npm run tauri build
```

Build artifacts are placed in `src-tauri/target/release/bundle/`:
- `.deb` package
- `.rpm` package
- `.appimage`

## Component Overview

| Component | File | Description |
|---|---|---|
| **App** | `src/App.tsx` | Root layout with sidebar navigation, routing, and daemon connection banner |
| **SyncStatus** | `src/components/SyncStatus.tsx` | Status dashboard: connection state, file counts, sync/pause controls |
| **Settings** | `src/components/Settings.tsx` | Sync pair management (add/remove), sync mode selector, conflict strategy selector |
| **ConflictDialog** | `src/components/ConflictDialog.tsx` | Lists unresolved conflicts with per-file and batch resolution buttons |
| **ActivityLog** | `src/components/ActivityLog.tsx` | Paginated, filterable activity feed with event type icons |
| **AccountManager** | `src/components/AccountManager.tsx` | Google account login/logout |
| **FolderPicker** | `src/components/FolderPicker.tsx` | Native local folder selection dialog via Tauri plugin |
| **RemoteFolderBrowser** | `src/components/RemoteFolderBrowser.tsx` | Hierarchical Google Drive folder browser for selecting remote sync targets |
| **RemoteFolderPicker** | `src/components/RemoteFolderPicker.tsx` | Wrapper component that combines the folder browser with selection UI |

### Lib Modules

| Module | File | Description |
|---|---|---|
| **IPC Client** | `src/lib/ipc.ts` | TypeScript wrappers around Tauri `invoke()` for all daemon commands |
| **Types** | `src/lib/types.ts` | Shared TypeScript interfaces (`DaemonStatus`, `SyncPair`, `ConflictRecord`, `LogEntry`) |
| **Hooks** | `src/lib/hooks.ts` | React hooks: `useStatus`, `useSyncPairs`, `useConflicts`, `useActivityLog`, `useDaemonEvent` |

### Rust Backend

| Module | File | Description |
|---|---|---|
| **main** | `src-tauri/src/main.rs` | Tauri app setup, daemon bridge initialization, tray setup, event forwarding |
| **commands** | `src-tauri/src/commands.rs` | Tauri command handlers that proxy calls through the daemon bridge |
| **ipc_bridge** | `src-tauri/src/ipc_bridge.rs` | Unix socket client that connects to the daemon's JSON-RPC server |
| **tray** | `src-tauri/src/tray.rs` | System tray icon and context menu management |

## Connecting to the Daemon

The UI automatically connects to the daemon's Unix socket at startup:

1. On launch, the Rust backend attempts to connect to `$XDG_RUNTIME_DIR/cloud-drive-sync.sock`
2. Connection retries up to 10 times with 3-second intervals
3. On success, the `daemon-connected` event is emitted to the frontend
4. On failure, the `daemon-offline` event is emitted
5. The user can manually reconnect via the `connect_daemon` command

Daemon notifications (sync progress, conflicts, errors) are forwarded as Tauri events and consumed by React hooks.

## Dependencies

### Frontend

| Package | Version | Purpose |
|---|---|---|
| react | ^18.3.0 | UI framework |
| react-dom | ^18.3.0 | React DOM renderer |
| react-router-dom | ^6.20.0 | Client-side routing |
| @tauri-apps/api | ^2.0.0 | Tauri frontend API |
| @tauri-apps/plugin-dialog | ^2.0.0 | Native file/folder dialogs |
| @tauri-apps/plugin-notification | ^2.0.0 | Desktop notifications |
| @tauri-apps/plugin-shell | ^2.0.0 | Shell command execution |

### Dev

| Package | Version | Purpose |
|---|---|---|
| typescript | ^5.3.0 | Type checking |
| vite | ^5.4.0 | Build tool and dev server |
| @vitejs/plugin-react | ^4.2.0 | React support for Vite |
| @tauri-apps/cli | ^2.0.0 | Tauri CLI tools |

### Rust Plugins

| Plugin | Purpose |
|---|---|
| tauri-plugin-shell | Shell command execution |
| tauri-plugin-notification | Desktop notifications |
| tauri-plugin-dialog | Native file dialogs |
