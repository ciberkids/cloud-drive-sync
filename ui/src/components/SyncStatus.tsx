import { useState, useCallback, useEffect, useRef } from "react";
import { useStatus, useDaemonEvent } from "../lib/hooks";
import * as ipc from "../lib/ipc";

interface SyncResult {
  type: "success" | "error" | "noop";
  message: string;
}

export function SyncStatus() {
  const status = useStatus(2000);
  const [syncPending, setSyncPending] = useState(false);
  const [syncResult, setSyncResult] = useState<SyncResult | null>(null);
  const resultTimer = useRef<ReturnType<typeof setTimeout>>();

  // Listen for sync_complete to show result
  useDaemonEvent<{
    pair_id: string;
    uploaded: number;
    downloaded: number;
    errors: number;
  }>("daemon:sync_complete", useCallback((payload) => {
    if (!syncPending) return;
    setSyncPending(false);

    const total = payload.uploaded + payload.downloaded;
    if (payload.errors > 0) {
      setSyncResult({
        type: "error",
        message: `Sync finished with ${payload.errors} error${payload.errors > 1 ? "s" : ""}${total > 0 ? `, ${total} file${total > 1 ? "s" : ""} transferred` : ""}`,
      });
    } else if (total === 0) {
      setSyncResult({
        type: "noop",
        message: "Everything is up to date — nothing to sync",
      });
    } else {
      const parts: string[] = [];
      if (payload.uploaded > 0)
        parts.push(`${payload.uploaded} uploaded`);
      if (payload.downloaded > 0)
        parts.push(`${payload.downloaded} downloaded`);
      setSyncResult({
        type: "success",
        message: `Sync complete: ${parts.join(", ")}`,
      });
    }
  }, [syncPending]));

  // Auto-dismiss result after 6 seconds
  useEffect(() => {
    if (syncResult) {
      clearTimeout(resultTimer.current);
      resultTimer.current = setTimeout(() => setSyncResult(null), 6000);
    }
    return () => clearTimeout(resultTimer.current);
  }, [syncResult]);

  const handleForceSync = async () => {
    setSyncResult(null);
    setSyncPending(true);
    try {
      await ipc.forceSync();
    } catch (e) {
      setSyncPending(false);
      setSyncResult({
        type: "error",
        message: `Failed to start sync: ${e instanceof Error ? e.message : String(e)}`,
      });
    }
  };

  // Timeout: if sync takes too long without a result, reset
  useEffect(() => {
    if (!syncPending) return;
    const timeout = setTimeout(() => {
      if (syncPending) {
        setSyncPending(false);
        // Don't show error — the sync might still complete, just the UI won't track it
      }
    }, 60000);
    return () => clearTimeout(timeout);
  }, [syncPending]);

  const handleTogglePause = async () => {
    try {
      if (status.paused) {
        await ipc.resumeSync();
      } else {
        await ipc.pauseSync();
      }
    } catch (e) {
      console.error("Toggle pause failed:", e);
    }
  };

  const statusIcon = () => {
    if (!status.connected) return "\u25CB";
    if (status.error) return "\u2716";
    if (status.syncing || syncPending) return "\u21BB";
    if (status.paused) return "\u275A\u275A";
    return "\u2714";
  };

  const statusText = () => {
    if (!status.connected) return "Disconnected";
    if (status.error) return `Error: ${status.error}`;
    if (syncPending) return "Syncing...";
    if (status.syncing) return "Syncing...";
    if (status.paused) return "Paused";
    return "Up to date";
  };

  const statusClass = () => {
    if (!status.connected) return "status-disconnected";
    if (status.error) return "status-error";
    if (status.syncing || syncPending) return "status-syncing";
    if (status.paused) return "status-paused";
    return "status-idle";
  };

  return (
    <div className="sync-status">
      <div className={`status-header ${statusClass()}`}>
        <span className="status-icon">{statusIcon()}</span>
        <div className="status-info">
          <h2>{statusText()}</h2>
          {status.last_sync && (
            <p className="last-sync">
              Last sync: {new Date(status.last_sync).toLocaleString()}
            </p>
          )}
        </div>
      </div>

      {syncResult && (
        <div className={`sync-result sync-result-${syncResult.type}`}>
          <span className="sync-result-icon">
            {syncResult.type === "success" ? "\u2714" : syncResult.type === "error" ? "\u2718" : "\u2714"}
          </span>
          <span className="sync-result-message">{syncResult.message}</span>
          <button className="sync-result-dismiss" onClick={() => setSyncResult(null)}>&times;</button>
        </div>
      )}

      <div className="status-stats">
        <div className="stat">
          <span className="stat-value">{status.files_synced}</span>
          <span className="stat-label">Files synced</span>
        </div>
        <div className="stat">
          <span className="stat-value">{status.active_transfers}</span>
          <span className="stat-label">Active transfers</span>
        </div>
      </div>

      {status.live_transfers.length > 0 && (
        <div className="live-transfers">
          <h3>Live Transfers</h3>
          {status.live_transfers.map((t) => {
            const pct = t.total > 0 ? Math.round((t.bytes / t.total) * 100) : 0;
            const fileName = t.path.split("/").pop() || t.path;
            return (
              <div key={t.path} className="transfer-item">
                <div className="transfer-header">
                  <span className="transfer-direction">
                    {t.direction === "upload" ? "\u2191" : "\u2193"}
                  </span>
                  <span className="transfer-name" title={t.path}>{fileName}</span>
                  <span className="transfer-speed">{t.speed_formatted}</span>
                </div>
                {t.total > 0 && (
                  <div className="transfer-progress">
                    <div className="transfer-bar">
                      <div className="transfer-bar-fill" style={{ width: `${pct}%` }} />
                    </div>
                    <span className="transfer-pct">{pct}%</span>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {(status.syncing || syncPending) && status.live_transfers.length === 0 && (
        <div className="progress-bar">
          <div className="progress-bar-fill progress-indeterminate" />
        </div>
      )}

      <div className="status-actions">
        <button
          onClick={handleForceSync}
          disabled={!status.connected || status.syncing || syncPending}
          className="btn btn-primary"
        >
          {syncPending ? "Syncing..." : "Sync Now"}
        </button>
        <button
          onClick={handleTogglePause}
          disabled={!status.connected}
          className="btn btn-secondary"
        >
          {status.paused ? "Resume" : "Pause"}
        </button>
      </div>

      {status.daemon && (
        <div className="daemon-info">
          <h3>Daemon</h3>
          <div className="daemon-details">
            <div className="daemon-row">
              <span className="daemon-label">Status</span>
              <span className="daemon-value">
                <span className={`daemon-dot ${status.connected ? "running" : "stopped"}`} />
                {status.connected ? "Running" : "Stopped"}
              </span>
            </div>
            <div className="daemon-row">
              <span className="daemon-label">PID</span>
              <span className="daemon-value">{status.daemon.pid ?? "N/A"}</span>
            </div>
            <div className="daemon-row">
              <span className="daemon-label">Uptime</span>
              <span className="daemon-value">{status.daemon.uptime_formatted ?? "N/A"}</span>
            </div>
            <div className="daemon-row">
              <span className="daemon-label">Socket</span>
              <span className="daemon-value daemon-socket" title={status.daemon.socket_path ?? ""}>
                {status.daemon.socket_path ?? "N/A"}
              </span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
