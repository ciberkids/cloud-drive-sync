# Packaging

Platform-specific package manager manifests for Cloud Drive Sync.

## Homebrew (macOS)

Once submitted to [homebrew-cask](https://github.com/Homebrew/homebrew-cask):

```bash
brew install --cask cloud-drive-sync
```

## Scoop (Windows)

```powershell
scoop bucket add cloud-drive-sync https://github.com/ciberkids/cloud-drive-sync
scoop install cloud-drive-sync
```

## Release Notes

- The Homebrew cask uses `sha256 :no_check` during development. CI should update the SHA256 hash on each release.
- The Scoop manifest `hash` field must be updated with the installer's SHA256 on each release.
