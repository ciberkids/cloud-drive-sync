/**
 * Mock IPC data for demo/screenshot mode.
 * Used when Tauri runtime is not available (e.g., headless browser captures).
 */

import type {
  Account,
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
  active_transfers: 0,
  live_transfers: [],
  daemon: {
    pid: 12345,
    uptime: 86400,
    uptime_formatted: "1d 0h 0m",
    socket_path: "/run/user/1000/cloud-drive-sync.sock",
  },
};

const DEMO_PAIRS: SyncPair[] = [
  {
    id: "pair_0",
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
    id: "pair_1",
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
    id: "pair_2",
    local_path: "/home/user/Work",
    remote_folder_id: "root",
    enabled: true,
    sync_mode: "two_way",
    ignore_hidden: false,
    ignore_patterns: [".git", "build/"],
    account_id: "bob@company.com",
    provider: "dropbox",
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
export async function getActivityLog(limit: number): Promise<LogEntry[]> { return DEMO_LOG.slice(0, limit); }
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
