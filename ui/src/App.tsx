import { BrowserRouter, Routes, Route, NavLink } from "react-router-dom";
import { useState } from "react";
import { SyncStatus } from "./components/SyncStatus";
import { Settings } from "./components/Settings";
import { ConflictDialog } from "./components/ConflictDialog";
import { ActivityLog } from "./components/ActivityLog";
import { Transfers } from "./components/Transfers";
import { AccountManager } from "./components/AccountManager";
import { useStatus } from "./lib/hooks";
import * as ipc from "./lib/ipc";

function NavBar() {
  const status = useStatus();
  const dotClass = status.daemon_reachable
    ? status.connected
      ? "connected"
      : "authenticated-no"
    : "disconnected";

  return (
    <nav className="sidebar">
      <div className="sidebar-header">
        <h1>Cloud Drive Sync</h1>
        <span
          className={`connection-dot ${dotClass}`}
          title={
            status.daemon_reachable
              ? status.connected
                ? "Connected"
                : "Daemon running, no account"
              : "Daemon not reachable"
          }
        />
      </div>
      <ul className="nav-list">
        <li>
          <NavLink to="/" end>
            Status
          </NavLink>
        </li>
        <li>
          <NavLink to="/settings">Settings</NavLink>
        </li>
        <li>
          <NavLink to="/conflicts">Conflicts</NavLink>
        </li>
        <li>
          <NavLink to="/transfers">Transfers</NavLink>
        </li>
        <li>
          <NavLink to="/activity">Activity</NavLink>
        </li>
        <li>
          <NavLink to="/account">Account</NavLink>
        </li>
      </ul>
    </nav>
  );
}

function DaemonBanner() {
  const status = useStatus();
  const [reconnecting, setReconnecting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Daemon reachable and account connected — nothing to show
  if (status.daemon_reachable && status.connected) return null;

  const handleReconnect = async () => {
    setReconnecting(true);
    setError(null);
    try {
      await ipc.connectDaemon();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setReconnecting(false);
    }
  };

  // Daemon not reachable at all — socket connection failed
  if (!status.daemon_reachable) {
    return (
      <div className="daemon-banner daemon-banner-error">
        <span className="daemon-banner-icon">&#x25CB;</span>
        <div className="daemon-banner-text">
          <span>
            Cannot reach daemon. Make sure{" "}
            <code>cloud-drive-sync start</code> is running.
          </span>
          {error && (
            <span className="daemon-banner-detail">{error}</span>
          )}
        </div>
        <button
          className="btn btn-sm btn-primary"
          onClick={handleReconnect}
          disabled={reconnecting}
        >
          {reconnecting ? "Connecting..." : "Reconnect"}
        </button>
      </div>
    );
  }

  // Daemon reachable but no cloud account authenticated
  return (
    <div className="daemon-banner daemon-banner-auth">
      <span className="daemon-banner-icon">&#x26A0;</span>
      <div className="daemon-banner-text">
        <span>
          Daemon is running but no cloud account is connected. Go to the{" "}
          <NavLink to="/account">Account</NavLink> tab to add one.
        </span>
        {status.error && (
          <span className="daemon-banner-detail">{status.error}</span>
        )}
      </div>
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <div className="app-layout">
        <NavBar />
        <div className="main-wrapper">
          <DaemonBanner />
          <main className="main-content">
            <Routes>
              <Route path="/" element={<SyncStatus />} />
              <Route path="/settings" element={<Settings />} />
              <Route path="/conflicts" element={<ConflictDialog />} />
              <Route path="/transfers" element={<Transfers />} />
              <Route path="/activity" element={<ActivityLog />} />
              <Route path="/account" element={<AccountManager />} />
            </Routes>
          </main>
        </div>
      </div>
    </BrowserRouter>
  );
}
