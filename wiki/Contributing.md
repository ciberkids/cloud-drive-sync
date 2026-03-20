# Contributing

## Dev Setup

```bash
git clone https://github.com/ciberkids/cloud-drive-sync.git
cd cloud-drive-sync
./dev.sh          # Sets up both daemon and UI, starts in demo mode
```

Or manually:

```bash
# Daemon
cd daemon
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# UI
cd ui
npm install
```

## Code Style

### Python (daemon)

- **Linter**: [ruff](https://docs.astral.sh/ruff/) (target: Python 3.12, line length: 100)
- **Formatting**: ruff format (black-compatible)
- Run: `cd daemon && .venv/bin/ruff check src/ tests/`

### TypeScript (UI frontend)

- **Type checking**: `tsc --noEmit` in strict mode
- Run: `cd ui && npx tsc --noEmit`

### Rust (Tauri backend)

- **Formatting**: `cargo fmt`
- **Linting**: `cargo clippy`
- Run: `cd ui/src-tauri && cargo fmt && cargo clippy`

### Quick Lint Check

```bash
make lint   # Runs ruff + tsc
```

## Testing

### Daemon Tests

```bash
cd daemon
source .venv/bin/activate
pytest -v                    # Run all tests
pytest --cov=gdrive_sync     # With coverage
pytest tests/test_planner.py # Run a specific file
```

Tests use `pytest-asyncio` with `asyncio_mode = "auto"` (async test functions are detected automatically).

#### Bug and Feature Regression Tests

| Test File | Covers |
|---|---|
| `test_bug_activity_filters.py` | Activity log filtering by pair and event_type normalization |
| `test_bug_remote_browser.py` | Remote folder browser query filtering |
| `test_bug_stale_data.py` | Stale pair cleanup on engine startup |
| `test_bug_status_counts.py` | Accurate `files_synced` count from DB |
| `test_bug_sync_trigger.py` | `force_sync`/`pause_sync`/`resume_sync` with `pair_id` parameter |
| `test_feature_ignore_hidden.py` | Hidden file filtering in scanner, watcher, and planner |

### Running All Checks

```bash
make lint   # Lint Python + TypeScript
make test   # Run pytest
```

## PR Process

### Branch Naming

Use descriptive branch names with a prefix:

- `feat/add-selective-sync` — new feature
- `fix/conflict-resolution-race` — bug fix
- `refactor/split-sync-engine` — code refactoring
- `docs/update-api-reference` — documentation
- `test/add-executor-tests` — test additions

### Commit Messages

Write clear, imperative commit messages:

```
Add selective sync filtering by file extension

Support include/exclude glob patterns in sync pair config.
Patterns are evaluated by the planner before generating actions.
```

### Required Checks

Before submitting a PR:

1. `make lint` passes (ruff + tsc)
2. `make test` passes (pytest)
3. New features include tests
4. Documentation is updated if the public API changes

## How to Add a New IPC Method

1. **Define the method name** in `daemon/src/gdrive_sync/ipc/protocol.py`
2. **Add the handler** in `daemon/src/gdrive_sync/ipc/handlers.py`
3. **Add the Tauri command** in `ui/src-tauri/src/commands.rs` and register in `main.rs`
4. **Add the TypeScript client** in `ui/src/lib/ipc.ts`
5. **Add types** in `ui/src/lib/types.ts` if needed
6. **Write tests** in `daemon/tests/` for the handler
7. **Document the method** in the [[API Reference|API-Reference]]

## How to Add a New UI Page

1. **Create the component** in `ui/src/components/MyPage.tsx`
2. **Add the route** in `ui/src/App.tsx`
3. **Add navigation** in the `NavBar` component in `ui/src/App.tsx`
4. **Add hooks** in `ui/src/lib/hooks.ts` if the page needs daemon data
5. **Add styles** in the appropriate CSS file
