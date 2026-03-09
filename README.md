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
- **Hidden file filtering** — exclude dotfiles and dot-directories from sync (configurable per pair)
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

## Quick Start (Demo Mode)

```bash
# Clone and start everything in demo mode (no Google account needed)
git clone https://github.com/YOUR_USER/gdrive-sync.git
cd gdrive-sync
./dev.sh              # daemon only
./dev.sh --with-ui    # daemon + Tauri UI
```

## Prerequisites

### Daemon only

- **Python 3.12+**

### UI (optional)

- **Node.js 18+** and **npm**
- **Rust toolchain** — install via [rustup](https://rustup.rs)
- **System libraries** for Tauri:

  | Distro | Packages |
  |--------|----------|
  | Fedora | `webkit2gtk4.1-devel gtk3-devel libayatana-appindicator-gtk3-devel` |
  | Ubuntu/Debian | `libwebkit2gtk-4.1-dev libgtk-3-dev libayatana-appindicator3-dev` |
  | Arch | `webkit2gtk-4.1 gtk3 libayatana-appindicator` |

## Manual Installation

### 1. Clone the repository

```bash
git clone https://github.com/YOUR_USER/gdrive-sync.git
cd gdrive-sync
```

### 2. Install the daemon

```bash
cd daemon

# Create a virtual environment
python3 -m venv .venv

# Install the daemon and all its dependencies
.venv/bin/pip install -e .

# (Optional) Install dev/test dependencies too
.venv/bin/pip install -e ".[dev]"
```

Verify the installation:

```bash
.venv/bin/python -m gdrive_sync --help
```

### 3. Install the UI

```bash
cd ui

# Install JavaScript dependencies
npm install

# (Optional) Verify Tauri compiles
npm run tauri build
```

The compiled binary will be at `ui/src-tauri/target/release/gdrive-sync-ui`.

## Running

### Start the daemon

The daemon must be running before the UI can connect to it.

```bash
cd daemon

# Foreground (see logs in terminal)
.venv/bin/python -m gdrive_sync --log-level debug start --foreground

# Background (daemonize)
.venv/bin/python -m gdrive_sync start

# Check status
.venv/bin/python -m gdrive_sync status

# Stop
.venv/bin/python -m gdrive_sync stop
```

On first launch without existing credentials, the daemon starts and waits for authentication. Connect via the UI and click **Sign in with Google** on the Account page.

### Start the UI

In a separate terminal:

```bash
cd ui

# Development mode (hot-reload)
npm run tauri dev

# Or run a release build directly
./src-tauri/target/release/gdrive-sync-ui
```

The UI connects to the daemon via a Unix socket at `$XDG_RUNTIME_DIR/gdrive-sync.sock` (typically `/run/user/1000/gdrive-sync.sock`).

### Run as a systemd service

To start the daemon automatically on login:

```bash
# Install the service
mkdir -p ~/.config/systemd/user
cp installer/gdrive-sync-daemon.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now gdrive-sync-daemon

# Check logs
journalctl --user -u gdrive-sync-daemon -f

# Uninstall
systemctl --user disable --now gdrive-sync-daemon
rm ~/.config/systemd/user/gdrive-sync-daemon.service
systemctl --user daemon-reload
```

**Note:** The systemd service expects the daemon binary at `~/.local/bin/gdrive-sync-daemon`. You can create a symlink:

```bash
mkdir -p ~/.local/bin
ln -sf "$(pwd)/daemon/.venv/bin/python -m gdrive_sync" ~/.local/bin/gdrive-sync-daemon
```

Or create a wrapper script:

```bash
cat > ~/.local/bin/gdrive-sync-daemon << 'EOF'
#!/bin/sh
exec /path/to/gdrive-sync/daemon/.venv/bin/python -m gdrive_sync "$@"
EOF
chmod +x ~/.local/bin/gdrive-sync-daemon
```

## Configuration

The daemon reads `~/.config/gdrive-sync/config.toml` (created automatically on first run):

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
sync_mode = "two_way"
ignore_hidden = true
```

You can also configure sync pairs through the UI Settings page.

See [daemon/README.md](daemon/README.md) for the full configuration reference.

## Testing

```bash
cd daemon

# Run all tests
.venv/bin/pytest -v

# Run integration tests only (uses demo mode, no Google credentials)
.venv/bin/pytest -v -m integration
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

- [Architecture](docs/ARCHITECTURE.md) — system design, sync algorithm, database schema
- [API Reference](docs/API.md) — full IPC method documentation with examples
- [Contributing](docs/CONTRIBUTING.md) — dev setup, code style, PR process
- [Daemon](daemon/README.md) — CLI usage, config reference, demo mode
- [UI](ui/README.md) — Tauri development and build instructions

## License

MIT
