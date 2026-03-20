#!/usr/bin/env bash
# GDrive Sync installer
# Usage: curl -fsSL https://raw.githubusercontent.com/ciberkids/cloud-drive-sync/main/install.sh | bash
set -euo pipefail

REPO="ciberkids/cloud-drive-sync"
INSTALL_DIR="$HOME/.local/bin"
SERVICE_DIR="$HOME/.config/systemd/user"

info()  { printf '\033[1;34m[info]\033[0m  %s\n' "$*"; }
warn()  { printf '\033[1;33m[warn]\033[0m  %s\n' "$*"; }
error() { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; exit 1; }

# ---------- detect distro ----------
detect_distro() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        case "$ID" in
            fedora|rhel|centos|rocky|alma) echo "rpm" ;;
            ubuntu|debian|pop|linuxmint)   echo "deb" ;;
            *)                              echo "other" ;;
        esac
    else
        echo "other"
    fi
}

# ---------- fetch latest release tag ----------
latest_tag() {
    curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" \
        | grep '"tag_name"' \
        | head -1 \
        | sed -E 's/.*"tag_name":\s*"([^"]+)".*/\1/'
}

# ---------- download a release asset by pattern ----------
download_asset() {
    local tag="$1" pattern="$2" dest="$3"
    local assets_url="https://api.github.com/repos/$REPO/releases/tags/$tag"
    local url
    url=$(curl -fsSL "$assets_url" \
        | grep '"browser_download_url"' \
        | grep "$pattern" \
        | head -1 \
        | sed -E 's/.*"browser_download_url":\s*"([^"]+)".*/\1/')
    [ -z "$url" ] && error "Could not find release asset matching '$pattern'"
    info "Downloading $url"
    curl -fsSL -o "$dest" "$url"
}

# ---------- install daemon ----------
install_daemon() {
    local tag="$1"
    mkdir -p "$INSTALL_DIR"
    download_asset "$tag" "gdrive-sync-daemon" "$INSTALL_DIR/gdrive-sync-daemon"
    chmod +x "$INSTALL_DIR/gdrive-sync-daemon"
    info "Daemon installed to $INSTALL_DIR/gdrive-sync-daemon"
}

# ---------- install UI ----------
install_ui() {
    local tag="$1" distro="$2"
    local tmpdir
    tmpdir=$(mktemp -d)
    trap 'rm -rf "$tmpdir"' EXIT

    case "$distro" in
        deb)
            download_asset "$tag" ".deb" "$tmpdir/gdrive-sync.deb"
            info "Installing .deb package (requires sudo)"
            sudo dpkg -i "$tmpdir/gdrive-sync.deb" || sudo apt-get install -f -y
            ;;
        rpm)
            download_asset "$tag" ".rpm" "$tmpdir/gdrive-sync.rpm"
            info "Installing .rpm package (requires sudo)"
            sudo rpm -U --force "$tmpdir/gdrive-sync.rpm"
            ;;
        *)
            download_asset "$tag" ".AppImage" "$INSTALL_DIR/gdrive-sync-ui.AppImage"
            chmod +x "$INSTALL_DIR/gdrive-sync-ui.AppImage"
            info "AppImage installed to $INSTALL_DIR/gdrive-sync-ui.AppImage"
            ;;
    esac
}

# ---------- install systemd service ----------
install_service() {
    local tag="$1"
    mkdir -p "$SERVICE_DIR"
    download_asset "$tag" "gdrive-sync-daemon.service" "$SERVICE_DIR/gdrive-sync-daemon.service"
    systemctl --user daemon-reload
    systemctl --user enable gdrive-sync-daemon
    info "Systemd user service enabled (start with: systemctl --user start gdrive-sync-daemon)"
}

# ---------- ensure ~/.local/bin is on PATH ----------
ensure_path() {
    case ":$PATH:" in
        *":$INSTALL_DIR:"*) ;;
        *)
            warn "$INSTALL_DIR is not on your PATH"
            warn "Add this to your shell profile:  export PATH=\"\$HOME/.local/bin:\$PATH\""
            ;;
    esac
}

# ---------- main ----------
main() {
    info "GDrive Sync installer"

    local distro
    distro=$(detect_distro)
    info "Detected package format: $distro"

    local tag
    tag=$(latest_tag)
    [ -z "$tag" ] && error "Could not determine latest release"
    info "Latest release: $tag"

    install_daemon "$tag"
    install_ui "$tag" "$distro"
    install_service "$tag"
    ensure_path

    echo
    info "Installation complete!"
    info "Start the daemon:  systemctl --user start gdrive-sync-daemon"
    info "Open the UI:       gdrive-sync-ui (or launch from your app menu)"
}

main "$@"
