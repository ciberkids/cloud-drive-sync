# Cross-Cloud Sync

Cloud Drive Sync can relay files **between different cloud providers** without any intermediate server. The trick is simple: point two sync pairs at the same local folder — one in `download_only` mode, one in `upload_only` mode.

## How It Works

```
Source Cloud  ──download_only──▶  ~/cloud-bridge  ──upload_only──▶  Destination Cloud
```

The local folder acts as a bridge. The daemon continuously:

1. Downloads new/changed files from the source provider into `~/cloud-bridge`
2. Detects those changes via the filesystem watcher
3. Uploads them to the destination provider

No file ever leaves your machine unencrypted — the data path is always Source Cloud → your disk → Destination Cloud.

---

## Confirmed Working: Google Drive → Nextcloud

This is an actively used production scenario. Files download from Google Drive and upload to Nextcloud automatically.

### CLI setup

```bash
# 1. Add both accounts
cloud-drive-sync account add --provider gdrive
cloud-drive-sync account add --provider nextcloud --server https://your.nextcloud.com

# 2. Create a dedicated bridge folder (must exist before adding pairs)
mkdir -p ~/cloud-bridge

# 3. Download-only pair from Google Drive
cloud-drive-sync pair add \
  --local ~/cloud-bridge \
  --remote root \
  --account user@gmail.com \
  --provider gdrive \
  --mode download_only

# 4. Upload-only pair to Nextcloud
cloud-drive-sync pair add \
  --local ~/cloud-bridge \
  --remote /Documents/cloud-bridge \
  --account user@gmail.com \
  --provider nextcloud \
  --mode upload_only
```

> **Same email on both accounts?** That's fine — accounts are identified by `provider:email`, so `gdrive:user@gmail.com` and `nextcloud:user@gmail.com` are completely separate entries.

### Equivalent config.toml

```toml
[[accounts]]
email = "user@gmail.com"
provider = "gdrive"

[[accounts]]
email = "user@gmail.com"
provider = "nextcloud"
server_url = "https://your.nextcloud.com"

[[sync.pairs]]
local_path = "/home/user/cloud-bridge"
remote_folder_id = "root"
sync_mode = "download_only"      # Google Drive → local
account_id = "user@gmail.com"
provider = "gdrive"

[[sync.pairs]]
local_path = "/home/user/cloud-bridge"
remote_folder_id = "/Documents/cloud-bridge"
sync_mode = "upload_only"        # local → Nextcloud
account_id = "user@gmail.com"
provider = "nextcloud"
```

---

## Other Combinations

### Example: Google Drive → Dropbox

```bash
cloud-drive-sync account add --provider gdrive
cloud-drive-sync account add --provider dropbox

mkdir -p ~/cloud-bridge

cloud-drive-sync pair add \
  --local ~/cloud-bridge \
  --remote root \
  --account user@gmail.com \
  --provider gdrive \
  --mode download_only

cloud-drive-sync pair add \
  --local ~/cloud-bridge \
  --remote "" \
  --account user@dropbox.com \
  --provider dropbox \
  --mode upload_only
```

### Combinations table

| Source | Destination | Status |
|--------|-------------|--------|
| Google Drive | Nextcloud | ✅ Confirmed working |
| Google Drive | Dropbox | 🧪 Untested — please report |
| Google Drive | OneDrive | 🧪 Untested — please report |
| Google Drive | Box | 🧪 Untested — please report |
| Nextcloud | Google Drive | 🧪 Untested — please report |
| Dropbox | Google Drive | 🧪 Untested — please report |
| Any | Any | 🧪 Follow the pattern above |

If you try a combination and it works (or breaks), please [open an issue](https://github.com/ciberkids/cloud-drive-sync/issues) — your report helps everyone.

---

## Bidirectional Cross-Cloud

To mirror changes in **both directions** (edits on either side propagate to the other), use `two_way` for both pairs:

```toml
[[sync.pairs]]
local_path = "/home/user/cloud-bridge"
remote_folder_id = "root"
sync_mode = "two_way"
account_id = "user@gmail.com"
provider = "gdrive"

[[sync.pairs]]
local_path = "/home/user/cloud-bridge"
remote_folder_id = "/Documents/cloud-bridge"
sync_mode = "two_way"
account_id = "user@gmail.com"
provider = "nextcloud"
```

> **Warning:** If both providers modify the same file at nearly the same time, a sync loop can occur — the daemon detects the conflict and applies the configured conflict strategy (`keep_both`, `newest_wins`, or `ask`). For high-churn folders, `newest_wins` is usually the most predictable choice.

---

## Tips

- **Use a dedicated bridge folder.** Do not reuse an existing personal folder — any file already in that folder will be uploaded to the destination provider on first sync.
- **The bridge folder must exist** before you add sync pairs pointing to it. The daemon will not create it automatically.
- **Filter file types** you don't want to relay by adding `ignore_patterns` to one or both pairs:

  ```toml
  [[sync.pairs]]
  local_path = "/home/user/cloud-bridge"
  ignore_patterns = ["*.gdoc", "*.gsheet", "*.gslide", "*.tmp"]
  ```

- **Google Docs** are exported as `.docx`/`.xlsx`/`.pptx` locally by default. These exported files will be uploaded to the destination provider. If you only want native Google format on the source side, add those extensions to `ignore_patterns` on the upload pair.
- **Monitor the activity log** after first setup to confirm files are flowing as expected:

  ```bash
  cloud-drive-sync activity --limit 50
  ```
