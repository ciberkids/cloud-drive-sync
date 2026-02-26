export type ConflictStrategy = "keep_both" | "newest_wins" | "ask_user";

export interface DaemonStatus {
  connected: boolean;
  syncing: boolean;
  paused: boolean;
  error: string | null;
  last_sync: string | null;
  files_synced: number;
  active_transfers: number;
}

export interface SyncPair {
  id: string;
  local_path: string;
  remote_folder_id: string;
  enabled: boolean;
}

export interface ConflictRecord {
  id: string;
  path: string;
  local_mtime: string;
  remote_mtime: string;
  local_size: number;
  remote_size: number;
  detected_at: string;
}

export interface LogEntry {
  id: number;
  timestamp: string;
  event_type: "upload" | "download" | "delete" | "conflict" | "error";
  path: string;
  details: string;
  status: string;
}

export type ConflictResolution = "keep_local" | "keep_remote" | "keep_both";
