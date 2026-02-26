# GDrive Sync

Bidirectional Google Drive sync for Linux, with a native desktop UI.

```
┌──────────────────────────────────────────────────────────────────────┐
│                         GDrive Sync                                  │
│                                                                      │
│  ┌─────────────────────────┐        ┌─────────────────────────────┐  │
│  │    Daemon (Python)      │        │      UI (Tauri/React)       │  │
│  │                         │        │                             │  │
│  │  ┌───────────────────┐  │        │  ┌───────────────────────┐  │  │
│  │  │   SyncEngine      │  │        │  │  Status Dashboard     │  │  │
│  │  │  ┌─────────────┐  │  │ Unix   │  │  Settings             │  │  │
│  │  │  │  Planner     │  │  │ Socket │  │  Conflict Resolution  │  │  │
│  │  │  │  Executor    │  │◄─┼───────►│  │  Activity Log         │  │  │
│  │  │  │  Conflicts   │  │  │JSON-RPC│  │  Account Manager      │  │  │
│  │  │  └─────────────┘  │  │  2.0   │  └───────────────────────┘  │  │
│  │  └───────────────────┘  │        │                             │  │
│  │                         │        │  ┌───────────────────────┐  │  │
│  │  ┌─────────┐ ┌───────┐ │        │  │   System Tray Icon    │  │  │
│  │  │ Watcher │ │ SQLite│ │        │  └───────────────────────┘  │  │
│  │  │(watchdog)│ │  DB   │ │        └─────────────────────────────┘  │
│  │  └─────────┘ └───────┘ │                                         │
│  │                         │                                         │
│  │  ┌───────────────────┐  │                                         │
│  │  │   DriveClient     │  │        ┌─────────────────────────────┐  │
│  │  │  (API v3 wrapper) │──┼───────►│    Google Drive API v3      │  │
│  │  └───────────────────┘  │        └─────────────────────────────┘  │
│  └─────────────────────────┘                                         │
└──────────────────────────────────────────────────────────────────────┘
```

## Features

- **Bidirectional sync** — uploads local changes and downloads remote changes automatically
- **Conflict resolution** — three strategies: keep both copies, newest wins, or ask the user
- **Real-time monitoring** — local filesystem watcher (watchdog) + remote change polling
- **System tray** — always-on tray icon with status indicators (idle, syncing, error, conflict)
- **Multi-pair support** — sync multiple local folders to different Drive locations
- **Native desktop UI** — Tauri + React app for configuration and monitoring
- **Daemon architecture** — runs as a background service via systemd
- **XDG compliance** — config, data, and runtime files follow the XDG Base Directory spec
- **Encrypted credentials** — OAuth2 tokens stored encrypted on disk
- **Demo mode** — test the full UI and sync flow without a Google account

## Screenshots

> Screenshots show the Tauri desktop application.

### Status Dashboard

```
┌──────────────────────────────────────────────────────┐
│  ✔ Up to date                                        │
│  Last sync: 2025-01-15 14:32:00                      │
│                                                      │
│  ┌────────────┐  ┌─────────────────┐                 │
│  │ 247        │  │ 0               │                 │
│  │ Files      │  │ Active          │                 │
│  │ synced     │  │ transfers       │                 │
│  └────────────┘  └─────────────────┘                 │
│                                                      │
│  [ Sync Now ]  [ Pause ]                             │
└──────────────────────────────────────────────────────┘
```

### Settings

```
┌──────────────────────────────────────────────────────┐
│  Sync Folders                                        │
│  ┌────────────────────────────────────────────────┐  │
│  │ ~/Documents/work  →  My Drive      [Remove]   │  │
│  │ ~/Pictures        →  0A3x...folder [Remove]   │  │
│  └────────────────────────────────────────────────┘  │
│                                                      │
│  Local folder: [________________________] [Browse]   │
│  Remote ID:    [root___________________]             │
│  [ Add Sync Folder ]                                 │
│                                                      │
│  Conflict Resolution                                 │
│  Strategy: [Keep both (rename conflicting file) ▼]   │
└──────────────────────────────────────────────────────┘
```

### Conflicts

```
┌──────────────────────────────────────────────────────┐
│  Conflicts (2)    [Keep all local] [Keep all remote] │
│                                                      │
│  ┌────────────────────────────────────────────────┐  │
│  │ report.docx                                    │  │
│  │ Local: 245 KB  2025-01-15 14:30                │  │
│  │           vs                                   │  │
│  │ Remote: 251 KB  2025-01-15 14:28               │  │
│  │ [Keep local]  [Keep remote]  [Keep both]       │  │
│  └────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────┘
```

### Activity Log

```
┌──────────────────────────────────────────────────────┐
│  Activity   [All] [Upload] [Download] [Delete] ...   │
│                                                      │
│  ↑ notes.md                            14:32 success │
│  ↓ budget.xlsx                         14:31 success │
│  ✖ old-draft.txt                       14:30 success │
│  ⚠ report.docx                         14:28 conflict│
│  ↓ photo.jpg                           14:25 success │
│                                                      │
│  [ Load more ]                                       │
└──────────────────────────────────────────────────────┘
```

### Account Manager

```
┌──────────────────────────────────────────────────────┐
│  Google Account                                      │
│                                                      │
│  ● Connected                                         │
│                                                      │
│  [ Disconnect Account ]                              │
└──────────────────────────────────────────────────────┘
```

## Quick Start

```bash
# Clone and start everything in demo mode (no Google account needed)
git clone https://github.com/gdrive-sync/gdrive-sync.git
cd gdrive-sync
./dev.sh
```

This starts the daemon in demo mode and launches the Tauri UI.

## Prerequisites

- **Python 3.12+**
- **Node.js 18+**
- **Rust toolchain** (rustup + cargo)
- **System libraries** (for Tauri):
  - Debian/Ubuntu: `libwebkit2gtk-4.1-dev libgtk-3-dev libayatana-appindicator3-dev`
  - Fedora: `webkit2gtk4.1-devel gtk3-devel libayatana-appindicator-gtk3-devel`

## Installation

### From Source

```bash
# Set up both daemon and UI
make setup

# Or separately:
make setup-daemon   # Python venv + dependencies
make setup-ui       # npm install
```

### From Packages (planned)

`.deb`, `.rpm`, and `.appimage` packages will be available in future releases.

```bash
# Build packages locally
make build
# Artifacts are placed in ./artifacts/
```

### Systemd Service

```bash
make install-service   # Install and enable the systemd user service
make uninstall-service # Remove the service
```

## Configuration

The daemon reads its configuration from `~/.config/gdrive-sync/config.toml`:

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
```

See [daemon/README.md](daemon/README.md) for the full configuration reference.

## Project Structure

```
gdrive-sync/
├── daemon/              # Python sync daemon
│   ├── src/gdrive_sync/ # Source code
│   └── tests/           # pytest test suite
├── ui/                  # Tauri + React desktop UI
│   ├── src/             # React components
│   └── src-tauri/       # Rust backend
├── docs/                # Documentation
│   ├── ARCHITECTURE.md  # System design
│   ├── API.md           # IPC API reference
│   ├── SCREENSHOTS.md   # UI wireframes
│   └── CONTRIBUTING.md  # Contributor guide
├── installer/           # systemd service file
├── Makefile             # Build and dev commands
└── dev.sh               # One-liner dev setup
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md) — system design, sync algorithm, database schema
- [API Reference](docs/API.md) — full IPC method documentation with examples
- [UI Screenshots](docs/SCREENSHOTS.md) — wireframes for every UI page
- [Contributing](docs/CONTRIBUTING.md) — dev setup, code style, PR process
- [Daemon](daemon/README.md) — CLI usage, config reference, demo mode
- [UI](ui/README.md) — Tauri development and build instructions

## License

MIT
