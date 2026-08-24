# Daemon

The Python daemon that performs bidirectional Google Drive synchronization.

## Overview

The daemon runs on Linux, macOS, and Windows as a background process (or systemd user service on Linux) and handles:

- Watching local directories for changes (via watchdog)
- Polling Google Drive for remote changes
- Planning and executing sync operations (upload, download, delete)
- Detecting and resolving conflicts
- Serving an IPC interface over a Unix domain socket

## CLI Usage

```
cloud-drive-sync-daemon [OPTIONS] COMMAND
```

### Global Options

| Option | Description |
|---|---|
| `--config PATH` | Path to `config.toml` (default: `~/.config/cloud-drive-sync/config.toml`) |
| `--log-level LEVEL` | Override log level: `debug`, `info`, `warning`, `error` |

### Commands

#### `start`

Start the sync daemon.

```bash
cloud-drive-sync-daemon start              # Daemonize (fork to background, Linux/macOS only)
cloud-drive-sync-daemon start --foreground  # Run in foreground (for development/systemd)
cloud-drive-sync-daemon start --demo        # Run with mock Drive API (no Google account needed)
```

| Flag | Description |
|---|---|
| `--foreground` | Run in the foreground instead of forking |
| `--demo` | Use mock Drive client with synthetic test data |
| `--config PATH` | Path to config file |

> **Windows note:** The daemon always runs in foreground mode on Windows (fork is not supported). The Tauri UI manages the daemon lifecycle as a sidecar process.

#### `stop`

Stop a running daemon by sending SIGTERM.

```bash
cloud-drive-sync-daemon stop
```

#### `status`

Check whether the daemon is running.

```bash
cloud-drive-sync-daemon status
# Output: "Daemon is running (PID 12345)" or "Daemon is not running."
```

#### `auth`

Run the OAuth2 authorization flow interactively.

```bash
cloud-drive-sync-daemon auth
# Output: "Authorization successful. Credentials stored and ready to use."
```

## Configuration Reference

The daemon reads configuration from a platform-specific path (or the path specified by `--config`). All values have sensible defaults.

| Platform | Default config path |
|---|---|
| Linux | `~/.config/cloud-drive-sync/config.toml` |
| macOS | `~/Library/Application Support/cloud-drive-sync/config.toml` |
| Windows | `%APPDATA%\cloud-drive-sync\config.toml` |

### `[general]`

| Key | Type | Default | Description |
|---|---|---|---|
| `log_level` | string | `"info"` | Logging level: `debug`, `info`, `warning`, `error` |

### `[sync]`

| Key | Type | Default | Description |
|---|---|---|---|
| `poll_interval` | integer | `30` | Seconds between remote change polls |
| `conflict_strategy` | string | `"keep_both"` | How to handle conflicts: `keep_both`, `newest_wins`, `ask_user` |
| `max_concurrent_transfers` | integer | `4` | Max simultaneous upload/download operations |
| `debounce_delay` | float | `1.0` | Seconds to wait before processing a local change (coalesces rapid edits) |

### `[[sync.pairs]]`

Each `[[sync.pairs]]` entry defines a local-to-remote folder mapping.

| Key | Type | Default | Description |
|---|---|---|---|
| `local_path` | string | (required) | Absolute path to the local directory |
| `remote_folder_id` | string | `"root"` | Google Drive folder ID (`"root"` = My Drive top level) |
| `enabled` | boolean | `true` | Whether this pair should be synced |
| `sync_mode` | string | `"two_way"` | Sync direction: `"two_way"`, `"upload_only"`, or `"download_only"` |
| `ignore_hidden` | boolean | `true` | Whether to exclude hidden files/directories (names starting with `.`) from sync |

### Example Configuration

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

[[sync.pairs]]
local_path = "/home/user/Pictures"
remote_folder_id = "0A3xRemoteFolderIdHere"
enabled = true
sync_mode = "upload_only"
ignore_hidden = true
```

## Demo Mode

Demo mode runs the full daemon with a mock Drive client instead of connecting to Google's API:

```bash
cloud-drive-sync-daemon start --foreground --demo
```

What demo mode does:

- Creates a temporary local sync directory with sample files
- Simulates remote files and changes
- Processes sync operations (upload/download/conflict) against the mock backend
- Responds to all IPC commands normally

This allows the UI to be fully tested without a Google account or network access.

## Headless Authentication

The `--headless` flag disables automatic browser opening. Instead, the daemon prints a URL or code to the console, and you complete authorization on any device with a browser (your phone, a laptop, etc.). This works over SSH, in Docker containers, and on servers without a display.

### Google Drive

```bash
cloud-drive-sync account add --provider gdrive --headless
```

What happens:
1. The daemon prints an authorization URL
2. Open that URL in **any browser** (on your phone, another computer, etc.)
3. Sign in with your Google account and click "Allow"
4. Google redirects to `http://localhost?code=...` — this page won't load (that's normal)
5. Copy the **full URL** from your browser's address bar and paste it back into the terminal
6. The daemon extracts the code and completes authorization

Output looks like:
```
Visit this URL to authorize:

  https://accounts.google.com/o/oauth2/auth?client_id=...&scope=...

Sign in, click 'Allow'.
Your browser will redirect to a localhost URL that won't load.
Copy the FULL URL from your browser's address bar and paste it here.

Paste the redirect URL (or just the code): http://localhost?code=4/0A...
```

#### Via the Web UI

When using the web UI (e.g., `http://localhost:8080/` or behind a reverse proxy):

1. Go to the **Accounts** tab and click **Add Account**
2. A "Sign in with Google" button appears — click it to open the auth page
3. Sign in and click "Allow"
4. Your browser redirects to a localhost page that won't load — that's expected
5. Copy the **entire URL** from your browser's address bar (it contains `?code=...`)
6. Paste it into the input field in the web UI and click **Complete Setup**

This works regardless of domain, port, or reverse proxy configuration.

### OneDrive

```bash
cloud-drive-sync account add --provider onedrive --headless
```

What happens:
1. The daemon prints a **device code** and a verification URL
2. Open `https://microsoft.com/devicelogin` on any device
3. Enter the code shown in the terminal
4. Sign in with your Microsoft account and approve
5. The daemon detects the approval automatically (polls in the background)

Output looks like:
```
To sign in, use a web browser to open https://microsoft.com/devicelogin
and enter the code ABCD-EFGH to authenticate.
```

This is the most Docker-friendly flow — no redirect needed.

### Dropbox

```bash
cloud-drive-sync account add --provider dropbox --headless
```

What happens:
1. The daemon prints an authorization URL
2. Open that URL in any browser and click "Allow"
3. Dropbox shows an authorization code on screen
4. Copy the code and paste it back into the terminal

Output looks like:
```
1. Go to: https://www.dropbox.com/oauth2/authorize?...
2. Click 'Allow' (you might have to log in first)
3. Copy the authorization code.

Enter the authorization code: _
```

### Nextcloud

**Via the UI (recommended):** Select "Nextcloud" in the Account Manager, fill in your server URL, username, and app password, then click Connect. No browser or terminal prompts needed.

**Via CLI:**
```bash
cloud-drive-sync account add --provider nextcloud --headless
```

The CLI prompts interactively for server URL, username, and app password (requires a TTY).

**Creating an app password:** In Nextcloud, go to Settings → Security → Devices & sessions → create a new app-specific password. Use that password — not your regular login password.

### Box

```bash
cloud-drive-sync account add --provider box --headless
```

What happens:
1. The daemon prints an authorization URL
2. Open that URL in any browser and sign in to Box
3. Box shows an authorization code
4. Paste it back into the terminal

### Docker Usage

The recommended way to add accounts in Docker is via the HTTP web UI at `http://localhost:8080`:
- **Google Drive / Dropbox / OneDrive / Box**: Click "Add Account", complete the OAuth browser flow using the provided URL, then paste the redirect URL back.
- **Nextcloud**: Select Nextcloud, fill in the server URL + username + app password form, click Connect. No TTY needed.

For Google Drive via CLI (requires `-it` for the OAuth URL prompt):
```bash
# Start the daemon
docker run -d --name cloud-drive-sync \
  -p 8080:8080 \
  -v cloud-drive-sync-config:/root/.config/cloud-drive-sync \
  -v cloud-drive-sync-data:/root/.local/share/cloud-drive-sync \
  -v ~/Documents:/data/Documents \
  ghcr.io/ciberkids/cloud-drive-sync:latest

# Add Google account via CLI (prints auth URL, paste redirect URL back)
docker exec -it cloud-drive-sync \
  python -m cloud_drive_sync account add --provider gdrive --headless

# Verify it worked
docker exec cloud-drive-sync python -m cloud_drive_sync account list
```

After adding accounts, the daemon syncs automatically — no restart needed.

## HTTP Server (Web UI + REST API)

Headless does not mean CLI-only. Started with `--http-port`, the daemon serves the **full web management UI** — the same React interface as the desktop app — plus a REST API, from the daemon process itself. There is no separate web server to run and nothing extra to install: the compiled UI ships inside the daemon package.

| | |
|---|---|
| Web UI | `http://<host>:<port>/` |
| REST API | `http://<host>:<port>/api/*` |

Everything the desktop UI does is available in the browser: adding accounts, creating and editing sync pairs, resolving conflicts, watching transfers, browsing remote folders, and reading the activity log. The HTTP server dispatches to the same request handler as the IPC socket, so the CLI, the desktop app and the browser all drive one daemon with no difference in behaviour.

### Enabling it

`--http-port` defaults to `0`, meaning **disabled**. It is opt-in everywhere except the container images.

**Foreground / manual**

```bash
cloud-drive-sync-daemon start --foreground --http-port 8080
```

**Docker and Quadlet** — already enabled. The image's default command is `start --foreground --http-port 8080`, so you only need to publish the port (`-p 8080:8080`). See [Docker](Docker) and [Quadlet](Quadlet).

**systemd (packaged install)** — **not** enabled. The shipped unit runs `start --foreground` with no HTTP port, so a `.deb` / `.rpm` / AppImage install on a headless server has no web UI until you add the flag:

```bash
systemctl --user edit cloud-drive-sync-daemon
```

```ini
[Service]
ExecStart=
ExecStart=/usr/bin/cloud-drive-sync-daemon start --foreground --http-port 8080
```

The bare `ExecStart=` is required: it clears the unit's original value, and without it systemd refuses to start a service with two `ExecStart` lines. Then reload and restart:

```bash
systemctl --user daemon-reload
systemctl --user restart cloud-drive-sync-daemon
```

Confirm it came up — the daemon logs the bound address on startup:

```
[INFO] HTTP server listening on http://0.0.0.0:8080
```

### Authentication

> ⚠️ **Authentication is off unless you set a token or create an account.** Without either, anyone who can reach the port has full control of the daemon: they can list your files, add or remove cloud accounts, change where data syncs, and **switch off delete protection**.

There are two credentials, and they are layered rather than alternatives:

| | What it is for | How it travels |
|---|---|---|
| **Access token** | Scripts, `curl`, Docker health checks, anything automated | `Authorization: Bearer <token>` on `/api/*` |
| **Web UI account** | A person in a browser | Username and password, then a session cookie |

Both are accepted on `/api/*`. Setting up an account does **not** invalidate your token, so nothing scripted breaks — see [Signing in to the web UI](#signing-in-to-the-web-ui).

### Requiring a token

```bash
cloud-drive-sync gen-token                       # prints a strong random token
cloud-drive-sync start --foreground --http-port 8080 --http-token "$TOKEN"
# in a container:  -e CDS_HTTP_TOKEN=...
```

With a token set:

- `/api/*` requires `Authorization: Bearer <token>`
- the web UI shows a sign-in page, then stores the token in an `HttpOnly`, `SameSite=Strict` cookie
- the MCP endpoint takes its own token via `--mcp-token` / `CDS_MCP_TOKEN`

```bash
curl -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/status
```

### New installs get a token automatically

As of v2.4.3, a **fresh install** generates its own token on first start and prints it:

```
  ┌─ First run: an access token was generated ───────────────
  │
  │    xY3k…                                                
  │
  │  Open http://<host>:8080 and paste it in to sign in.
  │  Stored in your config file under [http] token.
  └──────────────────────────────────────────────────────────
```

In a container that log line is the only copy, so grab it with `docker logs cloud-drive-sync`. It is also written to your config file, so it survives restarts:

```toml
[http]
token = "…"
```

Change it by editing that value, or override it entirely with `--http-token` / `CDS_HTTP_TOKEN`, which take precedence. Delete the value to go back to no authentication.

"Fresh install" means **no config file existed yet**. That is the whole distinction:

| | Behaviour |
|---|---|
| New install | Token generated, stored, printed. Protected by default |
| Existing install (config file present) | Unchanged. No token, no lock-out, and the startup warning still fires |
| `--http-token` or `CDS_HTTP_TOKEN` set | That token is used; nothing is generated or written |
| `--demo` | Nothing generated, because demo mode shares your real config file |

**Upgrades are deliberately left alone.** Turning auth on for a deployment that already exists would lock people out of a web UI they have bookmarked, and the only place the new token would exist is the log of a service they can no longer reach through the UI. So an existing install keeps the previous behaviour, and the daemon logs a prominent warning at startup whenever a port is reachable beyond loopback without a token. If you see that warning, it applies to you — set a token as above.

### Signing in to the web UI

The token works as a sign-in, but it is one shared secret with nobody's name on it. For a browser you can create **one account** with a username and password:

```bash
cloud-drive-sync user set alice        # prompts twice, hidden
cloud-drive-sync user show            # username and dates, never the hash
cloud-drive-sync user clear           # back to token-only
```

In a container the module form is the one that exists — the image ships `cloud-drive-sync-daemon`, not `cloud-drive-sync`:

```bash
docker exec -it cloud-drive-sync python -m cloud_drive_sync user set alice
```

Or create it from the browser: open the UI, choose **Create an account with a username and password**, and paste the access token to prove you are the operator. That is the same token first run printed, and it is why the setup screen is not something a stranger can claim — on a daemon with **no** token there is no browser path at all, and `user set` on the host is the only way in.

Once an account exists:

- the browser asks for the username and password, and holds a session cookie (`HttpOnly`, `SameSite=Lax`, and `Secure` when the connection is HTTPS)
- `Authorization: Bearer <token>` keeps working exactly as before, for everything automated
- the token stops being accepted *as a browser cookie* — otherwise it would be a second way in that bypasses the account entirely

**Deliberately one account, with no roles.** A single-owner sync daemon gains nothing from a user list it would only ever put one row in. If you need per-person access, put a reverse proxy with its own authentication in front.

**Sessions live in memory, so restarting the daemon signs you out.** That is the accepted price of not keeping session state on disk; an upgrade or a container restart means signing in again.

**Forgot the password?** Run `cloud-drive-sync user set <name>` on the host — access to the machine is the recovery path, exactly as it is for every other daemon setting. There is no email reset, because there is no mail transport and no second factor, so a reset flow would just be a bypass.

Failed sign-ins are throttled with a growing delay rather than a lockout: with one account, a lockout is an outage anyone who knows the username could trigger.

The account is a row in the **database** (`state.db`), not in `config.toml`. If you delete the database — a supported repair for a corrupt sync state — the account goes with it, and the daemon falls back to the token that is still in your config file, so you can sign in and create it again.

### Restricting the bind address

`--http-host` (default `0.0.0.0`, which containers need) controls who can connect at all:

```bash
cloud-drive-sync start --foreground --http-port 8080 --http-host 127.0.0.1
```

Bound to loopback only this machine can reach it, and running without a token there is unremarkable — the daemon says so rather than warning.

### Remaining specifics

- **CORS is `Access-Control-Allow-Origin: *`,** and we never send `Access-Control-Allow-Credentials`. Browsers refuse to combine credentials with a wildcard origin, so a page on another origin can neither read the token nor ride on the session cookie. Without any credential configured, any web page in a browser that can reach the port can drive the API.
- **Cross-site request forgery is refused three ways** once a session cookie is involved: the cookie is `SameSite=Lax` (so it is not sent on a cross-site `POST`/`PUT`/`DELETE`), a mutating `/api/*` call authenticated by cookie must not be form-encoded (an HTML form cannot send JSON), and its `Origin` must match. None of this applies to bearer-token callers, so `curl` scripts that send no explicit content type keep working.
- **The account has no roles and there is only one of it.** No per-user auditing. That is the right shape for a single-owner daemon and the wrong shape for multi-tenant access — put a reverse proxy in front for that.
- **Passwords over plain HTTP are sniffable,** exactly as the token always was. On an untrusted network, terminate TLS in front of the daemon and set `[http] trust_proxy = true` so the session cookie is marked `Secure`.

Recommended deployments:

| Situation | Approach |
|---|---|
| Single machine, local use | Docker: publish as `-p 127.0.0.1:8080:8080` so only that host can connect |
| LAN access from a phone or laptop | Create an account (`cloud-drive-sync user set <name>`) so the credential is not a token you paste on a small screen |
| Remote server, occasional admin | Leave the port unpublished and use an SSH tunnel: `ssh -L 8080:localhost:8080 user@server`, then open `http://localhost:8080` |
| Permanent remote access | Reverse proxy (nginx, Caddy, Traefik) terminating TLS and enforcing authentication in front of it |
| Any | Firewall the port; never forward it from a router to the internet |

Treat a reachable port as equivalent to filesystem access to everything the daemon syncs.



### REST API reference

**Status**

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/status` | Daemon and sync status |

**Accounts**

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/accounts` | List all accounts |
| POST | `/api/accounts` | Add a new account (OAuth or Nextcloud credentials) |
| POST | `/api/accounts/auth-code` | Exchange OAuth authorization code |
| GET | `/api/accounts/oauth-callback` | OAuth redirect callback handler |
| DELETE | `/api/accounts/{email}` | Remove an account |
| PUT | `/api/accounts/{email}/max-transfers` | Set per-account max concurrent transfers |

**Sync Pairs**

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/pairs` | List sync pairs |
| POST | `/api/pairs` | Add a sync pair (`local_path`, `remote_folder_id`, `provider`, `account_id`, `sync_mode`) |
| DELETE | `/api/pairs/{pair_id}` | Remove a sync pair |
| PUT | `/api/pairs/{pair_id}/mode` | Set sync mode (`two_way` / `upload_only` / `download_only`) |
| PUT | `/api/pairs/{pair_id}/ignore-hidden` | Toggle dotfile exclusion |
| PUT | `/api/pairs/{pair_id}/ignore-patterns` | Set glob ignore patterns |
| GET | `/api/pairs/{pair_id}/rules` | Get advanced sync rules (size limit, regex filters) |
| PUT | `/api/pairs/{pair_id}/rules` | Set advanced sync rules |

**Sync Control**

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/sync` | Trigger an immediate sync (optional `pair_id`) |
| POST | `/api/sync/pause` | Pause sync (optional `pair_id`) |
| POST | `/api/sync/resume` | Resume sync (optional `pair_id`) |

**Conflicts & Activity**

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/conflicts` | List unresolved conflicts |
| POST | `/api/conflicts/{conflict_id}/resolve` | Resolve a conflict |
| GET | `/api/activity` | Recent sync activity log (`?limit=N&offset=N`) |

**Settings**

| Method | Path | Description |
|--------|------|-------------|
| GET/PUT | `/api/settings/notifications` | Notification preferences |
| GET/PUT | `/api/settings/bandwidth` | Upload/download bandwidth limits (kbps, 0 = unlimited) |
| GET/PUT | `/api/settings/proxy` | HTTP/HTTPS proxy settings |
| PUT | `/api/settings/conflict-strategy` | Conflict strategy (`keep_both` / `newest_wins` / `ask_user`) |

**Webhooks**

The `scope` query parameter selects the level: `global` (the default) or `pair:<uid>`.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/webhooks?scope=S` | Stored config for a level, with secrets masked |
| PUT | `/api/webhooks?scope=S` | Replace a level's config. Requires a token to be configured |
| GET | `/api/webhooks/resolved?scope=S` | The callbacks that will actually fire, after merging |
| GET | `/api/webhooks/status` | Delivery health per target |
| POST | `/api/webhooks/test?scope=S` | Send a `webhook.test` event. Requires a token to be configured |

**File Browser (headless)**

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/remote-folders` | List remote folders (`?parent_id=&account_id=`) |
| POST | `/api/remote-folders` | Create a remote folder |
| GET | `/api/local-dirs` | List local directories (`?path=`) |
| POST | `/api/local-dirs` | Create a local directory |

#### Sign-in endpoints

Five endpoints, and no user management, because there is one account. They are the only `/api/*` paths reachable without a credential — they have to be, or there would be no way to obtain one.

| Method | Path | Body | Purpose |
|---|---|---|---|
| `GET` | `/api/auth/session` | — | What the sign-in screen should render: `{"auth":"none"\|"token"\|"user", "setup_available":bool, "authenticated":bool, "username":str\|null}` |
| `POST` | `/api/auth/token` | `{token}` | Exchange the access token for a cookie. Refused once an account exists |
| `POST` | `/api/auth/setup` | `{token, username, password}` | Create the account. Needs the access token |
| `POST` | `/api/auth/login` | `{username, password}` | Sign in. Answers one `401 invalid_credentials` for a wrong username *or* a wrong password — no enumeration |
| `POST` | `/api/auth/logout` | — | Drop the presented session |
| `POST` | `/api/auth/password` | `{current, new}` | Change the password. Signs out every other browser |

```bash
# Sign in and keep the cookie, the way the browser does
curl -c jar -X POST -H 'Content-Type: application/json' \
  -d '{"username":"alice","password":"…"}' http://localhost:8080/api/auth/login
curl -b jar http://localhost:8080/api/status
```

### Adding accounts via Web UI

When you click **Add Account** in the web UI, the daemon runs the headless auth flow in the background. Since the auth prompts appear in the daemon's stdout (not in the browser), follow these steps:

1. Click **Add Account** in the web UI — the button shows "Authenticating..."
2. In another terminal, check the daemon logs for the authorization URL:
   ```bash
   # Docker
   docker logs -f cloud-drive-sync

   # Docker Compose
   docker compose logs -f daemon

   # Local
   # The URL prints directly in the terminal running the daemon
   ```
3. Open the authorization URL in your browser and complete sign-in
4. The web UI updates automatically when auth completes

### Example curl commands

```bash
# Check status
curl http://localhost:8080/api/status

# List accounts
curl http://localhost:8080/api/accounts

# Add account (headless)
curl -X POST http://localhost:8080/api/accounts \
  -H "Content-Type: application/json" \
  -d '{"provider": "gdrive", "headless": true}'

# List sync pairs
curl http://localhost:8080/api/pairs

# Trigger a sync
curl -X POST http://localhost:8080/api/sync

# Get recent activity
curl 'http://localhost:8080/api/activity?limit=20'

# Set bandwidth limits
curl -X PUT http://localhost:8080/api/settings/bandwidth \
  -H "Content-Type: application/json" \
  -d '{"max_upload_kbps": 1000, "max_download_kbps": 2000}'
```

## MCP Server (for AI assistants)

The daemon can expose its capabilities over the [Model Context Protocol](https://modelcontextprotocol.io), so an AI assistant — Claude Desktop, Claude Code, or any MCP client — can inspect and manage sync directly instead of you relaying CLI output to it.

It is a third front-end onto the same request handler the CLI and web UI use, so an assistant cannot reach behaviour those two do not already have.

| | |
|---|---|
| Endpoint | `http://<host>:<port>/mcp` |
| Transport | Streamable HTTP |
| Default | **Disabled**, containers included |

### Enabling it

```bash
cloud-drive-sync-daemon start --foreground --mcp-port 8081
```

In Docker or Quadlet, set the environment variable and publish the port — the image already has the dependency installed:

```bash
docker run -d --name cloud-drive-sync \
  -p 8080:8080 -p 127.0.0.1:8081:8081 \
  -e CDS_MCP_PORT=8081 \
  -e CDS_MCP_ALLOWED_HOSTS='*' \
  ghcr.io/ciberkids/cloud-drive-sync:latest
```

| Flag | Environment variable | Default | Description |
|---|---|---|---|
| `--mcp-port` | `CDS_MCP_PORT` | `0` | Port to serve MCP on. `0` disables it. |
| `--mcp-host` | `CDS_MCP_HOST` | `0.0.0.0` | Bind address. Use `127.0.0.1` to restrict to this machine. |
| `--mcp-allow-writes` | `CDS_MCP_ALLOW_WRITES` | off | Also expose tools that change state. |
| `--mcp-allowed-host` | `CDS_MCP_ALLOWED_HOSTS` | localhost only | `Host` header to accept, e.g. `nas.local:*`. Repeatable. `*` accepts any. |

When setting several hosts through the environment variable, **separate them with spaces, not commas** — `CDS_MCP_ALLOWED_HOSTS='a.local:* b.local:*'`. A comma-separated value is read as one malformed host, which then matches nothing and rejects every request.

If `--mcp-port` is set but the optional dependency is missing, the daemon logs an error and carries on without MCP rather than failing to start. Install it with `pip install 'cloud-drive-sync[mcp]'`.

The `mcp` extra requires SDK **2.x**. The dependency is bounded to one major version on purpose: 2.0 replaced the handler API outright, and an unbounded requirement let CI resolve a breaking major with no code change on our side.

### Connecting an assistant

```json
{
  "mcpServers": {
    "cloud-drive-sync": {
      "type": "http",
      "url": "http://localhost:8081/mcp"
    }
  }
}
```

In Claude Code: `claude mcp add --transport http cloud-drive-sync http://localhost:8081/mcp`

### Tools

**Read-only — always available**

| Tool | Purpose |
|---|---|
| `get_status` | Daemon state, uptime, per-pair synced/pending/error counts |
| `list_sync_pairs` | Configured pairs with paths, provider, mode, strategy |
| `list_accounts` | Connected accounts (never tokens or credentials) |
| `get_activity_log` | Recent activity; `filter="error"` to investigate failures |
| `list_conflicts` | Unresolved conflicts, with ids for `resolve_conflict` |
| `get_sync_rules` | Include/exclude rules for a pair |
| `get_bandwidth_limits` | Current throttles |
| `get_file_status` | Why one specific file has or hasn't synced |
| `list_remote_folders` | Browse cloud folders when choosing a target |

**State-changing — only with `--mcp-allow-writes`**

`force_sync`, `pause_sync`, `resume_sync`, `resolve_conflict`, `add_sync_pair`, `remove_sync_pair`, `set_sync_mode`, `set_conflict_strategy`, `set_pair_conflict_strategy`, `set_ignore_hidden`, `set_ignore_patterns`, `set_bandwidth_limits`, `set_sync_rules`, `create_remote_folder`, `set_account_max_transfers`, `repair`, `add_account`, `remove_account`

Without the flag these are not advertised at all, rather than offered and refused — an assistant that sees a tool will try to use it. None of them delete files: removing a pair or an account only changes configuration and credentials, and `repair` only touches database records.

**Never exposed at any level**

`shutdown` (an assistant stopping the daemon is never the intent), `start_auth` / `exchange_auth_code` (interactive OAuth, and the code is a secret), `get_proxy` / `set_proxy` (proxy URLs can embed credentials), `list_local_dirs` / `mkdir_local` (host filesystem access beyond synced state).

### MCP Authentication

> ⚠️ **The MCP endpoint is unauthenticated unless you set `--mcp-token`.** Without one, anyone who can reach the port can use every enabled tool.

Set a token with `--mcp-token` / `CDS_MCP_TOKEN`; clients then send `Authorization: Bearer <token>`. It is separate from the HTTP token so an assistant can be given access without handing over the web UI credential.

It is safer by default than `--http-port` in two respects, and you should keep it that way:

- **Read-only unless you opt in.** Without `--mcp-allow-writes` nothing can be changed, so the worst case is disclosure of sync metadata rather than someone repointing your data.
- **Loopback-only `Host` checking.** DNS-rebinding protection is on with only `localhost`, `127.0.0.1` and `[::1]` accepted, so a web page in your browser cannot drive the endpoint. Reaching it from another machine means naming that host with `--mcp-allowed-host nas.local:*`, or disabling the check with `*`.

Recommended: publish it to loopback (`-p 127.0.0.1:8081:8081`) and reach it over an SSH tunnel rather than exposing the port. Enable writes only when you actually want an assistant changing configuration, and prefer a separate read-only endpoint for monitoring agents.

## Docker Deployment

The daemon runs headless in Docker with no GUI dependencies.

### Quick Start

```bash
docker run -d --name cloud-drive-sync \
  -p 8080:8080 \
  -e PUID=$(id -u) -e PGID=$(id -g) \
  -v cloud-drive-sync-config:/root/.config/cloud-drive-sync \
  -v cloud-drive-sync-data:/root/.local/share/cloud-drive-sync \
  -v ~/Documents:/data/Documents \
  ghcr.io/ciberkids/cloud-drive-sync:latest

# Open http://localhost:8080/ for the web management UI

# Add account (interactive — prints auth URL)
docker exec -it cloud-drive-sync \
  python -m cloud_drive_sync account add --provider gdrive --headless

# Check status
docker exec cloud-drive-sync python -m cloud_drive_sync status
```

### File Ownership (PUID / PGID)

By default the daemon runs as root inside the container, which causes synced files
in bind-mounted folders to be owned by root on the host.  Set `PUID` and `PGID` to
your host user's numeric IDs to have the daemon run as that user and write files
with the correct ownership:

```bash
docker run -d \
  -e PUID=$(id -u) \
  -e PGID=$(id -g) \
  ...
```

Or in `docker-compose.yml`:

```yaml
environment:
  - PUID=1000   # host user ID (id -u)
  - PGID=1000   # host group ID (id -g)
```

Config and data volumes (`/root/.config/cloud-drive-sync` and
`/root/.local/share/cloud-drive-sync`) are automatically chowned to PUID:PGID on
startup so no manual volume permission changes are needed.  Omitting `PUID`/`PGID`
(or setting `PUID=0`) preserves the legacy root behaviour.

### HTTP REST API

Enabled by default in the container images on port 8080 — publish it with `-p 8080:8080` and open `http://localhost:8080/`.

See [HTTP Server (Web UI + REST API)](#http-server-web-ui--rest-api) above for the endpoint reference and the security notes that apply when the port is reachable from other machines.

### Docker Compose

See `docker/docker-compose.yml` for a ready-to-use compose file.

### Volumes

| Mount | Purpose |
|-------|---------|
| `/root/.config/cloud-drive-sync` | Config (config.toml) |
| `/root/.local/share/cloud-drive-sync` | Credentials, database |
| `/run/cloud-drive-sync` | IPC socket (for CLI from host) |
| `/data/*` | Sync folder mount points |

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `XDG_RUNTIME_DIR` | `/run/cloud-drive-sync` | IPC socket directory |
| `CDS_GOOGLE_CLIENT_ID` | (embedded) | Override Google OAuth client ID |
| `CDS_GOOGLE_CLIENT_SECRET` | (embedded) | Override Google OAuth client secret |

## Emergency Stop

`pause` stops *starting* new work and lets in-flight transfers finish. When something is actively going wrong — the wrong folder is syncing, deletions are propagating, a provider is misbehaving — that is not what you want. **Stop activity** cancels work already in progress.

Two scopes:

| Scope | UI | CLI | REST |
|---|---|---|---|
| Everything | **Stop activity** in the sidebar | `cloud-drive-sync stop-activity` | `POST /api/sync/stop` |
| One account | per-account control | `cloud-drive-sync stop-activity --account you@example.com` | `POST /api/sync/stop` with `{"account_id": "..."}` |

Resume with the same control, `resume-activity`, or `POST /api/sync/resume-stopped`. Current state: `GET /api/sync/stop-state`.

The stop is **persisted**. A daemon that starts with a stop in force starts halted and logs why — otherwise a container restart policy would quietly undo the thing you did in an emergency.

A per-account resume cannot override an application-wide stop; lift the global one too, or the control appears to work while nothing moves.

### What "immediately" actually guarantees

The limit here is real, so it is worth being precise:

- **Stops at once** — everything queued, every awaiting operation, the directory watchers, and all subsequent passes. This is the overwhelming majority of pending work.
- **May take a moment** — a provider SDK call already executing inside a worker thread. Python cannot cancel a thread, so an upload already handed to the Box or Dropbox SDK runs until it returns. Its result is then discarded.

At most one transfer per concurrent worker (`max_concurrent_transfers`, default 4) can therefore still be writing briefly after you press stop. Nothing queued behind them starts. Partial uploads are recorded in the resumable-transfer table and are cleaned up or resumed on the next pass rather than being mistaken for real files.

If you need the byte flow to stop with certainty — not merely the daemon's participation in it — stop the daemon process.

## Nextcloud Change Detection

WebDAV has no delta API, so detecting remote changes on Nextcloud means walking the tree and comparing ETags — **one PROPFIND per directory, every poll interval**. On a large tree that is expensive for the server, and it is why [#44](https://github.com/ciberkids/cloud-drive-sync/issues/44), [#47](https://github.com/ciberkids/cloud-drive-sync/issues/47) and [#50](https://github.com/ciberkids/cloud-drive-sync/issues/50) were damaging rather than merely wasteful: each was a per-property cost multiplied across every directory, repeated every 30 seconds.

If your server has the [`notify_push`](https://github.com/nextcloud/notify_push) app — the same one the official Nextcloud desktop client uses — the daemon uses it instead, and most of those requests disappear.

**No configuration is needed.** On startup the daemon asks the server whether it advertises `notify_push`; if so it opens a WebSocket and subscribes to file-ID notifications, and if not it keeps polling. The Status dashboard shows which mechanism each pair is using — **⚡ push** or **↻ polling**.

### What changes when push is active

| | Polling | Push |
|---|---|---|
| Cost of an idle poll | one PROPFIND per directory | nothing — no request at all |
| Cost of a change | full tree walk | one lookup per changed file |
| Full walk frequency | every `poll_interval` (30s) | every 15 minutes, for reconciliation |

Push reports the specific `oc:fileid` values that changed, which is the identifier the sync database is already keyed on — so a notification maps directly onto known state with no translation.

### Polling never goes away

`notify_push` is explicitly best-effort. Upstream states that "updates might happen without a notification being sent and a notification can be sent even if no update has actually happened." So the ETag walk is retained as a **reconciliation pass every 15 minutes**, which covers any notification that was never sent.

The daemon also falls back to a full walk when:

- the server sends the coarse `notify_file` event, meaning it could not determine which files changed
- a notified file ID no longer resolves — that is a deletion, and the ID alone does not give the path, so the walk derives it by diffing
- the WebSocket drops; after 5 consecutive failures it stops retrying and polls for the rest of the session

The first poll after a restart always reconciles, since notifications sent while the daemon was down were missed.

### Forcing polling

If push behaves badly on your instance, disable it per pair:

```toml
[[sync.pairs]]
local_path = "/home/you/Documents"
provider = "nextcloud"
force_polling = true
```

This skips the capabilities request entirely, so it is also a way to avoid that request on a fragile instance.

### How the WebSocket authenticates

The push protocol authenticates by sending credentials over the socket itself. Sending your app password there would mean transmitting a long-lived credential on every connect and every reconnect, so the daemon avoids it where it can:

1. If the server advertises a `pre_auth` endpoint, the daemon exchanges the app password for a **short-lived, single-use token** over HTTPS and presents that instead. The app password never crosses the WebSocket.
2. If it does not — older versions of the app — the daemon falls back to sending the app password, since that is the only credential the socket accepts.

A fresh token is fetched for **every** connection attempt, because tokens are single-use: reusing one is refused, so a cached token would authenticate the first connection and then fail every reconnect after it. That failure mode is invisible in normal operation — a refused push connection just falls back to polling — which is why it is [asserted against a real server](https://github.com/ciberkids/cloud-drive-sync/blob/main/daemon/tests/test_integration_notify_push.py) rather than only against fakes.

Either way, use an **app password** rather than your account password (Settings → Security → Devices & sessions), so the credential can be revoked on its own.

### Server requirements

`notify_push` needs Redis, a push daemon process and ideally a reverse proxy — see its [README](https://github.com/nextcloud/notify_push). Many instances do not have it, which is why detection is automatic rather than assumed. Nothing breaks without it; the daemon simply keeps polling.

## Large File Uploads

Files above a per-provider threshold are uploaded in chunks over an upload session rather than in one request, so a failure costs one chunk instead of the whole transfer.

| Provider | Single request up to | Chunk size |
|---|---|---|
| Dropbox | 150 MB | 8 MB |
| Box | 50 MB | Chosen by the server per session |
| OneDrive | 4 MB | 10 MB |
| Google Drive | — | Resumable upload, chunked by the SDK |

Chunks are read with async file I/O. That is not a detail: a 10 MB blocking read on the event loop would stall **every other sync pair** once per chunk, so a single large upload used to make an otherwise idle daemon look frozen.

### If a file changes while it is being uploaded

The size is measured once, before the first chunk. If the file is **truncated** after that — a download still in flight, a log rotated, an application rewriting in place — the remaining bytes never arrive, and the upload fails with:

```
/path/to/file shrank during upload: expected 524288000 bytes but the file ended at 104857600
```

This is a normal, retryable failure. The transfer is retried up to three times, re-measuring the file each time, so a file that has settled at its new size uploads on the next attempt.

If the file **grows** instead, the upload sends the size it measured and stops there. The cloud copy is a prefix of the local file, and the next scan sees the newer modification time and uploads again.

## Webhooks

Outbound HTTP callbacks: when something happens, the daemon POSTs a JSON event to an
endpoint you choose. Configured in `config.toml` — see
[`[webhooks]`](Configuration#webhooks) for every key — at two levels, global and per
pair, which merge.

The motivating case is a headless install. A refused mass deletion pauses a pair and
waits for a human; a conflict waits for a decision; an expired credential stops an
account syncing. On a NAS or in a container, nobody is looking at the UI, and all of
that currently surfaces only if someone runs `activity`.

### What a payload looks like

One event per POST, `Content-Type: application/json`:

```json
{
  "schema_version": 1,
  "event": "sync.completed",
  "event_id": "9f2b0c14-7e83-4a51-b6d2-1c8f4e5a7b30",
  "occurred_at": "2026-08-20T12:34:56.789Z",
  "source": { "app": "cloud-drive-sync", "version": "2.4.5", "instance_id": "b71e4f9a-..." },
  "scope": {
    "pair_id": "3f7a1c68-2d4e-4f0b-9a11-8c5e6b0d2a94",
    "pair_label": "/home/me/Documents",
    "account": { "provider": "gdrive", "email": "me@example.com" },
    "local_path": "/home/me/Documents",
    "remote_folder_id": "0B9aXqZ1kLmNoPqRs"
  },
  "delivery": { "target": "ops-bus", "target_key": "global|ops-bus", "attempt": 1, "sent_at": "..." },
  "data": { "uploaded": 12, "downloaded": 3, "deleted": 0, "errors": 0, "files": { "...": [] } }
}
```

Three things a receiver should rely on:

- **`event_id` is stable across retries.** Delivery is at-least-once, so this is your
  deduplication key. The same event redelivered carries the same id; only
  `delivery.attempt` changes.
- **`scope.pair_id` is stable.** It is the pair's `uid`, not its position, so removing
  another pair will not silently re-point it at a different folder.
- **Ignore fields you do not know.** New fields are added without bumping
  `schema_version`; only a breaking change bumps it.

Order events on `occurred_at` rather than on arrival: a retried event can land after a
later one.

### Reliability, and what it does not promise

A webhook never slows down a sync. Events are queued and delivered by a background
worker per target, so a hung endpoint costs nothing on the sync path.

- Retries on connection errors, timeouts, `408`, `429` and `5xx`, with exponential
  backoff and jitter, honouring `Retry-After`. Other `4xx` responses are **not**
  retried — a `401` is a configuration error, and retrying it per event would turn a
  typo into a flood.
- Redirects are never followed.
- After 10 consecutive failures a target is marked unhealthy and drops to one attempt
  every five minutes. `webhook status` shows this.
- Each target has a bounded queue (1000 events). On overflow the oldest are dropped and
  counted. `deletion.blocked`, `sync.failed` and `account.auth_failed` use a separate
  lane so a busy stream cannot evict an alert.
- **The queue is in memory.** Events still queued when the daemon stops are lost. For
  the case where that matters most this is less bad than it sounds: a refused deletion
  is persisted independently and still waiting in `deletions list` after a restart, so
  the information survives even when the notification did not.

### Checking it works

```bash
# What will actually fire for a folder, after merging global and per-pair config
cloud-drive-sync webhook list --scope pair:3f7a1c68-2d4e-4f0b-9a11-8c5e6b0d2a94

# Send a test event
cloud-drive-sync webhook test --name ops-bus

# Delivery counters, queue depth and breaker state
cloud-drive-sync webhook status
```

`webhook list` reports configuration problems as well as targets — a callback that
inherited no URL, an `auth` block with no `mode`, or a new name missing `define = true`.
Those are logged at startup too.

### Security notes

- **Editing webhook config requires authentication to be enabled.** This is stricter
  than the other endpoints on purpose. Everything they can do moves your data between
  your own accounts; a webhook sends your file activity to a host the caller names, and
  makes the daemon issue HTTP requests inside your network. On an install with no token
  set, `/api/*` is reachable without credentials — so webhook writes are refused there
  rather than inheriting that. Run `cloud-drive-sync gen-token` first.
- Secrets are never returned by a read, logged, or written to the activity log. Use the
  `_env` form (`token_env = "..."`) so they never enter `config.toml` at all.
- URLs are only ever logged as scheme and host, because a path or query string can
  itself be the credential.
- TLS verification is on by default. Turning it off logs a warning naming the target.
- Private and LAN addresses are allowed by default, because posting to something on
  your own network is the main use case. Set
  `webhooks.allow_private_addresses = false` globally to refuse them on a shared box.

> **Not the same as provider push notifications.** Google Drive push and Nextcloud
> `notify_push` are *inbound* — the cloud telling the daemon something changed. These
> webhooks are *outbound*. See [Nextcloud Change Detection](#nextcloud-change-detection)
> for the inbound kind.

## Delete Protection

Sync is two-way, so deleting files locally deletes them in the cloud too. That is the intended behaviour right up until the deletion was not intended — a bad `rm -rf`, an external drive unmounted while its mountpoint is still a sync path, a disk failure, or a container recreated with an empty volume. The daemon would see thousands of deletions as user intent and faithfully empty the cloud copy, turning the backup into a mirror of the disaster.

The delete fail-safe refuses batches that look like that.

| Setting | Default | Meaning |
|---|---|---|
| `max_deletions_per_sync` (`[sync]`) | `100` | Cap per direction, counted over the window below |
| `deletion_window_seconds` (`[sync]`) | `60` | Sliding window the cap applies to. `0` = per sync pass only |
| `max_deletions_per_sync` (`[[sync.pairs]]`) | inherit | Per-pair override; `0` disables the guard for that pair |
| `deletion_window_seconds` (`[[sync.pairs]]`) | inherit | Per-pair window override |

All four are editable in **Settings → Delete Protection** (global) and under **Advanced Rules** on each pair. There is no enforced minimum — set the cap to `2` if you want a third deletion inside the window to require confirmation.

### Three triggers, whichever fires first

**1. Count over a time window.** The cap applies to the proposed deletions *plus* those already performed in the last `deletion_window_seconds`. A per-pass cap alone is defeated by a slow drip: 99 deletions per pass never trips a limit of 100, but repeated it still empties the library. Counting the window closes that — a mass delete breaches on its first pass, a drip on its Nth.

The window is counted from the activity log, not from an in-memory counter, so it **survives a restart**. An in-memory count would reset on restart, and a crash-loop would hand a fresh allowance every cycle. Only deletions that actually succeeded count; failed attempts do not consume the allowance, and each pair has its own.

**2. Share of tracked files.** A pass is also refused when deletions exceed **50%** of the files tracked for that pair. An absolute count catches a large library; the ratio catches a small one, where 90 deletions is under any sensible count but is nearly everything the user has. Batches under 10 files are never gated by ratio alone.

**3. Direction.** Local and remote are counted separately throughout — a wiped *remote* emptying the local copy is the same threat mirrored, and download-only pairs make it reachable.

### What happens on a breach

1. **Nothing is deleted.** The whole batch is refused, not trimmed.
2. The pair is **paused**, so the next poll does not retry it.
3. The refusal is **written to the database**, so restarting the daemon does not resolve it — otherwise a container restart policy would quietly undo the hold.
4. It appears in the activity log as `delete_blocked`, and the UI shows a banner with the counts and a sample of paths.

Local and remote deletions are counted separately: a wiped *remote* must not be able to empty the local copy either, and download-only pairs make that reachable.

### Resolving a block

In the web UI, the banner offers **Delete them** or **Keep files**. From the command line:

```bash
# What is blocked, with sample paths
cloud-drive-sync deletions list

# Allow them — prompts for confirmation first
cloud-drive-sync deletions approve 0

# Refuse them; the pair stays paused
cloud-drive-sync deletions reject 0
```

Via the API:

```bash
# What is blocked?
curl -s http://localhost:8080/api/pending-deletions | jq

# Approve — the next pass performs the deletions
curl -X POST http://localhost:8080/api/pending-deletions/0/resolve \
  -H 'Content-Type: application/json' -d '{"approve": true}'

# Reject — the pair stays paused and nothing is deleted
curl -X POST http://localhost:8080/api/pending-deletions/0/resolve \
  -H 'Content-Type: application/json' -d '{"approve": false}'
```

Approval is **one-shot**: it lets the next pass through and is then consumed. Approving today's mass delete is not consent for every future one. Approving also does not replay the stored batch — the next pass re-plans, so if you restored the files in the meantime, nothing is deleted.

Changing the limit:

```bash
curl -X PUT http://localhost:8080/api/settings/max-deletions \
  -H 'Content-Type: application/json' -d '{"max_deletions_per_sync": 250}'

# Per pair; 0 disables the guard for it
curl -X PUT http://localhost:8080/api/settings/max-deletions \
  -H 'Content-Type: application/json' -d '{"max_deletions_per_sync": 0, "pair_id": "0"}'
```

> Setting `0` disables delete protection. A wiped local folder will then empty the cloud copy with no prompt.

An AI assistant over MCP can *see* blocked batches (`list_pending_deletions`) but cannot approve them — the guard exists to put a human in the loop, so `resolve_pending_deletions` is not exposed as a tool at any permission level.

## Logging and Disk Usage

The daemon writes to `<data-dir>/cloud-drive-sync.log` and to stderr. Both are bounded, so neither can grow without limit:

| Limit | Value | Notes |
|---|---|---|
| Log file size | 10 MB | Rotated in place, oldest discarded |
| Rotated copies kept | 5 | Total log footprint therefore caps at ~60 MB |
| Maximum message length | 2000 characters | Longer messages are truncated with the original length appended |

Message truncation matters because provider exceptions embed the full failed request in their text. Without a cap, a single rejected WebDAV request could write a ~440 KB line, and repeated retries wrote it again each time. The diagnostic part of a message — action, path, status code — is always at the front, so truncation never removes it. Tracebacks are not truncated.

Rotated files are named `cloud-drive-sync.log.1` through `.5`. To keep long-term history, ship the log elsewhere (journald, a log collector, or a `logrotate` rule on a copy) rather than relying on the daemon's own files.

### Database maintenance

The sync state database at `<data-dir>/state.db` maintains itself, so it does not need manual `VACUUM`ing.

| Behaviour | Value |
|---|---|
| Activity log retention | 30 days |
| Maintenance interval | 6 hours |
| Startup reclaim threshold | 25% of the file free **and** at least 64 MB reclaimable |

Two things run automatically:

- **Every 6 hours**, activity-log rows older than 30 days are deleted and the freed pages are returned to the filesystem. Pruning is bounded per run, so the first pass on a large history is spread over several runs rather than blocking. Activity older than 30 days will therefore disappear from the **Activity** view — export it first if you need to keep it.
- **At startup**, if the file is mostly wasted space, it is rewritten to reclaim it. SQLite does not shrink a database when rows are deleted: freed pages go on an internal free list and get reused, so a long-running daemon's file only ever grows. Databases created before this behaviour existed could reach several GB while holding no rows at all; the first start after upgrading reclaims that and logs the before/after size.

The startup reclaim is deliberately rare because it rewrites the whole file and delays startup while it runs. New databases are created with incremental auto-vacuum enabled, so they give space back continuously and should never reach the threshold.

**Checking it yourself.** The status payload reports the database size and how much of it is reclaimable free space, so bloat is visible before it reaches GB scale:

```bash
curl -s http://localhost:8080/api/status | jq '.daemon.database'
```

```json
{
  "size_formatted": "312.0 KB",
  "reclaimable_formatted": "0 B",
  "reclaimable_ratio": 0.0,
  "page_count": 83,
  "freelist_count": 0
}
```

`reclaimable_ratio` is the number to watch — it distinguishes a file that is large because it holds data from one that is large because it is mostly dead space. The **Status** dashboard shows the size, and calls out the reclaimable amount once it passes the same thresholds that trigger the startup reclaim.

## Stub Repair

Stubs are incomplete sync-state entries that accumulate when a transfer is interrupted or the sync database is reset while remote files still exist. They show up as files tracked in the database that are missing on one side, causing repeated, fruitless sync attempts.

### Scanning for stubs

In the web UI, open **Settings**, expand a sync pair, and click **Scan for stubs**. The daemon checks the database against the live local and remote file lists and reports how many stale entries it found.

Via the REST API:

```bash
# Dry-run scan (no changes)
curl -X POST 'http://localhost:8080/api/pairs/0/repair?dry_run=true'

# Apply the repair (deletes stale DB entries)
curl -X POST 'http://localhost:8080/api/pairs/0/repair'
```

### When to use it

- After a container restart or database migration where the DB was re-created
- If the activity log shows repeated errors for the same file that does not exist locally
- After manually deleting files outside of the sync process
- After an `rclone bisync --resync` or equivalent baseline reset on the remote side

Stub repair only removes database records — it never deletes files from local storage or from the cloud.

## Development

### Setup

```bash
cd daemon
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Run in Development

```bash
# With real Drive API
python -m cloud_drive_sync --log-level debug start --foreground

# With demo mode
python -m cloud_drive_sync start --foreground --demo
```

### Run Tests

```bash
pytest -v
pytest --cov=cloud_drive_sync  # With coverage
```

### Lint

```bash
ruff check src/ tests/
```

## Architecture

The daemon is structured as a set of asyncio components:

```
Daemon
  ├── Config (TOML loader)
  ├── Database (async SQLite via aiosqlite)
  ├── DriveClient (Google API v3 wrapper)
  ├── SyncEngine
  │     ├── DirectoryWatcher (per pair, watchdog-based)
  │     ├── ChangePoller (per pair, Drive Changes API)
  │     ├── SyncPlanner (diff + action planning)
  │     ├── SyncExecutor (concurrent transfer runner)
  │     └── ConflictResolver (strategy dispatch)
  └── IpcServer (Unix socket, JSON-RPC 2.0)
        └── RequestHandler (method dispatch)
```

For full architectural details, see the [[Architecture]] page.

## Dependencies

| Package | Version | Purpose |
|---|---|---|
| google-api-python-client | >=2.100.0 | Google Drive API v3 |
| google-auth-oauthlib | >=1.1.0 | OAuth2 flow |
| google-auth-httplib2 | >=0.2.0 | HTTP transport for Google auth |
| watchdog | >=4.0.0 | Filesystem event monitoring |
| aiosqlite | >=0.19.0 | Async SQLite |
| aiofiles | >=23.2.0 | Async file I/O |
| tomli-w | >=1.0.0 | TOML writing |
| click | >=8.1.0 | CLI framework |
| cryptography | >=41.0.0 | Credential encryption |

### Dev Dependencies

| Package | Version | Purpose |
|---|---|---|
| pytest | >=7.4.0 | Test framework |
| pytest-asyncio | >=0.21.0 | Async test support |
| pytest-cov | >=4.1.0 | Coverage reporting |
| ruff | >=0.1.0 | Linter and formatter |
