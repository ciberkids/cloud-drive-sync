import { BrowserRouter, Routes, Route, NavLink } from "react-router-dom";
import { SyncStatus } from "./components/SyncStatus";
import { Settings } from "./components/Settings";
import { ConflictDialog } from "./components/ConflictDialog";
import { ActivityLog } from "./components/ActivityLog";
import { AccountManager } from "./components/AccountManager";
import { useStatus } from "./lib/hooks";

function NavBar() {
  const status = useStatus();
  return (
    <nav className="sidebar">
      <div className="sidebar-header">
        <h1>GDrive Sync</h1>
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
          <NavLink to="/activity">Activity</NavLink>
        </li>
        <li>
          <NavLink to="/account">Account</NavLink>
        </li>
      </ul>
    </nav>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <div className="app-layout">
        <NavBar />
        <main className="main-content">
          <Routes>
            <Route path="/" element={<SyncStatus />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="/conflicts" element={<ConflictDialog />} />
            <Route path="/activity" element={<ActivityLog />} />
            <Route path="/account" element={<AccountManager />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}
