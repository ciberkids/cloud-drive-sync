import { invoke } from "@tauri-apps/api/core";
import type {
  Account,
  DaemonStatus,
  SyncPair,
  ConflictRecord,
  LogEntry,
  ConflictResolution,
} from "./types";

export async function getStatus(): Promise<DaemonStatus> {
  return invoke<DaemonStatus>("get_status");
}

export async function getSyncPairs(): Promise<SyncPair[]> {
  return invoke<SyncPair[]>("get_sync_pairs");
}

export async function addSyncPair(
  localPath: string,
  remoteFolderId: string,
  ignoreHidden?: boolean
): Promise<SyncPair> {
  return invoke<SyncPair>("add_sync_pair", {
    localPath,
    remoteFolderId,
    ignoreHidden,
  });
}

export async function removeSyncPair(pairId: string): Promise<void> {
  return invoke("remove_sync_pair", { pairId });
}

export async function setConflictStrategy(strategy: string): Promise<void> {
  return invoke("set_conflict_strategy", { strategy });
}

export async function resolveConflict(
  conflictId: string,
  resolution: ConflictResolution
): Promise<void> {
  return invoke("resolve_conflict", { conflictId, resolution });
}

export async function forceSync(pairId?: string): Promise<void> {
  return invoke("force_sync", { pairId });
}

export async function pauseSync(pairId?: string): Promise<void> {
  return invoke("pause_sync", { pairId });
}

export async function resumeSync(pairId?: string): Promise<void> {
  return invoke("resume_sync", { pairId });
}

export async function getActivityLog(
  limit: number,
  offset: number
): Promise<LogEntry[]> {
  return invoke<LogEntry[]>("get_activity_log", { limit, offset });
}

export async function getConflicts(): Promise<ConflictRecord[]> {
  return invoke<ConflictRecord[]>("get_conflicts");
}

export async function startAuth(): Promise<unknown> {
  return invoke("start_auth");
}

export async function logout(): Promise<void> {
  return invoke("logout");
}

export async function connectDaemon(): Promise<void> {
  return invoke("connect_daemon");
}

export async function setSyncMode(
  pairId: string,
  syncMode: string
): Promise<void> {
  return invoke("set_sync_mode", { pairId, syncMode });
}

export async function setIgnoreHidden(
  pairId: string,
  ignoreHidden: boolean
): Promise<void> {
  return invoke("set_ignore_hidden", { pairId, ignoreHidden });
}

export async function setIgnorePatterns(
  pairId: string,
  patterns: string[]
): Promise<void> {
  return invoke("set_ignore_patterns", { pairId, patterns });
}

export async function addAccount(): Promise<unknown> {
  return invoke("add_account");
}

export async function removeAccount(email: string): Promise<void> {
  return invoke("remove_account", { email });
}

export async function listAccounts(): Promise<Account[]> {
  return invoke<Account[]>("list_accounts");
}

export async function setNotificationPrefs(prefs: {
  notify_sync_complete?: boolean;
  notify_conflicts?: boolean;
  notify_errors?: boolean;
}): Promise<{
  notify_sync_complete: boolean;
  notify_conflicts: boolean;
  notify_errors: boolean;
}> {
  return invoke("set_notification_prefs", {
    notifySyncComplete: prefs.notify_sync_complete,
    notifyConflicts: prefs.notify_conflicts,
    notifyErrors: prefs.notify_errors,
  });
}

export async function getNotificationPrefs(): Promise<{
  notify_sync_complete: boolean;
  notify_conflicts: boolean;
  notify_errors: boolean;
}> {
  return invoke("get_notification_prefs");
}

export async function listRemoteFolders(
  parentId: string
): Promise<{
  folders: Array<{ id: string; name: string }>;
  shared_drives?: Array<{ id: string; name: string }>;
  parent_id: string;
  error?: string;
}> {
  return invoke("list_remote_folders", { parentId });
}
