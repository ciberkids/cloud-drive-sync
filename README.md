# GDrive Sync

Bidirectional Google Drive sync for Linux, with a native desktop UI.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/architecture.png">
  <img alt="Architecture diagram" src="docs/screenshots/architecture.png">
</picture>

<details>
<summary>View as Mermaid</summary>

```mermaid
graph TB
    subgraph UI["UI (Tauri + React)"]
        direction TB
        subgraph Frontend["React Frontend"]
            StatusDashboard["Status Dashboard"]
            Settings
            ConflictDialog["Conflict Resolution"]
            ActivityLog["Activity Log"]
            AccountManager["Account Manager"]
        end
        Tray["System Tray Icon"]
        RustBackend["Rust Backend\n(DaemonBridge)"]
        Frontend --> RustBackend
    end

    subgraph Daemon["Daemon (Python 3.12)"]
        direction TB
        subgraph SyncEngine
            Planner
            Executor
            ConflictResolver["Conflict Resolver"]
        end
        Watcher["Watcher\n(watchdog)"]
        DB["SQLite DB\n(aiosqlite)"]
        DriveClient["DriveClient\n(API v3 wrapper)"]
        Watcher --> SyncEngine
        SyncEngine --> DB
        SyncEngine --> DriveClient
    end

    RustBackend <-->|"JSON-RPC 2.0\nUnix Socket"| SyncEngine
    DriveClient -->|"HTTPS"| GoogleDrive[("Google Drive\nAPI v3")]
```

</details>

## Features

- **Bidirectional sync** — uploads local changes and downloads remote changes automatically
- **Conflict resolution** — three strategies: keep both copies, newest wins, or ask the user
- **Real-time monitoring** — local filesystem watcher (watchdog) + remote change polling
- **System tray** — always-on tray icon with dynamic status indicators (idle, syncing, error, conflict)
- **Selective sync** — per-pair ignore patterns and `.gdrive-sync-ignore` files (gitignore-style)
- **Shared Drives** — full support for Google Workspace Shared Drives (Team Drives)
- **Multiple accounts** — connect multiple Google accounts, bind each sync pair to a specific account
- **Hidden file filtering** — exclude dotfiles and dot-directories from sync (configurable per pair)
- **Multi-pair support** — sync multiple local folders to different Drive locations
- **Native desktop UI** — Tauri + React app for configuration and monitoring
- **Daemon architecture** — runs as a background service via systemd
- **XDG compliance** — config, data, and runtime files follow the XDG Base Directory spec
- **Encrypted credentials** — OAuth2 tokens stored encrypted on disk (per-account)
- **Demo mode** — test the full UI and sync flow without a Google account

## Screenshots

> Screenshots show the Tauri desktop application.

### Status Dashboard

![Status Dashboard](docs/screenshots/status-dashboard.png)

### Settings

![Settings](docs/screenshots/settings.png)

### Conflicts

![Conflicts](docs/screenshots/conflicts.png)

### Activity Log

![Activity Log](docs/screenshots/activity-log.png)

### Account Manager

![Account Manager](docs/screenshots/account-manager.png)

> To add screenshots, place PNG files in `docs/screenshots/` matching the filenames above.

## Quick Start (Demo Mode)

```bash
# Clone and start everything in demo mode (no Google account needed)
git clone https://github.com/ciberkids/cloud-drive-sync.git
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

## Google Cloud Setup (Required)

GDrive Sync uses the Google Drive API via OAuth 2.0. You need to create your own OAuth client credentials before the app can access your Drive.

### 1. Create a Google Cloud project

1. Go to the [Google Cloud Console](https://console.cloud.google.com/)
2. Click **Select a project** (top bar) → **New Project**
3. Name it (e.g. `gdrive-sync`) and click **Create**

### 2. Enable the Google Drive API

1. In your project, go to **APIs & Services → Library**
2. Search for **Google Drive API**
3. Click it and press **Enable**

### 3. Configure the OAuth consent screen

1. Go to **APIs & Services → OAuth consent screen**
2. Select **External** user type (unless you have a Workspace org), click **Create**
3. Fill in the required fields:
   - **App name**: `GDrive Sync` (or anything you like)
   - **User support email**: your email
   - **Developer contact email**: your email
4. Click **Save and Continue**
5. On the **Scopes** page, click **Add or Remove Scopes**, find `https://www.googleapis.com/auth/drive`, check it, and click **Update** → **Save and Continue**
6. On the **Test users** page, click **Add Users**, enter your Google email address, and click **Save and Continue**
7. Review and click **Back to Dashboard**

> **Note:** While the app is in "Testing" mode, only the test users you added can authorize. This is fine for personal use. If you want others to use it, you'd need to publish the app (which requires Google's review).

### 4. Create OAuth client credentials

1. Go to **APIs & Services → Credentials**
2. Click **Create Credentials → OAuth client ID**
3. Application type: **Desktop app**
4. Name: `GDrive Sync Desktop` (or anything)
5. Click **Create**
6. Click **Download JSON** on the confirmation dialog

### 5. Install the credentials file

Move the downloaded JSON file to the GDrive Sync config directory:

```bash
mkdir -p ~/.config/gdrive-sync
mv ~/Downloads/client_secret_*.json ~/.config/gdrive-sync/client_secret.json
```

The daemon expects the file at `~/.config/gdrive-sync/client_secret.json`. On first launch, it will open your browser for Google sign-in using these credentials.

---

## Manual Installation

### 1. Clone the repository

```bash
git clone https://github.com/ciberkids/cloud-drive-sync.git
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
ignore_patterns = ["*.tmp", "node_modules", "build/"]
account_id = "user@gmail.com"

[[accounts]]
email = "user@gmail.com"
display_name = "user@gmail.com"
```

You can also configure sync pairs through the UI Settings page.

### Selective sync (ignore patterns)

Exclude files and folders from sync using glob patterns. There are two ways to set them:

**Per-pair config** — add `ignore_patterns` to a sync pair in `config.toml` (or use the "Ignore Patterns" button in the UI Settings):

```toml
[[sync.pairs]]
local_path = "/home/user/Projects"
ignore_patterns = ["*.log", "node_modules", "dist/", "__pycache__"]
```

**Per-folder file** — create a `.gdrive-sync-ignore` file in any synced folder root (gitignore-style, one pattern per line):

```
# Build artifacts
build/
dist/
*.o

# Logs
*.log

# IDE files
.idea/
.vscode/
```

Patterns from the config, the ignore file, and built-in defaults (`.git`, `__pycache__`, `.DS_Store`, etc.) are all merged together.

### Multiple accounts

Add multiple Google accounts via the UI Account Manager ("Add Google Account" button). Each sync pair can be bound to a specific account. On first upgrade from single-account to multi-account, existing credentials are automatically migrated.

### Shared Drives

Shared Drives (Team Drives) are automatically available. When browsing remote folders in the UI, Shared Drives appear as a separate section at the root level. All sync operations (upload, download, polling) work with Shared Drive files.

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
