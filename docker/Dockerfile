FROM python:3.12-slim

LABEL org.opencontainers.image.source=https://github.com/ciberkids/cloud-drive-sync
LABEL org.opencontainers.image.description="Cloud Drive Sync daemon - multi-cloud bidirectional file sync"
LABEL org.opencontainers.image.licenses=MIT

RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install daemon with all providers
COPY daemon/ ./daemon/
RUN pip install --no-cache-dir "./daemon[all-providers]"

# Create directories for config, data, and sync folders
RUN mkdir -p /root/.config/cloud-drive-sync \
    /root/.local/share/cloud-drive-sync \
    /data

# Default sync folder mount point
VOLUME ["/root/.config/cloud-drive-sync", "/root/.local/share/cloud-drive-sync", "/data"]

# Expose IPC socket directory
VOLUME ["/run/cloud-drive-sync"]
ENV XDG_RUNTIME_DIR=/run/cloud-drive-sync

ENTRYPOINT ["python", "-m", "cloud_drive_sync"]
CMD ["start", "--foreground"]
