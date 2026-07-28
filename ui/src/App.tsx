import { BrowserRouter, Routes, Route, NavLink } from "react-router-dom";
import { useState, useEffect } from "react";
import { listen } from "@tauri-apps/api/event";
import { SyncStatus } from "./components/SyncStatus";
import { Settings } from "./components/Settings";
import { ConflictDialog } from "./components/ConflictDialog";
import { ActivityLog } from "./components/ActivityLog";
import { Transfers } from "./components/Transfers";
import { AccountManager } from "./components/AccountManager";
import { About } from "./components/About";
import { CloudBridges } from "./components/CloudBridges";
import { useStatus } from "./lib/hooks";
import * as ipc from "./lib/ipc";
import type { PendingDeletion } from "./lib/types";

function ThemeToggle() {
  const [theme, setTheme] = useState<"dark" | "light">(() => {
    return (localStorage.getItem("theme") as "dark" | "light") || "dark";
  });

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("theme", theme);
  }, [theme]);

  return (
    <button
      className="theme-toggle"
      onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
      title={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
    >
      {theme === "dark" ? "\u2600" : "\u263E"}
    </button>
  );
}

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
        <ThemeToggle />
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
          <NavLink to="/bridges">Bridges</NavLink>
        </li>
        <li>
          <NavLink to="/account">Account</NavLink>
        </li>
        <li>
          <NavLink to="/about">About</NavLink>
        </li>
      </ul>
    </nav>
  );
}

/**
 * Delete fail-safe block (#53). Separate from DaemonBanner because it must show
 * even when the daemon is perfectly healthy — a paused pair with pending
 * deletions is not a connectivity problem, and the user has to decide before
 * anything syncs again.
 */
function DeleteBlockBanner() {
  const [pending, setPending] = useState<PendingDeletion[]>([]);
  const [busy, setBusy] = useState(false);

  const refresh = () => {
    ipc
      .getPendingDeletions()
      .then(setPending)
      .catch(() => {});
  };

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, []);

  if (pending.length === 0) return null;

  const total = pending.reduce((n, p) => n + p.count, 0);
  const pairIds = [...new Set(pending.map((p) => p.pair_id))];
  const sample = pending.flatMap((p) => p.sample).slice(0, 5);

  const decide = async (approve: boolean) => {
    setBusy(true);
    try {
      for (const pairId of pairIds) {
        await ipc.resolvePendingDeletions(pairId, approve);
      }
      setPending([]);
    } catch (e) {
      console.error("Failed to resolve pending deletions:", e);
      refresh();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="daemon-banner daemon-banner-error">
      <span className="daemon-banner-icon">&#x26A0;</span>
      <div className="daemon-banner-text">
        <span>
          <strong>Sync paused — {total} deletions blocked.</strong>{" "}
          {pending
            .map(
              (p) =>
                `${p.count} ${p.direction} file${p.count === 1 ? "" : "s"}` +
                (p.tracked ? ` (${Math.round((p.count / p.tracked) * 100)}% of tracked)` : "")
            )
            .join(", ")}{" "}
          on {pairIds.join(", ")}. Nothing has been deleted yet.
        </span>
        {sample.length > 0 && (
          <span className="daemon-banner-detail">
            e.g. {sample.join(", ")}
            {total > sample.length ? ` … and ${total - sample.length} more` : ""}
          </span>
        )}
      </div>
      <button className="btn btn-danger" disabled={busy} onClick={() => decide(true)}>
        Delete them
      </button>
      <button className="btn" disabled={busy} onClick={() => decide(false)}>
        Keep files
      </button>
    </div>
  );
}

function DaemonBanner() {
  const status = useStatus();
  const [reconnecting, setReconnecting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);

  // Listen for daemon status messages from the Rust backend
  useEffect(() => {
    let unlisten: (() => void) | undefined;
    listen<string>("daemon-status-msg", (event) => {
      setStatusMsg(event.payload);
    }).then((fn) => {
      unlisten = fn;
    });
    return () => {
      unlisten?.();
    };
  }, []);

  // Clear status message once connected
  useEffect(() => {
    if (status.daemon_reachable && status.connected) {
      // Keep message visible briefly, then clear
      const timer = setTimeout(() => setStatusMsg(null), 2000);
      return () => clearTimeout(timer);
    }
  }, [status.daemon_reachable, status.connected]);

  // Show startup status message (connecting, starting daemon, etc.)
  if (statusMsg && !status.daemon_reachable) {
    return (
      <div className="daemon-banner daemon-banner-info">
        <span className="daemon-banner-icon">&#x231B;</span>
        <div className="daemon-banner-text">
          <span>{statusMsg}</span>
        </div>
      </div>
    );
  }

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
          <DeleteBlockBanner />
          <DaemonBanner />
          <main className="main-content">
            <Routes>
              <Route path="/" element={<SyncStatus />} />
              <Route path="/settings" element={<Settings />} />
              <Route path="/conflicts" element={<ConflictDialog />} />
              <Route path="/transfers" element={<Transfers />} />
              <Route path="/activity" element={<ActivityLog />} />
              <Route path="/bridges" element={<CloudBridges />} />
              <Route path="/account" element={<AccountManager />} />
              <Route path="/about" element={<About />} />
            </Routes>
          </main>
        </div>
      </div>
    </BrowserRouter>
  );
}
