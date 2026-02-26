import { useStatus } from "../lib/hooks";
import * as ipc from "../lib/ipc";

export function TrayMenu() {
  const status = useStatus(3000);

  const statusText = () => {
    if (!status.connected) return "Daemon offline";
    if (status.syncing) return "Syncing...";
    if (status.paused) return "Paused";
    if (status.error) return "Error";
    return "Up to date";
  };

  return (
    <div className="tray-menu">
      <div className="tray-status">
        <strong>{statusText()}</strong>
        {status.active_transfers > 0 && (
          <span>{status.active_transfers} active transfers</span>
        )}
      </div>
      <div className="tray-actions">
        <button
          onClick={() => ipc.forceSync()}
          disabled={!status.connected || status.syncing}
          className="btn btn-sm"
        >
          Sync Now
        </button>
        <button
          onClick={() =>
            status.paused ? ipc.resumeSync() : ipc.pauseSync()
          }
          disabled={!status.connected}
          className="btn btn-sm"
        >
          {status.paused ? "Resume" : "Pause"}
        </button>
      </div>
    </div>
  );
}
