FROM python:3.12-slim

LABEL org.opencontainers.image.source=https://github.com/ciberkids/cloud-drive-sync
LABEL org.opencontainers.image.description="Cloud Drive Sync daemon - multi-cloud bidirectional file sync"
LABEL org.opencontainers.image.licenses=MIT

RUN apt-get update && apt-get install -y --no-install-recommends \
    gosu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install daemon with all providers
COPY daemon/ ./daemon/
RUN pip install --no-cache-dir "./daemon[all-providers,mcp]"

# Entrypoint handles PUID/PGID remapping via gosu.
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Create directories for config, data, and sync folders
RUN mkdir -p /root/.config/cloud-drive-sync \
    /root/.local/share/cloud-drive-sync \
    /data \
    /run/cloud-drive-sync

# Default sync folder mount point
VOLUME ["/root/.config/cloud-drive-sync", "/root/.local/share/cloud-drive-sync", "/data"]

# Expose IPC socket directory
VOLUME ["/run/cloud-drive-sync"]
ENV XDG_RUNTIME_DIR=/run/cloud-drive-sync

# HTTP REST API + Web UI
EXPOSE 8080

# MCP server for AI assistants. Opt-in: set CDS_MCP_PORT=8081 to enable, and
# publish the port. Off by default so upgrading never opens a new control
# channel for anyone who did not ask for one.
EXPOSE 8081

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "cloud_drive_sync", "start", "--foreground", "--http-port", "8080"]
