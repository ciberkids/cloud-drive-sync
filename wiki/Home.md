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
│  │  ┌───────────────────┐  │        ┌─────────────────────────────┐  │
│  │  │   DriveClient     │  │        │    Google Drive API v3      │  │
│  │  │  (API v3 wrapper) │──┼───────►│                             │  │
│  │  └───────────────────┘  │        └─────────────────────────────┘  │
│  └─────────────────────────┘                                         │
└──────────────────────────────────────────────────────────────────────┘
```

## Features

- **Bidirectional sync** — uploads local changes and downloads remote changes automatically
- **Conflict resolution** — three strategies: keep both copies, newest wins, or ask the user
- **Real-time monitoring** — local filesystem watcher (watchdog) + remote change polling
- **System tray** — always-on tray icon with status indicators (idle, syncing, error, conflict)
- **Hidden file filtering** — exclude dotfiles and dot-directories from sync (configurable per pair)
- **Multi-pair support** — sync multiple local folders to different Drive locations
- **Native desktop UI** — Tauri + React app for configuration and monitoring
- **Daemon architecture** — runs as a background service via systemd
- **XDG compliance** — config, data, and runtime files follow the XDG Base Directory spec
- **Encrypted credentials** — OAuth2 tokens stored encrypted on disk
- **Demo mode** — test the full UI and sync flow without a Google account

## Quick Start (Demo Mode)

```bash
# Clone and start everything in demo mode (no Google account needed)
git clone https://github.com/ciberkids/cloud-drive-sync.git
cd cloud-drive-sync
./dev.sh              # daemon only
./dev.sh --with-ui    # daemon + Tauri UI
```

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
│   └── CONTRIBUTING.md  # Contributor guide
├── installer/           # systemd service, .desktop files, icons
├── Makefile             # Build and dev commands
└── dev.sh               # One-liner dev setup
```

## Documentation

- [[Installation]] — prerequisites, manual install, running, systemd setup
- [[Architecture]] — system design, sync algorithm, database schema
- [[API Reference|API-Reference]] — full IPC method documentation with examples
- [[Daemon]] — CLI usage, config reference, demo mode
- [[UI]] — Tauri development and build instructions
- [[Contributing]] — dev setup, code style, PR process

## License

MIT
