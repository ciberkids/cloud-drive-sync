import { invoke } from "@tauri-apps/api/core";
import type {
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
  remoteFolderId: string
): Promise<SyncPair> {
  return invoke<SyncPair>("add_sync_pair", {
    localPath,
    remoteFolderId,
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

export async function forceSync(): Promise<void> {
  return invoke("force_sync");
}

export async function pauseSync(): Promise<void> {
  return invoke("pause_sync");
}

export async function resumeSync(): Promise<void> {
  return invoke("resume_sync");
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
