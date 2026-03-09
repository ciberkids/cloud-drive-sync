# Contributing

## Dev Setup

```bash
git clone https://github.com/gdrive-sync/gdrive-sync.git
cd gdrive-sync
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

The following test files cover specific bugs and features with targeted regression tests:

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

## Architecture Overview

```
gdrive-sync/
├── daemon/                  # Python sync daemon
│   ├── src/gdrive_sync/
│   │   ├── sync/            # Sync engine, planner, executor, conflicts
│   │   ├── drive/           # Google Drive API client
│   │   ├── local/           # Filesystem watcher, scanner, hasher
│   │   ├── ipc/             # JSON-RPC server + handlers
│   │   ├── db/              # SQLite database + models
│   │   ├── auth/            # OAuth2 credential management
│   │   ├── util/            # Logging, paths, retry
│   │   ├── config.py        # TOML config loader
│   │   ├── daemon.py        # Main daemon class
│   │   └── cli.py           # Click CLI entry point
│   └── tests/
├── ui/                      # Tauri + React desktop UI
│   ├── src/
│   │   ├── components/      # React page components
│   │   └── lib/             # IPC client, types, hooks
│   └── src-tauri/src/       # Rust backend (bridge, commands, tray)
├── docs/                    # Documentation
└── installer/               # systemd service file
```

For full architectural details, see [docs/ARCHITECTURE.md](ARCHITECTURE.md).

## How to Add a New IPC Method

1. **Define the method name** in `daemon/src/gdrive_sync/ipc/protocol.py`:

   ```python
   METHOD_MY_METHOD = "my_method"
   ```

   Add it to the `ALL_METHODS` list.

2. **Add the handler** in `daemon/src/gdrive_sync/ipc/handlers.py`:

   Register it in the `self._handlers` dict in `RequestHandler.__init__`:
   ```python
   self._handlers["my_method"] = self._my_method
   ```

   Implement the handler:
   ```python
   async def _my_method(self, params: dict) -> dict:
       value = params.get("value")
       if value is None:
           raise TypeError("value is required")
       result = await self._engine.do_something(value)
       return {"status": "ok", "data": result}
   ```

3. **Add the Tauri command** in `ui/src-tauri/src/commands.rs`:

   ```rust
   #[tauri::command]
   pub async fn my_method(
       state: tauri::State<'_, BridgeState>,
       value: String,
   ) -> Result<serde_json::Value, String> {
       let mut bridge = state.0.lock().await;
       bridge.call("my_method", json!({"value": value}))
           .await
           .map_err(|e| e.to_string())
   }
   ```

   Register it in `main.rs`:
   ```rust
   commands::my_method,
   ```

4. **Add the TypeScript client** in `ui/src/lib/ipc.ts`:

   ```typescript
   export async function myMethod(value: string): Promise<MyResult> {
     return invoke<MyResult>("my_method", { value });
   }
   ```

5. **Add types** in `ui/src/lib/types.ts` if needed.

6. **Write tests** in `daemon/tests/` for the handler.

7. **Document the method** in `docs/API.md` with params, response, and JSON examples.

## How to Add a New UI Page

1. **Create the component** in `ui/src/components/MyPage.tsx`:

   ```tsx
   export function MyPage() {
     return (
       <div className="my-page">
         <h2>My Page</h2>
         {/* content */}
       </div>
     );
   }
   ```

2. **Add the route** in `ui/src/App.tsx`:

   ```tsx
   import { MyPage } from "./components/MyPage";

   // Inside <Routes>:
   <Route path="/my-page" element={<MyPage />} />
   ```

3. **Add navigation** in the `NavBar` component in `ui/src/App.tsx`:

   ```tsx
   <li>
     <NavLink to="/my-page">My Page</NavLink>
   </li>
   ```

4. **Add hooks** in `ui/src/lib/hooks.ts` if the page needs to fetch daemon data.

5. **Add styles** in the appropriate CSS file.
