#!/bin/bash
# Docker entrypoint — remaps the daemon process to PUID:PGID when specified.
# Usage:  docker run -e PUID=1000 -e PGID=1000 ...
#
# Without PUID/PGID (or PUID=0), the container runs as root (legacy behaviour).
# Config and data paths stay at /root/.{config,local}/share regardless of the
# remapped UID so existing bind-mount layouts remain compatible.
set -e

PUID="${PUID:-0}"
PGID="${PGID:-0}"

if [ "$PUID" != "0" ] || [ "$PGID" != "0" ]; then
    # Create the group if no group with that GID exists yet.
    if ! getent group "$PGID" > /dev/null 2>&1; then
        groupadd -g "$PGID" syncgroup
    fi

    # Create the user if no user with that UID exists yet.
    if ! getent passwd "$PUID" > /dev/null 2>&1; then
        useradd -u "$PUID" -g "$PGID" -d /root -M -s /bin/sh syncuser
    fi

    # /root is 0700 in the base image, so the remapped user cannot traverse it to
    # reach the config and data directories underneath — the daemon died on its
    # first path lookup with "Permission denied: /root/.config/cloud-drive-sync",
    # before opening a single file. Chowning those directories (below) is not
    # enough on its own when the parent cannot be entered.
    #
    # o+x grants traversal only. The contents of /root stay unlistable, which is
    # what 0700 was protecting; everything the daemon needs below it is already
    # 0755 and gets its ownership fixed next.
    chmod o+x /root

    # Fix ownership on all directories the daemon writes to.
    chown -R "${PUID}:${PGID}" \
        /root/.config/cloud-drive-sync \
        /root/.local/share/cloud-drive-sync \
        /data 2>/dev/null || true
    chown "${PUID}:${PGID}" /run/cloud-drive-sync 2>/dev/null || true

    # Keep XDG paths pointing at /root so existing volume mounts keep working.
    export XDG_CONFIG_HOME=/root/.config
    export XDG_DATA_HOME=/root/.local/share

    exec gosu "${PUID}:${PGID}" "$@"
fi

exec "$@"
