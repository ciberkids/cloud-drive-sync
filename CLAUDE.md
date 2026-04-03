# CLAUDE.md

## Development Workflow

After every code change, always follow this sequence:

1. **Run tests** — `cd daemon && python -m pytest tests/ -x -q` (445+ tests must pass)
2. **Run lint** — `cd daemon && .venv/bin/ruff check src/ tests/`
3. **Run Rust check** — `cd ui/src-tauri && cargo check` (create sidecar placeholder first if needed)
4. **Run TS check** — `cd ui && npx tsc --noEmit`
5. **Update documentation** — if the change affects user-facing behavior, update docs/ (DAEMON.md, ARCHITECTURE.md, CLI.md, UI.md) and README.md. docs/ is the single source of truth; the wiki auto-syncs from it.
6. **Update screenshots** — if the UI changed, refresh screenshots: `cd ui && DEMO=1 npx vite --port 1421` then capture with Chrome headless (see memory reference_screenshots.md)
7. **Commit and push** — descriptive commit message, push to main
8. **Check GitHub Issues** — look for open issues that may be affected by changes
9. **Tag a release** — if the changes are significant, tag `vX.Y.Z` to trigger the release pipeline (produces DEB, RPM, AppImage, Flatpak, DMG, MSI/NSIS, Docker image, standalone daemon)

## Parallelization

Use TeamCreate with worktree-isolated teammates for parallel workstreams. Do NOT use plain background Agents as a substitute.

## Key Commands

```bash
# Daemon tests
cd daemon && python -m pytest tests/ -x -q

# Lint
cd daemon && .venv/bin/ruff check src/ tests/

# Rust check (needs sidecar placeholder)
mkdir -p ui/src-tauri/bin && touch ui/src-tauri/bin/cloud-drive-sync-daemon-x86_64-unknown-linux-gnu
cd ui/src-tauri && cargo check

# TypeScript check
cd ui && npx tsc --noEmit

# Run daemon in demo mode with HTTP API
cd daemon && python -m cloud_drive_sync start --foreground --demo --http-port 8080

# Run UI in demo mode (for screenshots)
cd ui && DEMO=1 npx vite --port 1421

# Bruno API tests (daemon must be running with --http-port 8080)
bru run --env local tests/api/

# Capture screenshots
SCREENSHOTS=docs/screenshots
for route in "status-dashboard:/" "settings:/settings" "conflicts:/conflicts" "transfers:/transfers" "activity-log:/activity" "account-manager:/account"; do
  name="${route%%:*}"; path="${route##*:}"
  google-chrome --headless --disable-gpu --screenshot="$SCREENSHOTS/${name}.png" --window-size=900,650 --hide-scrollbars "http://localhost:1421${path}"
done
```

## Project Structure

- `daemon/` — Python sync daemon (pytest, ruff)
- `ui/` — Tauri + React desktop UI (cargo, tsc, vite)
- `docs/` — Single source of truth for all documentation (auto-synced to GitHub wiki)
- `tests/api/` — Bruno API test collection for HTTP REST API
- `docker/` — Dockerfile + docker-compose for headless deployment
- `installer/` — systemd service, launchd plist, desktop files, icons
- `packaging/` — Homebrew cask, Scoop manifest
- `flatpak/` — Flatpak manifest + metainfo

## Embedded Credentials

Google OAuth client ID/secret are embedded in `daemon/src/cloud_drive_sync/auth/oauth.py`. GitHub Push Protection will flag these — they must be explicitly allowed via the unblock URLs in the push rejection message. This is standard practice for open-source desktop OAuth apps.
