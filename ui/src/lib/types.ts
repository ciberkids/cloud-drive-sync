export type ConflictStrategy = "keep_both" | "newest_wins" | "ask_user" | "local_wins" | "remote_wins";

export interface DaemonInfo {
  pid: number | null;
  uptime: number | null;
  uptime_formatted: string | null;
  socket_path: string | null;
  version: string | null;
  started_at: string | null;
  build_date: string | null;
}

export interface LiveTransfer {
  pair_id: string;
  path: string;
  direction: "upload" | "download" | "mkdir" | "delete_local" | "delete_remote";
  bytes: number;
  total: number;
  speed: number;
  speed_formatted: string;
}

export interface PairCount {
  pair_id: string;
  files_synced: number;
  account_id: string;
  provider: string;
  local_path: string;
}

export interface SyncCompleteFiles {
  uploaded: string[];
  downloaded: string[];
  deleted: string[];
  conflicted: string[];
}

export interface DaemonStatus {
  connected: boolean;       // cloud account authenticated
  daemon_reachable: boolean; // socket connection to daemon works
  syncing: boolean;
  paused: boolean;
  error: string | null;
  last_sync: string | null;
  files_synced: number;
  pair_counts: PairCount[];
  active_transfers: number;
  live_transfers: LiveTransfer[];
  daemon: DaemonInfo | null;
  conflict_strategy?: ConflictStrategy;
}

export type SyncMode = "two_way" | "upload_only" | "download_only";

export interface SyncPair {
  id: string;
  local_path: string;
  remote_folder_id: string;
  enabled: boolean;
  sync_mode: SyncMode;
  ignore_hidden: boolean;
  ignore_patterns?: string[];
  account_id?: string;
  provider?: string;
  conflict_strategy?: ConflictStrategy | "";
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
  event_type: "upload" | "download" | "delete" | "sync" | "conflict" | "error" | "auth" | "move";
  path: string;
  details: string;
  status: string;
  pair_id?: string;
  reason?: string;
}

export type ConflictResolution = "keep_local" | "keep_remote" | "keep_both";

export interface Account {
  email: string;
  display_name: string;
  status: "connected" | "disconnected";
  provider?: string;
  max_concurrent_transfers?: number;
}
