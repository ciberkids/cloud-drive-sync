# Running Cloud Drive Sync as a Podman Quadlet

Podman Quadlets are the cleanest way to run Cloud Drive Sync as a persistent service on a Fedora, RHEL, openSUSE, or any Podman-based server. You get full `systemctl` integration — start, stop, restart, logs, enable at boot — without needing Docker Compose or writing a `.service` file by hand.

## What is a Quadlet?

A Quadlet is a small `.container` file (plus optional `.volume` files) that Podman reads and turns into a full systemd service automatically. Place the files in the right directory, run `systemctl daemon-reload`, and the service appears — ready to `start`, `enable`, and `journal`.

- **System-wide** (runs as root or a service account): `/etc/containers/systemd/`
- **Rootless / per-user** (recommended for homelabs): `~/.config/containers/systemd/`

Quadlets are the modern, Podman-native alternative to Docker Compose for always-on services. They require **Podman 4.4+**, which ships by default on Fedora 38+, RHEL 9.2+, and openSUSE Leap 15.5+.

## Prerequisites

- **Podman 4.4+**

  ```bash
  podman --version
  # podman version 4.x.y or higher
  ```

- **systemd**

  ```bash
  systemctl --version
  # systemd 252 or higher is fine
  ```

If you are on an older distro and `podman --version` prints something below 4.4, update Podman first — older versions do not support Quadlets.

## Installation (System-Wide)

System-wide installation runs the container as root (or a dedicated service account). Use this on a NAS or server where you want the service to start before any user logs in.

1. **Copy the quadlet files** from the repo's `installer/` directory into `/etc/containers/systemd/`:

   ```bash
   sudo cp installer/cloud-drive-sync.container \
              installer/cloud-drive-sync-config.volume \
              installer/cloud-drive-sync-data.volume \
              installer/cloud-drive-sync-run.volume \
            /etc/containers/systemd/
   ```

2. **Edit the container file** to match your setup:

   ```bash
   sudo $EDITOR /etc/containers/systemd/cloud-drive-sync.container
   ```

   - Set `PUID` and `PGID` to your host user's numeric IDs so synced files are owned correctly:

     ```bash
     id -u   # PUID
     id -g   # PGID
     ```

     > This applies to a **rootful** unit in `/etc/containers/systemd/`, where those IDs mean the same thing inside the container as outside. In a **rootless** unit (`~/.config/containers/systemd/`) your host user is already the container's root, so leave `PUID`/`PGID` unset and add `UserNS=keep-id` instead — setting them there points the daemon at a subordinate UID that does not own your bind-mounted folders.

   - Uncomment and edit the `Volume=` lines for your sync folders (see [Customising Volume Mounts](#customising-volume-mounts) below).

3. **Reload systemd** so it picks up the new unit:

   ```bash
   sudo systemctl daemon-reload
   ```

4. **Start the service:**

   ```bash
   sudo systemctl start cloud-drive-sync.service
   ```

5. **Enable it at boot:**

   ```bash
   sudo systemctl enable cloud-drive-sync.service
   ```

## Installation (Rootless Podman)

Rootless mode is ideal for homelabs — no root required, and the container runs entirely under your own user account.

1. **Create the Quadlet directory** if it does not exist:

   ```bash
   mkdir -p ~/.config/containers/systemd/
   ```

2. **Copy the quadlet files:**

   ```bash
   cp installer/cloud-drive-sync.container \
      installer/cloud-drive-sync-config.volume \
      installer/cloud-drive-sync-data.volume \
      installer/cloud-drive-sync-run.volume \
      ~/.config/containers/systemd/
   ```

3. **Edit the container file:**

   ```bash
   $EDITOR ~/.config/containers/systemd/cloud-drive-sync.container
   ```

   Update `PUID`/`PGID` and uncomment your sync volume mounts.

4. **Reload the user-level systemd:**

   ```bash
   systemctl --user daemon-reload
   ```

5. **Start and enable the service:**

   ```bash
   systemctl --user start cloud-drive-sync.service
   systemctl --user enable cloud-drive-sync.service
   ```

   > **Tip:** To keep the service running after you log out (useful on a server), enable lingering for your user:
   >
   > ```bash
   > loginctl enable-linger $USER
   > ```

## Managing the Service

All the usual `systemctl` commands work. For system-wide installation, omit `--user`.

| Task | Command |
|------|---------|
| Check status | `systemctl [--user] status cloud-drive-sync` |
| View live logs | `journalctl [--user] -u cloud-drive-sync -f` |
| Start | `systemctl [--user] start cloud-drive-sync` |
| Stop | `systemctl [--user] stop cloud-drive-sync` |
| Restart | `systemctl [--user] restart cloud-drive-sync` |
| Enable at boot | `systemctl [--user] enable cloud-drive-sync` |
| Disable at boot | `systemctl [--user] disable cloud-drive-sync` |

Once the service is running, the web management UI is available at:

```
http://<server-ip>:8080
```

Although this is a headless deployment with no desktop session, you still get the complete graphical interface: the daemon serves the web UI itself over HTTP, and no separate web server is involved. The container's default command is `start --foreground --http-port 8080`, so it is enabled without any configuration on your part — the `PublishPort=8080:8080` line in the Quadlet file is all that is needed to reach it. It is the same UI as the desktop app and is backed by the same request handler, so the browser, the CLI and the desktop app all act on one daemon. Full details and the endpoint reference: [HTTP Server (Web UI + REST API)](Daemon#http-server-web-ui--rest-api).

> ⚠️ **This UI is unauthenticated until you give it a token**, and `PublishPort=8080:8080` exposes it on every interface of the server — which matters more here than on a laptop, since this is typically a machine other people can reach. Without a token, anyone who opens that address can add or remove cloud accounts, change where your data syncs, and switch off delete protection.
>
> Set one in the Quadlet file. Keep it in a separate file rather than inline, so the secret is not world-readable alongside the unit:
>
> ```ini
> [Container]
> EnvironmentFile=%h/.config/cloud-drive-sync/secrets.env
> ```
>
> ```bash
> install -m 600 /dev/null ~/.config/cloud-drive-sync/secrets.env
> echo "CDS_HTTP_TOKEN=$(openssl rand -hex 32)" > ~/.config/cloud-drive-sync/secrets.env
> ```
>
> The daemon logs a warning on every start where the port is reachable off-box without a token, so an unprotected deployment is not silent.
>
> To restrict it further, publish it to loopback only — `PublishPort=127.0.0.1:8080:8080` — and reach it over an SSH tunnel:
>
> ```bash
> ssh -L 8080:localhost:8080 user@server
> # then open http://localhost:8080 on your own machine
> ```
>
> For permanent remote access, put a reverse proxy in front of it that terminates TLS, and set `CDS_HTTP_TOKEN` so the daemon is protected even if the proxy is bypassed. See [Authentication](Daemon#authentication).

### Letting an AI assistant manage sync

The container ships an [MCP](https://modelcontextprotocol.io) server for AI clients, off by default. Add to the `[Container]` section of your Quadlet file:

```ini
PublishPort=127.0.0.1:8081:8081
Environment=CDS_MCP_PORT=8081
Environment=CDS_MCP_ALLOWED_HOSTS=*
```

Reload with `systemctl --user daemon-reload && systemctl --user restart cloud-drive-sync`, then reach it at `http://localhost:8081/mcp` — over an SSH tunnel if the server is remote, since publishing to `127.0.0.1` deliberately keeps it off the network. It is read-only unless you also set `Environment=CDS_MCP_ALLOW_WRITES=1`. See [MCP Server](Daemon#mcp-server-for-ai-assistants).

## Adding Accounts

The easiest way to add a cloud account is through the **web UI** at `http://<server-ip>:8080`:

1. Go to the **Accounts** tab and click **Add Account**
2. Choose your provider (Google Drive, OneDrive, Dropbox, Nextcloud, Box)
3. Follow the on-screen instructions — for OAuth providers this involves copying an authorization URL and pasting back the redirect URL; for Nextcloud it is a simple form

You can also add accounts from the command line by exec-ing into the running container:

```bash
# Google Drive (headless — prints an auth URL, paste the redirect URL back)
podman exec -it cloud-drive-sync \
  python -m cloud_drive_sync account add --provider gdrive --headless

# OneDrive (device-code flow — open a URL on any device, no redirect needed)
podman exec -it cloud-drive-sync \
  python -m cloud_drive_sync account add --provider onedrive --headless

# List accounts to verify
podman exec cloud-drive-sync \
  python -m cloud_drive_sync account list
```

For full headless authentication instructions for each provider, see [DAEMON.md — Headless Authentication](DAEMON.md#headless-authentication).

## Auto-Updates

The `.container` file includes `AutoUpdate=registry`, which tells Podman to check the container registry for a newer image. Enable the built-in auto-update timer to have Podman check daily and restart the service automatically when a new release is published:

```bash
# System-wide
sudo systemctl enable --now podman-auto-update.timer

# Rootless
systemctl --user enable --now podman-auto-update.timer
```

To trigger an immediate update check:

```bash
podman auto-update
```

Podman will pull the new image, stop the old container, and restart it — all while preserving your volumes and configuration.

## Customising Volume Mounts

To sync folders from your host into the container, add `Volume=` lines to the `[Container]` section of the `.container` file. Container paths must be under `/data/`.

For example, to sync a media library and a documents folder:

```ini
[Container]
# ... other settings ...

Volume=/srv/media:/data/media
Volume=/home/alice/Documents:/data/documents
```

Then, inside the web UI (or via the REST API), add sync pairs pointing at `/data/media` and `/data/documents` as the local path.

You can mount as many folders as you like. The container path is what the daemon sees; the host path is where the files actually live on your server.

> **Heads-up:** After editing the `.container` file, reload the daemon and restart the service:
>
> ```bash
> systemctl [--user] daemon-reload
> systemctl [--user] restart cloud-drive-sync
> ```

## Volumes

Three named volumes are created automatically by Podman and persist across container restarts and image updates:

| Volume | Mount inside container | Purpose |
|--------|----------------------|---------|
| `cloud-drive-sync-config` | `/root/.config/cloud-drive-sync` | `config.toml` and sync-pair settings |
| `cloud-drive-sync-data` | `/root/.local/share/cloud-drive-sync` | OAuth credentials and sync database |
| `cloud-drive-sync-run` | `/run/cloud-drive-sync` | IPC socket (used by the CLI on the host) |

To inspect or back up a volume:

```bash
podman volume inspect cloud-drive-sync-config
podman volume export cloud-drive-sync-config > config-backup.tar
```

## Using the CLI from the Host

The IPC socket is exposed via the `cloud-drive-sync-run` volume, so you can run CLI commands from your host without exec-ing into the container, as long as you have the daemon package installed locally:

```bash
cloud-drive-sync status
cloud-drive-sync account list
cloud-drive-sync sync
```

The CLI automatically finds the socket at `/run/cloud-drive-sync` (via `XDG_RUNTIME_DIR`).

## Troubleshooting

**The service unit is not found after `daemon-reload`**

Make sure all four files (`.container` and the three `.volume` files) are in the same directory. Quadlet only generates the service if all referenced volume files are present alongside the container file.

**Container exits immediately / permission errors on volumes**

Check that `PUID` and `PGID` match your host user's actual IDs (`id -u` / `id -g`). If the named volumes were previously created as root, you may need to remove and recreate them:

```bash
podman volume rm cloud-drive-sync-config cloud-drive-sync-data cloud-drive-sync-run
systemctl [--user] start cloud-drive-sync
```

**Port 8080 is already in use**

Change the host-side port in the `PublishPort` line — for example `PublishPort=9090:8080` — then reload and restart.

**`podman auto-update` does not restart the service**

Verify the `.container` file contains `AutoUpdate=registry` and that the `podman-auto-update.timer` is active (`systemctl [--user] status podman-auto-update.timer`).
