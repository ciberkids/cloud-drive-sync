# Installation

Pre-built packages bundle both the **desktop UI** and the **sync daemon** — there is nothing extra to install or configure separately. Just install the package for your platform and you are ready to go.

> **Running on a server or NAS?** Skip this page.
> See [Docker](Docker) for container-based deployment, or [Quadlet](Quadlet) for rootless systemd-managed containers.
> This page covers the desktop app only.

---

## Quick Install (Linux)

An interactive script detects your distro and downloads the right package automatically:

```bash
curl -fsSL https://raw.githubusercontent.com/ciberkids/cloud-drive-sync/main/install.sh | bash
```

The script installs the package, enables the systemd user service, and warns you if `~/.local/bin` is not on your `PATH`. For a manual install, follow the platform-specific steps below.

---

## Linux

### Debian / Ubuntu / Mint (.deb)

```bash
# Download the latest .deb from the releases page
# https://github.com/ciberkids/cloud-drive-sync/releases/latest
# Then install it:
sudo dpkg -i cloud-drive-sync_*.deb
sudo apt-get install -f          # pulls in any missing dependencies

# Enable the daemon to start automatically on login
systemctl --user enable --now cloud-drive-sync-daemon

# Launch the desktop UI
cloud-drive-sync-ui
```

> **Tip:** You can also install directly from the command line using the `gh` CLI:
> ```bash
> gh release download --repo ciberkids/cloud-drive-sync --pattern '*.deb' --dir /tmp
> sudo dpkg -i /tmp/cloud-drive-sync_*.deb
> sudo apt-get install -f
> ```

---

### Fedora / RHEL / openSUSE (.rpm)

```bash
# Download the latest .rpm from the releases page
# https://github.com/ciberkids/cloud-drive-sync/releases/latest
# Then install it:
sudo rpm -U cloud-drive-sync-*.x86_64.rpm

# Enable the daemon to start automatically on login
systemctl --user enable --now cloud-drive-sync-daemon

# Launch the desktop UI
cloud-drive-sync-ui
```

> **Tip:** Using the `gh` CLI:
> ```bash
> gh release download --repo ciberkids/cloud-drive-sync --pattern '*.rpm' --dir /tmp
> sudo rpm -U /tmp/cloud-drive-sync-*.x86_64.rpm
> ```

---

### AppImage (any distro)

No installation or root access required — download and run directly:

```bash
# Download the latest AppImage
gh release download --repo ciberkids/cloud-drive-sync --pattern '*.AppImage' --dir ~/bin
chmod +x ~/bin/Cloud.Drive.Sync_*.AppImage

# Run it
~/bin/Cloud.Drive.Sync_*.AppImage
```

Or download manually from the [releases page](https://github.com/ciberkids/cloud-drive-sync/releases/latest), make it executable, and double-click it in your file manager.

The AppImage includes the daemon and starts it automatically when no running daemon is detected. No `systemctl` setup is needed.

---

### Flatpak (any distro)

```bash
# Download the Flatpak bundle
wget https://github.com/ciberkids/cloud-drive-sync/releases/latest/download/cloud-drive-sync.flatpak

# Install for the current user (no root needed)
flatpak install --user cloud-drive-sync.flatpak

# Launch
flatpak run com.cloud_drive_sync.app
```

The Flatpak bundles the daemon as a sidecar binary, so no separate install is needed. The daemon starts automatically when the app launches.

---

## macOS

Download the `.dmg` disk image from the [latest release](https://github.com/ciberkids/cloud-drive-sync/releases/latest):

1. Open the downloaded `.dmg` file.
2. Drag **Cloud Drive Sync** into your **Applications** folder.
3. Open it from Launchpad or Spotlight.

The app will ask for permission to manage files in your chosen sync folders on first launch.

> **Homebrew cask** — coming soon:
> ```bash
> brew install --cask cloud-drive-sync
> ```

---

## Windows

Download the installer (`.msi` or `.exe`) from the [latest release](https://github.com/ciberkids/cloud-drive-sync/releases/latest):

1. Run the downloaded installer and follow the prompts.
2. Cloud Drive Sync will appear in your Start menu and system tray after installation.
3. The daemon runs as a background process managed by the desktop app.

> **Scoop** — coming soon:
> ```bash
> scoop install cloud-drive-sync
> ```

---

## After Installation

Once installed, open the app and:

1. Go to **Accounts** and sign in to your cloud provider (Google Drive, Dropbox, OneDrive, Nextcloud, or Box).
2. Go to **Pairs** and add a sync folder pair (local folder ↔ remote folder).
3. Sync starts automatically. Check the **Status** dashboard to monitor progress.

For CLI usage and advanced configuration, see [CLI](CLI) and [Daemon](Daemon).

## Headless Servers (no desktop)

On a server or NAS with no desktop session you are not limited to the CLI — the daemon can serve the same web UI over HTTP that the desktop app shows, from its own process.

Unlike the Docker and Quadlet images, **the packaged installs above do not enable it by default.** The bundled systemd unit runs `start --foreground` with no HTTP port, so add the flag:

```bash
systemctl --user edit cloud-drive-sync-daemon
```

```ini
[Service]
ExecStart=
ExecStart=/usr/bin/cloud-drive-sync-daemon start --foreground --http-port 8080
```

```bash
systemctl --user daemon-reload
systemctl --user restart cloud-drive-sync-daemon
```

Then open `http://<server-ip>:8080`. The bare `ExecStart=` is required — it clears the original value, and systemd refuses a service with two `ExecStart` lines.

> ⚠️ The web UI has **no authentication** and binds all interfaces. Firewall the port, or reach it through an SSH tunnel (`ssh -L 8080:localhost:8080 user@server`) rather than exposing it. See [Security](Daemon#security).

If you would rather run headless in a container, [Docker](Docker) and [Quadlet](Quadlet) have the HTTP UI switched on out of the box.
