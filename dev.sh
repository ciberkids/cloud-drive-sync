#!/usr/bin/env bash
#
# dev.sh — One-command development environment for cloud-drive-sync
#
# Usage:
#   ./dev.sh              Setup + start daemon in demo mode
#   ./dev.sh --with-ui    Also start the Tauri UI dev server
#   ./dev.sh --test       Run tests first, then start daemon
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DAEMON_DIR="$SCRIPT_DIR/daemon"
UI_DIR="$SCRIPT_DIR/ui"
VENV="$DAEMON_DIR/.venv"
PYTHON="$VENV/bin/python"
PIP="$VENV/bin/pip"

DEMO_BASE="$HOME/cloud-drive-sync-demo"
DEMO_LOCAL="$DEMO_BASE/local"
DEMO_REMOTE="$DEMO_BASE/remote"

WITH_UI=false
RUN_TESTS=false
DAEMON_PID=""
UI_PID=""

# ── Parse arguments ─────────────────────────────────────────────
for arg in "$@"; do
    case "$arg" in
        --with-ui) WITH_UI=true ;;
        --test)    RUN_TESTS=true ;;
        -h|--help)
            echo "Usage: ./dev.sh [--with-ui] [--test]"
            echo ""
            echo "  --with-ui    Also start the Tauri UI dev server"
            echo "  --test       Run tests before starting the daemon"
            exit 0
            ;;
        *)
            echo "Unknown option: $arg"
            echo "Run ./dev.sh --help for usage."
            exit 1
            ;;
    esac
done

# ── Cleanup on exit ─────────────────────────────────────────────
cleanup() {
    echo ""
    echo "Shutting down..."
    [ -n "$UI_PID" ] && kill "$UI_PID" 2>/dev/null && echo "  Stopped UI (PID $UI_PID)"
    [ -n "$DAEMON_PID" ] && kill "$DAEMON_PID" 2>/dev/null && echo "  Stopped daemon (PID $DAEMON_PID)"
    wait 2>/dev/null
    echo "Done."
}
trap cleanup EXIT INT TERM

# ── Check Python version ────────────────────────────────────────
check_python() {
    local py=""
    for candidate in python3.12 python3.13 python3; do
        if command -v "$candidate" &>/dev/null; then
            py="$candidate"
            break
        fi
    done

    if [ -z "$py" ]; then
        echo "ERROR: Python 3.12+ is required but not found."
        echo "Install it with your package manager (e.g., sudo dnf install python3.12)"
        exit 1
    fi

    local version
    version=$("$py" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    local major minor
    major=$(echo "$version" | cut -d. -f1)
    minor=$(echo "$version" | cut -d. -f2)

    if [ "$major" -lt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -lt 12 ]; }; then
        echo "ERROR: Python 3.12+ required, found $version"
        exit 1
    fi

    echo "$py"
}

# ── Setup venv ──────────────────────────────────────────────────
setup_venv() {
    if [ ! -d "$VENV" ]; then
        echo "Creating Python virtual environment..."
        local py
        py=$(check_python)
        "$py" -m venv "$VENV"
        "$PIP" install --quiet --upgrade pip
        "$PIP" install --quiet -e "$DAEMON_DIR[dev]"
        echo "  Venv created at $VENV"
    else
        echo "  Venv already exists at $VENV"
    fi
}

# ── Setup demo directories ──────────────────────────────────────
setup_demo_dirs() {
    mkdir -p "$DEMO_LOCAL" "$DEMO_REMOTE"
    echo "  Demo dirs: $DEMO_LOCAL (local), $DEMO_REMOTE (remote)"
}

# ── Wait for socket ─────────────────────────────────────────────
wait_for_socket() {
    local socket_path
    socket_path="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/cloud-drive-sync.sock"
    local timeout=15
    local elapsed=0

    echo -n "  Waiting for daemon socket"
    while [ ! -S "$socket_path" ] && [ "$elapsed" -lt "$timeout" ]; do
        echo -n "."
        sleep 0.5
        elapsed=$((elapsed + 1))
    done
    echo ""

    if [ -S "$socket_path" ]; then
        echo "  Daemon socket ready: $socket_path"
        return 0
    else
        echo "  WARNING: Daemon socket not found after ${timeout}s"
        return 1
    fi
}

# ── Main ────────────────────────────────────────────────────────
echo "========================================"
echo "  cloud-drive-sync development environment"
echo "========================================"
echo ""

# Step 1: Python environment
echo "[1/4] Setting up Python environment..."
setup_venv

# Step 2: Demo directories
echo "[2/4] Setting up demo directories..."
setup_demo_dirs

# Step 3: Run tests (if requested)
if [ "$RUN_TESTS" = true ]; then
    echo "[*] Running tests..."
    cd "$DAEMON_DIR"
    "$VENV/bin/pytest" -v
    cd "$SCRIPT_DIR"
    echo ""
fi

# Step 4: Start daemon in demo mode
echo "[3/4] Starting daemon in demo mode..."
"$PYTHON" -m cloud_drive_sync --log-level debug start --foreground --demo &
DAEMON_PID=$!
echo "  Daemon PID: $DAEMON_PID"
wait_for_socket || true

# Step 5: Start UI (if requested)
if [ "$WITH_UI" = true ]; then
    echo "[4/4] Starting Tauri UI dev server..."
    if [ ! -d "$UI_DIR/node_modules" ]; then
        echo "  Installing npm dependencies..."
        cd "$UI_DIR" && npm install && cd "$SCRIPT_DIR"
    fi
    cd "$UI_DIR" && npm run tauri dev &
    UI_PID=$!
    cd "$SCRIPT_DIR"
    echo "  UI PID: $UI_PID"
else
    echo "[4/4] Skipping UI (use --with-ui to start)"
fi

# Print banner
echo ""
echo "========================================"
echo "  Ready!"
echo "========================================"
echo ""
echo "  Daemon:  running in demo mode (PID $DAEMON_PID)"
echo "  Local:   $DEMO_LOCAL"
echo "  Remote:  $DEMO_REMOTE"
echo "  Socket:  ${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/cloud-drive-sync.sock"
if [ "$WITH_UI" = true ]; then
    echo "  UI:      http://localhost:1420"
fi
echo ""
echo "  Try it:"
echo "    echo 'hello' > $DEMO_LOCAL/test.txt"
echo "    ls $DEMO_REMOTE/   # should appear after sync"
echo ""
echo "  Press Ctrl+C to stop."
echo ""

# Wait for background processes
wait
