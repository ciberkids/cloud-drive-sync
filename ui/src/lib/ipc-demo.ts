/**
 * Mock IPC data for demo/screenshot mode.
 * Used when Tauri runtime is not available (e.g., headless browser captures).
 */

import type {
  Account,
  AuthSession,
  DaemonStatus,
  SyncPair,
  ConflictRecord,
  LogEntry,
} from "./types";

const DEMO_STATUS: DaemonStatus = {
  connected: true,
  daemon_reachable: true,
  syncing: false,
  paused: false,
  error: null,
  last_sync: new Date().toISOString(),
  files_synced: 247,
  pair_counts: [
    { pair_id: "pair_0", files_synced: 148, account_id: "alice@gmail.com", provider: "gdrive", local_path: "/home/user/Documents" },
    { pair_id: "pair_1", files_synced: 63, account_id: "alice@gmail.com", provider: "gdrive", local_path: "/home/user/Photos" },
    { pair_id: "pair_2", files_synced: 36, account_id: "bob@company.com", provider: "dropbox", local_path: "/home/user/Work" },
    // Only Nextcloud reports a change-detection mechanism; the others poll a
    // provider changes API and have nothing interesting to say about it.
    { pair_id: "pair_3", files_synced: 36, account_id: "bob@nextcloud.example.com", provider: "nextcloud", local_path: "/home/user/Work", change_detection: "push (notify_push)" },
  ],
  active_transfers: 0,
  live_transfers: [],
  daemon: {
    pid: 12345,
    uptime: 86400,
    uptime_formatted: "1d 0h 0m",
    socket_path: "/run/user/1000/cloud-drive-sync.sock",
    version: "1.2.0",
    started_at: "2026-04-09 10:00",
    database: {
      size_bytes: 2_310_144,
      size_formatted: "2.2 MB",
      reclaimable_bytes: 172_032,
      reclaimable_formatted: "168.0 KB",
      reclaimable_ratio: 0.0745,
      page_count: 564,
      freelist_count: 42,
    },
    build_date: "2026-04-09",
  },
};

const DEMO_PAIRS: SyncPair[] = [
  {
    id: "0",
    local_path: "/home/user/Documents",
    remote_folder_id: "root",
    enabled: true,
    sync_mode: "two_way",
    ignore_hidden: true,
    ignore_patterns: ["node_modules", "*.tmp"],
    account_id: "alice@gmail.com",
    provider: "gdrive",
  },
  {
    id: "1",
    local_path: "/home/user/Photos",
    remote_folder_id: "1a2b3c4d5e",
    enabled: true,
    sync_mode: "upload_only",
    ignore_hidden: true,
    ignore_patterns: [],
    account_id: "alice@gmail.com",
    provider: "gdrive",
  },
  {
    id: "2",
    local_path: "/home/user/Work",
    remote_folder_id: "root",
    enabled: true,
    sync_mode: "two_way",
    ignore_hidden: false,
    ignore_patterns: [".git", "build/"],
    account_id: "bob@company.com",
    provider: "dropbox",
  },
  {
    id: "3",
    local_path: "/home/user/Work",
    remote_folder_id: "/Work",
    enabled: true,
    sync_mode: "two_way",
    ignore_hidden: false,
    ignore_patterns: [".git", "build/"],
    account_id: "bob@nextcloud.example.com",
    provider: "nextcloud",
  },
];

const DEMO_ACCOUNTS: Account[] = [
  {
    email: "alice@gmail.com",
    display_name: "Alice",
    status: "connected",
    provider: "gdrive",
    max_concurrent_transfers: 4,
  },
  {
    email: "bob@company.com",
    display_name: "Bob",
    status: "connected",
    provider: "dropbox",
    max_concurrent_transfers: 0,
  },
  {
    email: "bob@nextcloud.example.com",
    display_name: "Bob (Nextcloud)",
    status: "connected",
    provider: "nextcloud",
    max_concurrent_transfers: 0,
  },
];

const DEMO_CONFLICTS: ConflictRecord[] = [
  {
    id: "1",
    path: "Documents/report-q4.docx",
    local_mtime: "2026-03-22T14:30:00Z",
    remote_mtime: "2026-03-22T15:10:00Z",
    local_size: 245000,
    remote_size: 248000,
    detected_at: "2026-03-22T15:15:00Z",
  },
  {
    id: "2",
    path: "Photos/vacation/IMG_2024.jpg",
    local_mtime: "2026-03-21T09:00:00Z",
    remote_mtime: "2026-03-21T10:30:00Z",
    local_size: 4200000,
    remote_size: 4180000,
    detected_at: "2026-03-21T11:00:00Z",
  },
];

const DEMO_LOG: LogEntry[] = [
  { id: 1, timestamp: "2026-03-23T10:05:00Z", event_type: "upload", path: "Documents/notes.md", details: "12 KB at 1.2 MB/s", status: "success", pair_id: "pair_0" },
  { id: 2, timestamp: "2026-03-23T10:04:55Z", event_type: "download", path: "Documents/budget.xlsx", details: "89 KB at 2.1 MB/s", status: "success", pair_id: "pair_0" },
  { id: 3, timestamp: "2026-03-23T10:04:50Z", event_type: "upload", path: "Photos/screenshot.png", details: "340 KB at 1.8 MB/s", status: "success", pair_id: "pair_1" },
  { id: 4, timestamp: "2026-03-23T10:04:00Z", event_type: "sync", path: "", details: "Sync complete: 3 uploaded, 1 downloaded", status: "success", pair_id: "pair_0" },
  { id: 5, timestamp: "2026-03-23T10:03:00Z", event_type: "conflict", path: "Documents/report-q4.docx", details: "Both sides modified", status: "error", pair_id: "pair_0" },
  { id: 6, timestamp: "2026-03-23T09:58:00Z", event_type: "download", path: "Work/presentation.pptx", details: "2.1 MB at 3.4 MB/s", status: "success", pair_id: "pair_2" },
  { id: 7, timestamp: "2026-03-23T09:55:00Z", event_type: "upload", path: "Work/src/main.py", details: "4 KB at 800 KB/s", status: "success", pair_id: "pair_2" },
  { id: 8, timestamp: "2026-03-23T09:50:00Z", event_type: "sync", path: "", details: "Automatic sync started — scanning local and remote files", status: "success", pair_id: "pair_2" },
  { id: 9, timestamp: "2026-03-23T09:48:00Z", event_type: "delete", path: "Documents/old-draft.txt", details: "Local file deleted: removed from remote", status: "success", pair_id: "pair_0" },
  { id: 10, timestamp: "2026-03-23T09:45:00Z", event_type: "delete", path: "Photos/thumb_001.jpg", details: "Remote file deleted: removed locally", status: "success", pair_id: "pair_1" },
  { id: 11, timestamp: "2026-03-23T09:40:00Z", event_type: "upload", path: "Work/data.csv", details: "Network error: connection reset", status: "error", pair_id: "pair_2" },
  { id: 12, timestamp: "2026-03-23T09:35:00Z", event_type: "auth", path: "", details: "Token refreshed", status: "success", pair_id: "pair_0" },
  { id: 13, timestamp: "2026-03-23T09:30:00Z", event_type: "move", path: "Documents/project-v1.docx", details: "Renamed → project-final.docx", status: "success", pair_id: "pair_0" },
  { id: 14, timestamp: "2026-03-23T09:25:00Z", event_type: "move", path: "Work/archive/report.pdf", details: "Moved → Work/current/report.pdf", status: "success", pair_id: "pair_2" },
];

export async function getStatus(): Promise<DaemonStatus> { return DEMO_STATUS; }
export async function getSyncPairs(): Promise<SyncPair[]> { return DEMO_PAIRS; }
export async function addSyncPair() { return DEMO_PAIRS[0]; }
export async function removeSyncPair() {}
export async function setConflictStrategy() {}
export async function resolveConflict() {}
export async function forceSync() {}
export async function pauseSync() {}
export async function resumeSync() {}
export async function getActivityLog(limit: number, offset = 0, filter = "all"): Promise<LogEntry[]> {
  const filtered = filter === "all" ? DEMO_LOG
    : filter === "error" ? DEMO_LOG.filter(e => e.status === "error")
    : DEMO_LOG.filter(e => e.event_type === filter);
  return filtered.slice(offset, offset + limit);
}
export async function getConflicts(): Promise<ConflictRecord[]> { return DEMO_CONFLICTS; }
export async function startAuth() { return {}; }
export async function logout() {}
export async function connectDaemon() {}
export async function setSyncMode() {}
export async function setIgnoreHidden() {}
export async function setIgnorePatterns() {}
export async function addAccount() { return { status: "ok" }; }
export async function removeAccount() {}
export async function listAccounts(): Promise<Account[]> { return DEMO_ACCOUNTS; }
export async function setNotificationPrefs() { return { notify_sync_complete: true, notify_conflicts: true, notify_errors: true }; }
export async function getNotificationPrefs() { return { notify_sync_complete: true, notify_conflicts: true, notify_errors: true }; }
export async function setBandwidthLimits() { return { max_upload_kbps: 0, max_download_kbps: 0 }; }
export async function getBandwidthLimits() { return { max_upload_kbps: 0, max_download_kbps: 0 }; }
export async function setSyncRules() { return {}; }
export async function getSyncRules() { return { max_file_size_mb: 0, include_regex: [], exclude_regex: [], min_date: "" }; }
export async function setProxy() { return { http_proxy: "", https_proxy: "", no_proxy: "" }; }
export async function getProxy() { return { http_proxy: "", https_proxy: "", no_proxy: "" }; }
export async function listRemoteFolders() { return { folders: [{ id: "f1", name: "Documents" }, { id: "f2", name: "Photos" }, { id: "f3", name: "Work" }], parent_id: "root" }; }
export async function setAccountMaxTransfers() { return {}; }
export async function mkdirLocal() { return { ok: true }; }

// ── Delete fail-safe (#53) ──────────────────────────────────────────

export async function getMaxDeletions() {
  return {
    max_deletions_per_sync: 100,
    deletion_window_seconds: 60,
    pairs: { "0": null, "1": null },
    pair_windows: { "0": null, "1": null },
  };
}

export async function setMaxDeletions(
  _max: number | null,
  _pairId?: string,
  _window?: number
) {
  return { status: "ok" };
}

export async function getPendingDeletions() {
  // Empty in demo mode: the healthy state is what screenshots should show.
  return [];
}

export async function resolvePendingDeletions(_pairId: string, _approve: boolean) {
  return { status: "approved", batches: 1 };
}

// ── Emergency stop (#54) ────────────────────────────────────────────

let demoStopped = false;

export async function getStopState() {
  return { stopped: demoStopped, accounts: { "demo@example.com": false } };
}

export async function emergencyStop(_accountId?: string) {
  demoStopped = true;
  return { pairs_stopped: 2, operations_cancelled: 3 };
}

export async function emergencyResume(_accountId?: string) {
  demoStopped = false;
  return { pairs_resumed: 2 };
}

// ── Sign-in ─────────────────────────────────────────────────────────
//
// Demo mode renders the app with no gate, so screenshots of every other screen
// are unaffected by this feature. The sign-in views are captured from a real
// token-protected daemon instead -- a mocked login that always succeeds would be
// a screenshot of something that does not exist.

export async function getAuthSession(): Promise<AuthSession> {
  return { auth: "none", setup_available: false, authenticated: true, username: null };
}

export async function signIn(_username: string, _password: string): Promise<void> {}

export async function signInWithToken(_token: string): Promise<void> {}

export async function createAccount(
  _token: string,
  _username: string,
  _password: string
): Promise<void> {}

export async function signOut(): Promise<void> {}

export async function changePassword(_current: string, _next: string): Promise<void> {}
