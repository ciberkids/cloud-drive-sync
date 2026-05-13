# Provider Setup

Complete OAuth and credentials setup guide for every cloud provider supported by Cloud Drive Sync.

## Supported Providers

| Provider | Auth Method | Setup Required |
|---|---|---|
| Google Drive | OAuth 2.0 (browser) | None — credentials embedded |
| Nextcloud | App password | Generate in Nextcloud settings |
| Dropbox | OAuth 2.0 PKCE | Create free app in Dropbox console |
| OneDrive | Azure AD device code | Register app in Azure Portal |
| Box | OAuth 2.0 | Register app in Box Developer Console |

---

## Google Drive

Google Drive works out of the box. OAuth 2.0 credentials are embedded in the application — no developer account or app registration required.

### Desktop (with browser)

```bash
cloud-drive-sync account add --provider gdrive
```

This opens your default browser automatically. Sign in with your Google account and click **Allow**.

### "App not verified" warning

Google shows a security warning for open-source apps that have not gone through Google's formal verification process. This is expected and safe to proceed through:

1. Click **Advanced** (bottom-left of the warning screen)
2. Click **Go to Cloud Drive Sync (unsafe)**
3. Click **Allow**

This warning appears because the app is open-source and independently developed, not because it does anything unsafe. The OAuth credentials are visible in the source code for full transparency.

### Headless / Server / Docker

On servers, in Docker containers, or over SSH where no browser is available, use the `--headless` flag:

```bash
cloud-drive-sync account add --provider gdrive --headless
```

What happens:

1. The daemon prints an authorization URL in the terminal
2. Open that URL in **any browser** — your phone, a laptop, any device
3. Sign in with your Google account and click **Allow**
4. Google redirects to a `http://localhost?code=...` URL that will not load — this is normal
5. Copy the **full URL** from your browser's address bar (the one starting with `http://localhost?code=...`)
6. Paste it back into the terminal and press Enter

The daemon extracts the authorization code from the URL and completes setup automatically.

### Via the Web UI (Docker / HTTP mode)

When running with `--http-port` (e.g. `http://localhost:8080`):

1. Open the web UI and go to the **Accounts** tab
2. Click **Add Account** and select **Google Drive**
3. Click **Sign in with Google** — a URL is displayed
4. Open the URL in any browser, sign in, and click **Allow**
5. Copy the full redirect URL from your browser's address bar
6. Paste it into the input field in the web UI and click **Complete Setup**

### Custom OAuth credentials (power users)

If you want to use your own Google Cloud project credentials instead of the embedded ones:

**Option 1 — credentials file:**

1. Go to [Google Cloud Console](https://console.cloud.google.com/) → APIs & Services → Credentials
2. Create an **OAuth 2.0 Client ID** (Desktop application type)
3. Download the JSON file
4. Save it as `~/.config/cloud-drive-sync/client_secret.json`

The daemon automatically detects and uses this file if present.

**Option 2 — environment variables:**

```bash
export CDS_GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
export CDS_GOOGLE_CLIENT_SECRET=your-client-secret
cloud-drive-sync account add --provider gdrive
```

---

## Nextcloud (App Password)

Nextcloud uses app passwords — no app registration, no developer account, no OAuth flow. You generate a password in your Nextcloud settings and provide it directly.

### Generate an app password

1. Log in to your Nextcloud instance
2. Click your avatar (top-right) → **Settings**
3. Go to **Security** in the left sidebar
4. Scroll down to **Devices & sessions**
5. In the **Create new app password** field, enter a name (e.g. `cloud-drive-sync`)
6. Click **Create new app password**
7. Copy the generated password — it is shown only once

### Add the account

```bash
cloud-drive-sync account add --provider nextcloud
```

The CLI prompts for:

1. **Server URL** — your Nextcloud server address, e.g. `https://cloud.example.com`
2. **Username** — your Nextcloud login username
3. **App password** — the password you generated above (not your regular login password)

Via the desktop UI, go to **Account Manager** → **Add Account** → select **Nextcloud**, fill in the three fields, and click **Connect**. No browser window is opened.

### Notes

- Self-signed certificates: set `CDS_NEXTCLOUD_VERIFY_SSL=false` to disable certificate verification (not recommended for production)
- The server URL should not include a trailing slash or `/remote.php/dav`

---

## Dropbox (OAuth 2.0 PKCE)

Dropbox requires creating a free app in the Dropbox App Console. This is a one-time five-minute setup.

### Create a Dropbox app

1. Go to [https://www.dropbox.com/developers/apps](https://www.dropbox.com/developers/apps) and sign in
2. Click **Create app**
3. Choose **Scoped access**
4. Choose **Full Dropbox** (or **App folder** if you want to limit access to one folder)
5. Give the app a name (e.g. `my-cloud-drive-sync`) — this name must be unique on Dropbox
6. Click **Create app**
7. On the app settings page, note the **App key** — you will need this when adding the account
8. Under **OAuth 2 — Redirect URIs**, add `http://localhost` and click **Add**
9. Go to the **Permissions** tab and enable the following:
   - `files.metadata.read`
   - `files.metadata.write`
   - `files.content.read`
   - `files.content.write`
10. Click **Submit** at the bottom of the Permissions tab

### Add the account

```bash
cloud-drive-sync account add --provider dropbox
```

When prompted, enter your **App key** from step 7 above. The daemon then opens your browser for the standard Dropbox OAuth flow — sign in and click **Allow**.

### Headless

```bash
cloud-drive-sync account add --provider dropbox --headless
```

1. Enter your App key when prompted
2. The daemon prints an authorization URL
3. Open the URL in any browser and click **Allow**
4. Dropbox shows an authorization code on screen
5. Copy the code and paste it back into the terminal

---

## OneDrive (Azure AD)

OneDrive requires registering a free application in the Azure Portal. No paid Azure subscription is needed — a free Microsoft account is sufficient.

### Register an app in Azure

1. Go to [https://portal.azure.com](https://portal.azure.com) and sign in with any Microsoft account
2. Search for **App registrations** in the top search bar and open it
3. Click **New registration**
4. Fill in:
   - **Name**: `Cloud Drive Sync` (or any name you prefer)
   - **Supported account types**: select **Accounts in any organizational directory and personal Microsoft accounts**
   - **Redirect URI**: select **Public client/native (mobile & desktop)** from the dropdown, then enter `http://localhost:8400`
5. Click **Register**
6. On the overview page, note the **Application (client) ID** — you will need this when adding the account
7. Go to **API permissions** in the left sidebar
8. Click **Add a permission** → **Microsoft Graph** → **Delegated permissions**
9. Search for and enable:
   - `Files.ReadWrite.All`
   - `User.Read`
10. Click **Add permissions**
11. Click **Grant admin consent** if you are an admin and prompted to do so (personal accounts skip this)

### Add the account

```bash
cloud-drive-sync account add --provider onedrive
```

Enter your **Client ID** from step 6 when prompted. The browser opens for the Microsoft sign-in flow.

### Headless / Device code flow

OneDrive uses the device code flow for headless environments — no redirect URL needed, making it the most Docker-friendly option:

```bash
cloud-drive-sync account add --provider onedrive --headless
```

1. The daemon prints a device code and verification URL, e.g.:
   ```
   To sign in, use a web browser to open https://microsoft.com/devicelogin
   and enter the code ABCD-EFGH to authenticate.
   ```
2. Open `https://microsoft.com/devicelogin` on any device
3. Enter the code shown in the terminal
4. Sign in with your Microsoft account and click **Approve**
5. The daemon detects the approval automatically — no pasting needed

---

## Box (OAuth 2.0)

Box requires creating a free custom app in the Box Developer Console.

### Create a Box app

1. Go to [https://app.box.com/developers/console](https://app.box.com/developers/console) and sign in
2. Click **Create New App**
3. Select **Custom App**
4. Select **OAuth 2.0** as the authentication method
5. Give the app a name (e.g. `Cloud Drive Sync`) and click **Create App**
6. On the **Configuration** tab:
   - Note the **Client ID** and **Client Secret** — you will need both
   - Under **Redirect URIs**, add `http://localhost:8400`
   - Click **Save Changes**
7. On the **Configuration** tab, scroll to **Application Scopes** and enable:
   - **Read all files and folders stored in Box**
   - **Write all files and folders stored in Box**
8. Click **Save Changes**

### Add the account

Set your credentials as environment variables before running the add command:

```bash
export BOX_CLIENT_ID=your-client-id
export BOX_CLIENT_SECRET=your-client-secret
cloud-drive-sync account add --provider box
```

The daemon opens your browser for the Box sign-in and authorization flow.

### Headless

```bash
export BOX_CLIENT_ID=your-client-id
export BOX_CLIENT_SECRET=your-client-secret
cloud-drive-sync account add --provider box --headless
```

1. The daemon prints an authorization URL
2. Open the URL in any browser and sign in to Box
3. Box shows an authorization code
4. Paste the code back into the terminal

---

## Troubleshooting

**"redirect_uri_mismatch" error (Dropbox / OneDrive / Box)**
The redirect URI in your app registration does not match what the daemon sends. Double-check that you added exactly `http://localhost` (Dropbox) or `http://localhost:8400` (OneDrive, Box) — no trailing slash, correct port.

**Google "access_denied" after clicking Allow**
The Google account may have organization policies blocking OAuth apps. Try with a personal Gmail account, or have a G Suite admin approve the app.

**Nextcloud "401 Unauthorized"**
Make sure you are using an **app password**, not your regular Nextcloud login password. App passwords are generated in Settings → Security → Devices & sessions.

**Box / Dropbox credentials not found**
`BOX_CLIENT_ID` / `BOX_CLIENT_SECRET` must be set in the same shell session where you run `cloud-drive-sync account add`. They are used only during initial account setup — not during ongoing sync.
