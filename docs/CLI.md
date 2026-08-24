# CLI Reference

Cloud Drive Sync includes a full command-line interface for managing the daemon, accounts, sync pairs, and conflicts without the desktop UI. All management commands communicate with the running daemon over a Unix socket.

## Quick Reference

```
cloud-drive-sync [OPTIONS] COMMAND

Daemon:
  start [--foreground] [--demo]    Start the sync daemon
  stop                             Stop the running daemon
  status                           Check if daemon is running
  auth                             Run OAuth2 flow (legacy, for Google Drive)

Accounts:
  account add [--provider P]       Add a new cloud account
  account remove <email>           Remove an account
  account list                     List all accounts

Sync Pairs:
  pair add --local PATH --remote ID [--account EMAIL] [--provider P]
  pair remove <pair_id>            Remove a sync pair
  pair list                        List all sync pairs

Sync Control:
  sync [pair_id]                   Trigger an immediate sync
  pause [pair_id]                  Pause syncing
  resume [pair_id]                 Resume syncing

Monitoring:
  activity [--limit N]             Show recent sync activity
  conflicts                        Show unresolved conflicts
  resolve <conflict_id> <resolution>   Resolve a conflict

Web UI sign-in:
  user set <name> [--password P]   Create or replace the web UI account
  user show                        Show the account (never the hash)
  user clear                       Remove the account; back to token-only

Webhooks:
  webhook list [--scope S] [--raw]  Show webhooks that will fire
  webhook status                    Delivery health per target
  webhook test [--scope S] [--name N]   Send a test event
```

## Global Options

These options apply to all commands:

```
--config PATH       Path to config.toml (default: ~/.config/cloud-drive-sync/config.toml)
--log-level LEVEL   Set log level: debug, info, warning, error
--help              Show help for any command
```

---

## Daemon Management

### `start`

Start the sync daemon. Must be running before any other management command can work.

```bash
# Start as background daemon
cloud-drive-sync start

# Start in foreground (logs to stdout, Ctrl+C to stop)
cloud-drive-sync start --foreground

# Start in demo mode (no real cloud account needed)
cloud-drive-sync start --demo

# Inspect and resolve deletions the fail-safe blocked
cloud-drive-sync deletions list
cloud-drive-sync deletions approve 0
cloud-drive-sync deletions reject 0

# Generate a token, then require it on the API and web UI
cloud-drive-sync gen-token
cloud-drive-sync start --foreground --http-port 8080 --http-token "$TOKEN"

# Stop ALL activity immediately, cancelling transfers in progress
cloud-drive-sync stop-activity
cloud-drive-sync stop-activity --account you@example.com
cloud-drive-sync resume-activity

# Start with HTTP REST API and web UI
cloud-drive-sync start --foreground --http-port 8080

# Also expose the MCP server so an AI assistant can inspect sync (read-only)
cloud-drive-sync start --foreground --http-port 8080 --mcp-port 8081

# Start with debug logging
cloud-drive-sync --log-level debug start --foreground
```

| Flag | Description |
|------|-------------|
| `--http-host ADDR` | Address the HTTP server binds to. Default `0.0.0.0`; use `127.0.0.1` to restrict to this machine. Env: `CDS_HTTP_HOST`. |
| `--http-token TOKEN` | Require this token on `/api/*` and the web UI. Overrides `[http] token` in the config. A new install generates one automatically; an existing install without one is unauthenticated. Env: `CDS_HTTP_TOKEN`. |
| `--mcp-token TOKEN` | Require this bearer token on the MCP endpoint. Env: `CDS_MCP_TOKEN`. |
| `--http-port PORT` | Enable HTTP REST API and web UI on the given port. Default 0 (disabled). Docker containers default to port 8080. |
| `--mcp-port PORT` | Enable the MCP server for AI assistants. Default 0 (disabled), containers included. Env: `CDS_MCP_PORT`. |
| `--mcp-host ADDR` | Address the MCP server binds to. Default `0.0.0.0`; use `127.0.0.1` to restrict to this machine. Env: `CDS_MCP_HOST`. |
| `--mcp-allow-writes` | Also expose MCP tools that change state. Read-only without it. Env: `CDS_MCP_ALLOW_WRITES`. |
| `--mcp-allowed-host HOST` | `Host` header the MCP server accepts, e.g. `nas.local:*`. Repeatable, defaults to localhost only, `*` accepts any. Env: `CDS_MCP_ALLOWED_HOSTS`. |

The daemon creates a PID file at `$XDG_RUNTIME_DIR/cloud-drive-sync/cloud-drive-sync.pid` and listens on a Unix socket at `$XDG_RUNTIME_DIR/cloud-drive-sync/cloud-drive-sync.sock` — typically `/run/user/<uid>/cloud-drive-sync/`.

### `stop`

Send a graceful shutdown signal (SIGTERM) to the running daemon.

```bash
cloud-drive-sync stop
```

### `status`

Check whether the daemon is running.

```bash
cloud-drive-sync status
# Output: "Daemon is running (PID 12345)" or "Daemon is not running."
```

### `auth`

Run the Google Drive OAuth2 authorization flow directly (legacy command). For multi-provider setups, use `account add` instead.

```bash
cloud-drive-sync auth
```

---

## Account Management

All account commands require the daemon to be running.

### `account add`

Add a new cloud storage account. Opens a browser for OAuth authorization (or prompts for credentials for Nextcloud).

```bash
# Add a Google Drive account (default)
cloud-drive-sync account add

# Add a Dropbox account
cloud-drive-sync account add --provider dropbox

# Add a OneDrive account
cloud-drive-sync account add --provider onedrive

# Add a Nextcloud account
cloud-drive-sync account add --provider nextcloud

# Add a Box account
cloud-drive-sync account add --provider box

# Use console-based auth (no browser, for headless servers)
cloud-drive-sync account add --provider gdrive --headless
```

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--provider` | `gdrive` | Cloud provider: `gdrive`, `dropbox`, `onedrive`, `nextcloud`, `box` |
| `--headless` | off | Use console-based auth flow (paste URL instead of opening browser). Required for Docker and SSH sessions. |

**What happens:**

1. For OAuth providers (Google, Dropbox, OneDrive, Box): opens your browser for sign-in
2. For Nextcloud: prompts for server URL, username, and app password
3. Credentials are encrypted at rest and stored per-account, readable only by your user (`0600`). A file written by an older version is upgraded to encrypted storage the first time it is read
4. The account appears in `account list` and can be assigned to sync pairs

**Docker usage:** When running in a container, use `docker exec` with `--headless`:

```bash
docker exec -it cloud-drive-sync \
  python -m cloud_drive_sync account add --provider gdrive --headless
```

### `account remove`

Remove an account and delete its stored credentials.

```bash
cloud-drive-sync account remove user@gmail.com
```

Any sync pairs still referencing this account lose their account binding and stop syncing until reassigned. The pair itself is kept — its local path, sync mode, ignore patterns and rules survive, so you can point it at another account.

**If the same address is registered with more than one provider**, say which one:

```bash
cloud-drive-sync account remove user@gmail.com --provider dropbox
```

Without `--provider` an ambiguous address is refused, and the error lists the candidates. Only the named provider's account and credentials are removed; the others are untouched.

### `account list`

List all configured accounts with their provider and connection status.

```bash
cloud-drive-sync account list
```

**Example output:**

```
  ● user@gmail.com [gdrive] (connected)
  ● user@dropbox.com [dropbox] (connected)
  ○ user@nextcloud.example.com [nextcloud] (disconnected)
```

Legend: `●` = connected, `○` = disconnected

---

## Web UI Sign-in

One account, no roles — see [Signing in to the web UI](Daemon#signing-in-to-the-web-ui) for why, and for the browser path that does the same thing.

These commands talk to the running daemon over its Unix socket, so access to the machine is what authorises them. That is also the recovery path: a forgotten password is fixed here, not by email.

### `user set`

Create or replace the account. One verb for both, because there is only one account to disambiguate.

```bash
cloud-drive-sync user set alice
Password: ********
Repeat for confirmation: ********
Web UI account set for alice.
The web UI now asks for this username and password.
```

**Options:**

| Option | Description |
|---|---|
| `--password TEXT` | The password, if you cannot be prompted (scripts). Prefer the prompt: a password on the command line lands in your shell history and the process list |

Minimum 10 characters, and it may not be the username or the access token. No composition rules — length is the only requirement that helps.

In a container, use the module form — the image ships `cloud-drive-sync-daemon`, so the bare `cloud-drive-sync` name is not on its PATH:

```bash
docker exec -it cloud-drive-sync python -m cloud_drive_sync user set alice
```

Running this on a daemon with no access token **turns browser authentication on** for that deployment; the command says so.

### `user show`

```bash
cloud-drive-sync user show
Username:         alice
Created:          2026-08-24T09:12:04.512+00:00
Password changed: 2026-08-24T09:40:55.108+00:00
```

Never prints the hash. With no account: *"No web UI account. The web UI uses the access token, if one is set."*

### `user clear`

```bash
cloud-drive-sync user clear
Remove the web UI account? Access falls back to the token, or to nothing if no token is set. [y/N]: y
Web UI account removed.
```

If no token is set either, this leaves the port **unauthenticated** — which is what the confirmation prompt is warning about.

---

## Sync Pair Management

Sync pairs map a local folder to a remote folder on a specific cloud account.

### `pair add`

Create a new sync pair.

```bash
# Basic: sync ~/Documents to Google Drive root
cloud-drive-sync pair add --local ~/Documents --remote root

# Sync to a specific Google Drive folder (use folder ID from URL)
cloud-drive-sync pair add --local ~/Work --remote 1A2B3C4D5E6F

# Bind to a specific account
cloud-drive-sync pair add --local ~/Photos --remote root --account user@gmail.com

# Sync to Dropbox (path-based remote)
cloud-drive-sync pair add --local ~/Shared --remote /Team --account user@dropbox.com --provider dropbox

# Sync to Nextcloud
cloud-drive-sync pair add --local ~/Projects --remote /dev --account user@nextcloud.example.com --provider nextcloud
```

**Options:**

| Option | Required | Description |
|--------|----------|-------------|
| `--local` | Yes | Absolute path to local folder |
| `--remote` | Yes | Remote folder ID or path (use `root` or `/` for the root) |
| `--account` | No | Account email to bind this pair to |
| `--provider` | No | Provider name (inferred from account if omitted) |

**Remote folder ID formats by provider:**

| Provider | Format | Example |
|----------|--------|---------|
| Google Drive | Folder ID from URL, or `root` | `1A2B3C4D5E6F`, `root` |
| Dropbox | Path starting with `/`, or empty for root | `/Documents`, `""` |
| OneDrive | Item ID, or `root` | `root` |
| Nextcloud | WebDAV path | `/`, `/Documents` |
| Box | Numeric folder ID, or `0` for root | `0`, `123456789` |

### `pair remove`

Remove a sync pair by its ID (shown in `pair list`).

```bash
cloud-drive-sync pair remove 0
```

### `pair list`

List all configured sync pairs.

```bash
cloud-drive-sync pair list
```

**Example output:**

```
  [0] /home/user/Documents <-> My Drive (two_way) [gdrive]
  [1] /home/user/Photos <-> /Photos (upload_only) [dropbox]
  [2] /home/user/Projects <-> /dev (two_way) [nextcloud]
```

---

## Sync Control

### `sync`

Trigger an immediate full sync. Without arguments, syncs all pairs. Pass a pair ID to sync a specific pair.

```bash
# Sync all pairs
cloud-drive-sync sync

# Sync only pair_0
cloud-drive-sync sync pair_0
```

### `pause`

Pause syncing. The daemon stays running but stops processing changes.

```bash
# Pause every pair
cloud-drive-sync pause

# Pause one pair, using the id `pair list` prints
cloud-drive-sync pause 0
```

Both id forms work — `0` as shown by `pair list`, and the internal `pair_0`. An id that matches no pair is an error with a non-zero exit, rather than a message saying it was paused.

### `resume`

Resume syncing after a pause.

```bash
# Resume every pair
cloud-drive-sync resume

# Resume one pair, using the id `pair list` prints
cloud-drive-sync resume 0
```

As with `pause`, both id forms are accepted and an unmatched id exits non-zero.

---

## Monitoring

### `activity`

Show recent sync activity (uploads, downloads, errors, etc.).

```bash
# Show last 20 entries (default)
cloud-drive-sync activity

# Show last 50 entries
cloud-drive-sync activity --limit 50
```

**Example output:**

```
  ✓ 2026-03-21 14:32:00  File uploaded: 1.2 KB at 45.3 KB/s  notes.md
  ✓ 2026-03-21 14:31:45  File downloaded: 3.4 MB at 2.1 MB/s  budget.xlsx
  ✗ 2026-03-21 14:30:12  Sync error: Rate limit exceeded
  · 2026-03-21 14:28:00  Sync complete: 3 uploaded, 1 downloaded
```

Legend: `✓` = success, `✗` = error, `·` = info

### `conflicts`

List all unresolved file conflicts.

```bash
cloud-drive-sync conflicts
```

**Example output:**

```
  [1] report.docx (detected 2026-03-21T14:28:00)
  [3] presentation.pptx (detected 2026-03-21T12:15:00)
```

### `resolve`

Resolve a conflict by its ID.

```bash
# Keep the local version
cloud-drive-sync resolve 1 keep_local

# Keep the remote version
cloud-drive-sync resolve 1 keep_remote

# Keep both (remote file is renamed with a conflict suffix)
cloud-drive-sync resolve 1 keep_both
```

**Resolution strategies:**

| Strategy | What happens |
|----------|-------------|
| `keep_local` | Overwrite the remote file with the local version |
| `keep_remote` | Overwrite the local file with the remote version |
| `keep_both` | Keep both; the remote copy is renamed (e.g. `file (conflict).txt`) |

---

## Cross-Cloud Sync via CLI

You can use the CLI to set up cross-cloud sync between two providers. The pattern is: one pair downloads from provider A, another uploads to provider B, both pointing to the same local directory.

### Example: Google Drive to Dropbox

```bash
# 1. Add both accounts
cloud-drive-sync account add --provider gdrive
cloud-drive-sync account add --provider dropbox

# 2. Create the bridge directory
mkdir -p ~/cloud-bridge

# 3. Add download-only pair from Google Drive
cloud-drive-sync pair add \
  --local ~/cloud-bridge \
  --remote root \
  --account user@gmail.com \
  --provider gdrive

# 4. Add upload-only pair to Dropbox
cloud-drive-sync pair add \
  --local ~/cloud-bridge \
  --remote /backup \
  --account user@dropbox.com \
  --provider dropbox

# 5. Set sync modes (edit config.toml or use the UI)
# pair_0: sync_mode = "download_only"
# pair_1: sync_mode = "upload_only"

# 6. Trigger sync
cloud-drive-sync sync
```

### Example: Nextcloud to Box

```bash
cloud-drive-sync account add --provider nextcloud
cloud-drive-sync account add --provider box

mkdir -p ~/nc-to-box

cloud-drive-sync pair add \
  --local ~/nc-to-box \
  --remote /shared \
  --account user@nextcloud.example.com \
  --provider nextcloud

cloud-drive-sync pair add \
  --local ~/nc-to-box \
  --remote 0 \
  --account user@box.com \
  --provider box
```

---

## Systemd Integration

For always-on syncing, run the daemon as a systemd user service:

```bash
# Install the service file
mkdir -p ~/.config/systemd/user
cp installer/cloud-drive-sync-daemon.service ~/.config/systemd/user/
systemctl --user daemon-reload

# Enable and start
systemctl --user enable --now cloud-drive-sync-daemon

# Check logs
journalctl --user -u cloud-drive-sync-daemon -f

# Restart after config changes
systemctl --user restart cloud-drive-sync-daemon

# Disable
systemctl --user disable --now cloud-drive-sync-daemon
```

The CLI commands (`account`, `pair`, `sync`, etc.) work while the systemd service is running since they communicate over the Unix socket.

---

## Troubleshooting

### "Daemon socket not found"

The daemon isn't running. Start it first:

```bash
cloud-drive-sync start
# or
systemctl --user start cloud-drive-sync-daemon
```

### "Sign-in failed" in the web UI

The daemon answers one error for a wrong username *and* a wrong password, on purpose — it will not tell an attacker which half was right. Check the username with `cloud-drive-sync user show`, and reset the password with `cloud-drive-sync user set <name>`.

Repeated failures are delayed with a growing pause (never a lockout), so a burst of attempts gets slower rather than blocked. Wait, or run `user set` on the host.

If sign-in succeeds and then immediately asks again, the session cookie is not coming back. Behind a TLS-terminating proxy, set `[http] trust_proxy = true` so the cookie is marked `Secure` for a connection the daemon itself sees as plain HTTP.

**Signed out after every restart?** That is expected: sessions live in memory, not on disk.

### "Not authenticated"

No accounts are configured. Add one:

```bash
cloud-drive-sync account add --provider gdrive
```

### "Connection refused"

The socket file may be stale. Stop and restart:

```bash
cloud-drive-sync stop
cloud-drive-sync start --foreground  # check for errors
```

### Debug logging

Run with verbose output to diagnose issues:

```bash
cloud-drive-sync --log-level debug start --foreground
```

Or check the systemd journal:

```bash
journalctl --user -u cloud-drive-sync-daemon --since "5 min ago"
```
