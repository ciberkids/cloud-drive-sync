import { useState, useCallback, useEffect, useRef } from "react";
import { useStatus, useDaemonEvent, useSyncPairs } from "../lib/hooks";
import { providerLabel, providerColor } from "./AccountManager";
import * as ipc from "../lib/ipc";
import type { SyncCompleteFiles } from "../lib/types";

interface SyncResult {
  type: "success" | "error" | "noop";
  message: string;
  files?: SyncCompleteFiles;
  deleted?: number;
}

export function SyncStatus() {
  const status = useStatus(2000);
  const { pairs } = useSyncPairs();
  const [syncPending, setSyncPending] = useState(false);
  const [syncResult, setSyncResult] = useState<SyncResult | null>(null);
  const resultTimer = useRef<ReturnType<typeof setTimeout>>();

  // pair_id from engine is "pair_0","pair_1"...; pairs[].id is "0","1"...
  const pairMap = Object.fromEntries([
    ...pairs.map((p) => [p.id, p]),
    ...pairs.map((p, i) => [`pair_${i}`, p]),
  ]);

  const [showSyncDetail, setShowSyncDetail] = useState(false);

  // Listen for sync_complete to show result
  useDaemonEvent<{
    pair_id: string;
    uploaded: number;
    downloaded: number;
    deleted: number;
    errors: number;
    files?: SyncCompleteFiles;
  }>("daemon:sync_complete", useCallback((payload) => {
    if (!syncPending) return;
    setSyncPending(false);
    setShowSyncDetail(false);

    const total = payload.uploaded + payload.downloaded;
    const deleted = payload.deleted ?? 0;
    if (payload.errors > 0) {
      setSyncResult({
        type: "error",
        message: `Sync finished with ${payload.errors} error${payload.errors > 1 ? "s" : ""}${total > 0 ? `, ${total} file${total > 1 ? "s" : ""} transferred` : ""}`,
        files: payload.files,
        deleted,
      });
    } else if (total === 0 && deleted === 0) {
      setSyncResult({
        type: "noop",
        message: "Everything is up to date — nothing to sync",
        files: payload.files,
        deleted,
      });
    } else {
      const parts: string[] = [];
      if (payload.uploaded > 0)
        parts.push(`${payload.uploaded} uploaded`);
      if (payload.downloaded > 0)
        parts.push(`${payload.downloaded} downloaded`);
      if (deleted > 0)
        parts.push(`${deleted} deleted`);
      setSyncResult({
        type: "success",
        message: `Sync complete: ${parts.join(", ")}`,
        files: payload.files,
        deleted,
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
    if (!status.daemon_reachable) return "Daemon not running";
    if (!status.connected) return "No account connected";
    if (status.error) return `Error: ${status.error}`;
    if (syncPending) return "Syncing...";
    if (status.syncing) return "Syncing...";
    if (status.paused) return "Paused";
    return "Up to date";
  };

  const statusClass = () => {
    if (!status.daemon_reachable) return "status-disconnected";
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
            {syncResult.type === "error" ? "\u2718" : "\u2714"}
          </span>
          <span className="sync-result-message">{syncResult.message}</span>
          {syncResult.files && (
            (syncResult.files.uploaded.length > 0 || syncResult.files.downloaded.length > 0 ||
             syncResult.files.deleted.length > 0 || syncResult.files.conflicted.length > 0) && (
              <button
                className="sync-result-detail-btn"
                onClick={() => setShowSyncDetail((v) => !v)}
              >
                {showSyncDetail ? "Hide details" : "View details"}
              </button>
            )
          )}
          <button className="sync-result-dismiss" onClick={() => { setSyncResult(null); setShowSyncDetail(false); }}>&times;</button>
        </div>
      )}

      {showSyncDetail && syncResult?.files && (
        <div className="sync-detail-panel">
          {(["uploaded", "downloaded", "deleted", "conflicted"] as const).map((cat) => {
            const list = syncResult.files![cat];
            if (!list || list.length === 0) return null;
            const labels: Record<string, string> = {
              uploaded: "Uploaded",
              downloaded: "Downloaded",
              deleted: "Deleted",
              conflicted: "Conflicted",
            };
            return (
              <div key={cat} className="sync-detail-group">
                <div className="sync-detail-group-header">
                  <span className="sync-detail-group-label">{labels[cat]}</span>
                  <span className="sync-detail-group-count">{list.length}</span>
                </div>
                <ul className="sync-detail-list">
                  {list.slice(0, 50).map((f) => (
                    <li key={f} className="sync-detail-file" title={f}>
                      {f.split("/").pop()}
                      {f.includes("/") && <span className="sync-detail-dir"> \u2014 {f.substring(0, f.lastIndexOf("/"))}</span>}
                    </li>
                  ))}
                  {list.length > 50 && (
                    <li className="sync-detail-more">+{list.length - 50} more</li>
                  )}
                </ul>
              </div>
            );
          })}
        </div>
      )}

      {status.pair_counts && status.pair_counts.length > 0 ? (
        <div className="pair-bridges">
          {status.pair_counts.map((pc) => {
            const pair = pairMap[pc.pair_id];
            const color = providerColor(pc.provider);
            const label = providerLabel(pc.provider);
            const folderName = pc.local_path.split("/").filter(Boolean).pop() || pc.local_path;
            const accountShort = pc.account_id ? pc.account_id.split("@")[0] : "";
            const syncMode = pair?.sync_mode ?? "two_way";
            const dirIcon = syncMode === "upload_only" ? "↑" : syncMode === "download_only" ? "↓" : "↔";
            const isEnabled = pair?.enabled ?? true;
            return (
              <div key={pc.pair_id} className={`pair-bridge-row${!isEnabled ? " pair-bridge-disabled" : ""}`}>
                <div className="pair-bridge-local">
                  <span className="pair-bridge-folder" title={pc.local_path}>{folderName}</span>
                </div>
                <span className="pair-bridge-dir" title={syncMode}>{dirIcon}</span>
                <div className="pair-bridge-remote">
                  <span className="pair-bridge-pill" style={{ background: color }}>{label}</span>
                  {accountShort && <span className="pair-bridge-account" title={pc.account_id}>{accountShort}</span>}
                  {/* Only shown when the provider reports it — currently Nextcloud,
                      where push vs polling is a large difference in server load. */}
                  {pc.change_detection && (
                    <span
                      className={`pair-bridge-mechanism${
                        pc.change_detection.startsWith("push") ? " pair-bridge-mechanism-push" : ""
                      }`}
                      title={
                        pc.change_detection.startsWith("push")
                          ? "Changes arrive by push — no directory scanning"
                          : `Changes found by scanning: ${pc.change_detection}`
                      }
                    >
                      {pc.change_detection.startsWith("push") ? "\u26A1 push" : "\u21BB polling"}
                    </span>
                  )}
                </div>
                <div className="pair-bridge-count">
                  <span className="pair-bridge-count-value">{pc.files_synced.toLocaleString()}</span>
                  <span className="pair-bridge-count-label">files</span>
                </div>
              </div>
            );
          })}
        </div>
      ) : (
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
      )}

      {status.live_transfers.length > 0 && (
        <div className="live-transfers">
          <h3>Live Transfers</h3>
          {status.live_transfers.map((t) => {
            const pct = t.total > 0 ? Math.round((t.bytes / t.total) * 100) : 0;
            const fileName = t.path.split("/").pop() || t.path;
            const pair = pairMap[t.pair_id];
            const pColor = pair ? providerColor(pair.provider) : undefined;
            const pLabel = pair ? providerLabel(pair.provider) : undefined;
            const pFolder = pair?.local_path?.split("/").filter(Boolean).pop();
            const pAccount = pair?.account_id;
            return (
              <div key={`${t.pair_id}-${t.path}`} className="transfer-item">
                <div className="transfer-header">
                  <span className="transfer-direction">
                    {t.direction === "upload" ? "\u2191" : "\u2193"}
                  </span>
                  <span className="transfer-name" title={t.path}>{fileName}</span>
                  <span className="transfer-speed">{t.speed_formatted}</span>
                </div>
                {pair && (
                  <div className="transfer-pair-context">
                    <span className="transfer-pair-pill" style={{ background: pColor }}>{pLabel}</span>
                    {pAccount && <span className="transfer-pair-account">{pAccount}</span>}
                    {pFolder && <span className="transfer-pair-folder">{"\u203a"} {pFolder}</span>}
                  </div>
                )}
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
            {status.daemon.version && (
              <div className="daemon-row">
                <span className="daemon-label">Version</span>
                <span className="daemon-value">{status.daemon.version}</span>
              </div>
            )}
            {status.daemon.started_at && (
              <div className="daemon-row">
                <span className="daemon-label">Started</span>
                <span className="daemon-value">{status.daemon.started_at}</span>
              </div>
            )}
            {status.daemon.database && (
              <div className="daemon-row">
                <span className="daemon-label">Database</span>
                <span
                  className="daemon-value"
                  title={
                    `${status.daemon.database.page_count} pages, ` +
                    `${status.daemon.database.freelist_count} free — ` +
                    `${status.daemon.database.reclaimable_formatted} reclaimable`
                  }
                >
                  {status.daemon.database.size_formatted}
                  {/* Only worth mentioning once a meaningful share of a
                      non-trivial file is dead space — otherwise it is noise. */}
                  {status.daemon.database.reclaimable_ratio >= 0.25 &&
                    status.daemon.database.reclaimable_bytes > 64 * 1024 * 1024 && (
                      <span className="daemon-warn">
                        {" "}
                        · {status.daemon.database.reclaimable_formatted} reclaimable
                      </span>
                    )}
                </span>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
