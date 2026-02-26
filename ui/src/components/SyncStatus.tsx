import { useStatus } from "../lib/hooks";
import * as ipc from "../lib/ipc";

export function SyncStatus() {
  const status = useStatus();

  const handleForceSync = async () => {
    try {
      await ipc.forceSync();
    } catch (e) {
      console.error("Force sync failed:", e);
    }
  };

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
    if (!status.connected) return "\u25CB"; // empty circle
    if (status.error) return "\u2716"; // x mark
    if (status.syncing) return "\u21BB"; // clockwise arrows
    if (status.paused) return "\u275A\u275A"; // pause
    return "\u2714"; // checkmark
  };

  const statusText = () => {
    if (!status.connected) return "Disconnected";
    if (status.error) return `Error: ${status.error}`;
    if (status.syncing) return "Syncing...";
    if (status.paused) return "Paused";
    return "Up to date";
  };

  const statusClass = () => {
    if (!status.connected) return "status-disconnected";
    if (status.error) return "status-error";
    if (status.syncing) return "status-syncing";
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

      {status.syncing && (
        <div className="progress-bar">
          <div className="progress-bar-fill progress-indeterminate" />
        </div>
      )}

      <div className="status-actions">
        <button
          onClick={handleForceSync}
          disabled={!status.connected || status.syncing}
          className="btn btn-primary"
        >
          Sync Now
        </button>
        <button
          onClick={handleTogglePause}
          disabled={!status.connected}
          className="btn btn-secondary"
        >
          {status.paused ? "Resume" : "Pause"}
        </button>
      </div>
    </div>
  );
}
