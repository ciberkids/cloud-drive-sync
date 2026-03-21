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
  return (
    <nav className="sidebar">
      <div className="sidebar-header">
        <h1>Cloud Drive Sync</h1>
        <span className={`connection-dot ${status.connected ? "connected" : "disconnected"}`} />
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

  if (status.connected) return null;

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

  return (
    <div className="daemon-banner">
      <span className="daemon-banner-icon">&#x25CB;</span>
      <span>Daemon not connected. Make sure <code>cloud-drive-sync-daemon start</code> is running.</span>
      {error && <span className="daemon-banner-error">{error}</span>}
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
