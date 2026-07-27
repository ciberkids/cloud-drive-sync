# Running Cloud Drive Sync in Docker

This guide covers everything you need to run the Cloud Drive Sync daemon as a Docker container — from a quick one-liner to a full Compose setup with multiple sync folders.

The image is published to the **GitHub Container Registry (GHCR)** at `ghcr.io/ciberkids/cloud-drive-sync`. GHCR is fully compatible with Docker, Podman, and any OCI-compliant runtime. You do **not** need a Docker Hub account to use it.

---

## Available Tags

| Tag | What it is |
|-----|-----------|
| `latest` | Always the newest stable release |
| `vX.Y.Z` | A specific version (e.g. `v1.2.0`) — pin this in production |

Pull the image:

```bash
docker pull ghcr.io/ciberkids/cloud-drive-sync:latest
```

---

## Quick Start

The fastest way to get running:

```bash
docker run -d \
  --name cloud-drive-sync \
  --restart unless-stopped \
  -p 8080:8080 \
  -e PUID=$(id -u) \
  -e PGID=$(id -g) \
  -v cloud-drive-sync-config:/root/.config/cloud-drive-sync \
  -v cloud-drive-sync-data:/root/.local/share/cloud-drive-sync \
  -v ~/Documents:/data/Documents \
  ghcr.io/ciberkids/cloud-drive-sync:latest
```

Then open **http://localhost:8080** in your browser — that's the web management UI where you can add accounts, configure sync pairs, and monitor transfers.

The container runs the daemon headless, and the daemon itself serves that UI over HTTP — there is no separate web server involved. The image's default command is `start --foreground --http-port 8080`, so it is on already and you only need to publish the port. It is the same interface as the desktop app, backed by the same request handler, so the browser, the CLI and the desktop app all drive one daemon identically. Full details and the endpoint reference: [HTTP Server (Web UI + REST API)](Daemon#http-server-web-ui--rest-api).

> ⚠️ **The UI has no authentication.** `-p 8080:8080` publishes it on every interface of your host, and anyone who reaches it can add or remove cloud accounts and change where your data syncs. For a single machine, publish it as `-p 127.0.0.1:8080:8080` instead so only that host can connect. To reach it from elsewhere, use an SSH tunnel or put a TLS-terminating reverse proxy with authentication in front of it — never forward the port from a router to the internet. See [Security](Daemon#security).

> **What those flags do:**
> - `-p 8080:8080` — exposes the web UI and REST API on port 8080
> - `-e PUID / PGID` — makes synced files owned by your host user instead of root (see [File Ownership](#file-ownership-puid--pgid) below)
> - The two named volumes keep your config and credentials safe across container restarts
> - `~/Documents:/data/Documents` — maps a host folder into the container so the daemon can sync it

---

## Using Docker Compose

For a more permanent setup — especially if you want to sync multiple folders — Docker Compose is the recommended approach.

Create a `docker-compose.yml` (or use the one already in `docker/docker-compose.yml` in the repo):

```yaml
version: "3.8"

services:
  daemon:
    image: ghcr.io/ciberkids/cloud-drive-sync:latest
    container_name: cloud-drive-sync
    restart: unless-stopped
    volumes:
      # Config and credentials (persist across restarts)
      - cloud-drive-sync-config:/root/.config/cloud-drive-sync
      - cloud-drive-sync-data:/root/.local/share/cloud-drive-sync
      # IPC socket for CLI access from host
      - cloud-drive-sync-run:/run/cloud-drive-sync
      # Your sync folders - add your own here
      - ~/Documents:/data/Documents
      - ~/Photos:/data/Photos
    ports:
      # HTTP REST API + Web UI
      - "8080:8080"
    environment:
      - XDG_RUNTIME_DIR=/run/cloud-drive-sync
      # Set to your host user/group IDs so synced files are owned by you,
      # not root.  Find them with: id -u && id -g
      - PUID=1000
      - PGID=1000

volumes:
  cloud-drive-sync-config:
  cloud-drive-sync-data:
  cloud-drive-sync-run:
```

**Start the container:**

```bash
docker compose up -d
```

**View logs:**

```bash
docker logs -f cloud-drive-sync
```

**Stop:**

```bash
docker compose down
```

Your config and data live in named Docker volumes, so they survive `docker compose down` and container recreations.

> **Tip:** Replace `PUID=1000` and `PGID=1000` with your actual user/group IDs. Run `id -u && id -g` to find them.

---

## File Ownership (PUID / PGID)

By default, the daemon runs as root inside the container. When it writes files into a bind-mounted folder (like `~/Documents`), those files end up owned by `root` on the host — which means you cannot edit or delete them without `sudo`.

Setting `PUID` and `PGID` fixes this:

- The container entrypoint creates an internal user with the UID/GID you specify.
- The daemon runs as that user, so every file it writes is owned by you on the host.
- The entrypoint automatically `chown`s the config and data volumes to that user on startup, so credentials and state files are also accessible.

```bash
-e PUID=$(id -u) -e PGID=$(id -g)
```

**Edge cases:**
- Setting `PUID=0` (or omitting both variables) preserves the original root behaviour — useful if you intentionally want root ownership or are running a privileged container.
- If you change `PUID`/`PGID` after the container has already written files, you may need to `chown` the named volumes manually.

---

## Adding Accounts

Once the container is running, connect your cloud storage accounts. You have two options:

### Option 1 — Web UI (recommended for all providers)

1. Open **http://localhost:8080**
2. Click **"Add Account"**
3. Follow the OAuth flow in your browser

This works for every supported provider and does not require a terminal.

### Option 2 — CLI (Google Drive headless auth)

If you are on a machine with no browser (e.g. a remote server), use the headless flow:

```bash
docker exec -it cloud-drive-sync \
  python -m cloud_drive_sync account add --provider gdrive --headless
```

The daemon prints an authorization URL. Open that URL on any device — phone, laptop, wherever — complete the sign-in, and the daemon picks up the token automatically.

---

## Adding Sync Pairs

A *sync pair* links a local folder inside the container to a remote folder in your cloud account.

### Via the web UI

Go to **http://localhost:8080/settings** and use the "Add Pair" form.

### Via the CLI

```bash
docker exec cloud-drive-sync \
  python -m cloud_drive_sync pair add \
  --local /data/Documents \
  --remote root \
  --account user@gmail.com
```

> **Important:** Use the container path (`/data/Documents`), not the host path (`~/Documents`). The two are the same folder — just seen from different sides of the volume mount.

---

## Useful Commands

```bash
# Follow live logs
docker logs -f cloud-drive-sync

# Check sync status
docker exec cloud-drive-sync python -m cloud_drive_sync status

# Trigger an immediate sync
docker exec cloud-drive-sync python -m cloud_drive_sync sync

# Stop the container (Compose)
docker compose down

# Stop the container (plain Docker)
docker stop cloud-drive-sync
```

---

## Upgrading

Pull the new image, then restart:

```bash
docker pull ghcr.io/ciberkids/cloud-drive-sync:latest
docker compose down && docker compose up -d
```

Because config and data live in named volumes, nothing is lost — the new container picks up right where the old one left off.

If you are pinning to a specific version tag, update the `image:` line in your `docker-compose.yml` before running the above commands.
