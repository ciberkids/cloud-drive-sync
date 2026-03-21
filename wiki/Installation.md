# Installation

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
git clone https://github.com/ciberkids/cloud-drive-sync.git
cd cloud-drive-sync
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
.venv/bin/python -m cloud_drive_sync --help
```

### 3. Install the UI

```bash
cd ui

# Install JavaScript dependencies
npm install

# (Optional) Verify Tauri compiles
npm run tauri build
```

The compiled binary will be at `ui/src-tauri/target/release/cloud-drive-sync-ui`.

## Running

### Start the daemon

The daemon must be running before the UI can connect to it.

```bash
cd daemon

# Foreground (see logs in terminal)
.venv/bin/python -m cloud_drive_sync --log-level debug start --foreground

# Background (daemonize)
.venv/bin/python -m cloud_drive_sync start

# Check status
.venv/bin/python -m cloud_drive_sync status

# Stop
.venv/bin/python -m cloud_drive_sync stop
```

On first launch without existing credentials, the daemon starts and waits for authentication. Connect via the UI and click **Sign in with Google** on the Account page.

### Start the UI

In a separate terminal:

```bash
cd ui

# Development mode (hot-reload)
npm run tauri dev

# Or run a release build directly
./src-tauri/target/release/cloud-drive-sync-ui
```

The UI connects to the daemon via a Unix socket at `$XDG_RUNTIME_DIR/cloud-drive-sync.sock` (typically `/run/user/1000/cloud-drive-sync.sock`).

## Run as a systemd service

To start the daemon automatically on login:

```bash
# Install the service
mkdir -p ~/.config/systemd/user
cp installer/cloud-drive-sync-daemon.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now cloud-drive-sync-daemon

# Check logs
journalctl --user -u cloud-drive-sync-daemon -f

# Uninstall
systemctl --user disable --now cloud-drive-sync-daemon
rm ~/.config/systemd/user/cloud-drive-sync-daemon.service
systemctl --user daemon-reload
```

**Note:** The systemd service expects the daemon binary at `~/.local/bin/cloud-drive-sync-daemon`. You can create a symlink:

```bash
mkdir -p ~/.local/bin
ln -sf "$(pwd)/daemon/.venv/bin/python -m cloud_drive_sync" ~/.local/bin/cloud-drive-sync-daemon
```

Or create a wrapper script:

```bash
cat > ~/.local/bin/cloud-drive-sync-daemon << 'EOF'
#!/bin/sh
exec /path/to/cloud-drive-sync/daemon/.venv/bin/python -m cloud_drive_sync "$@"
EOF
chmod +x ~/.local/bin/cloud-drive-sync-daemon
```

## Configuration

The daemon reads `~/.config/cloud-drive-sync/config.toml` (created automatically on first run):

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

See the [[Daemon]] page for the full configuration reference.

## Testing

```bash
cd daemon

# Run all tests
.venv/bin/pytest -v

# Run integration tests only (uses demo mode, no Google credentials)
.venv/bin/pytest -v -m integration
```
