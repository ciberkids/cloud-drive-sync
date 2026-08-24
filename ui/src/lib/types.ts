export type ConflictStrategy = "keep_both" | "newest_wins" | "ask_user" | "local_wins" | "remote_wins";

export interface PendingDeletion {
  pair_id: string;
  direction: "local" | "remote";
  count: number;
  tracked: number;
  limit: number;
  sample: string[];
  created_at: string;
}

export interface StopState {
  /** Application-wide emergency stop. */
  stopped: boolean;
  /** Per-account stops, keyed by email. */
  accounts: Record<string, boolean>;
}

export interface DeleteFailsafeLimits {
  max_deletions_per_sync: number;
  /** Sliding window the limit is counted over. 0 = per sync pass only. */
  deletion_window_seconds: number;
  /** Per-pair overrides, keyed by pair index string ("0", "1") as get_sync_pairs returns. */
  pairs: Record<string, number | null>;
  pair_windows: Record<string, number | null>;
}

export interface DatabaseInfo {
  size_bytes: number;
  size_formatted: string;
  reclaimable_bytes: number;
  reclaimable_formatted: string;
  /** Fraction of the file that is free pages, 0..1. High on a large file = mostly dead space. */
  reclaimable_ratio: number;
  page_count: number;
  freelist_count: number;
}

export interface DaemonInfo {
  pid: number | null;
  uptime: number | null;
  uptime_formatted: string | null;
  socket_path: string | null;
  version: string | null;
  started_at: string | null;
  build_date: string | null;
  database?: DatabaseInfo | null;
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
  /** How this pair learns about remote changes — "push (notify_push)" or a polling variant. */
  change_detection?: string;
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
  /** Positional index as a string ("0", "1"), as get_sync_pairs returns it. */
  id: string;
  /** Stable per-pair identity. Optional: a daemon predating it omits the field. */
  uid?: string;
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

/**
 * What the sign-in screen needs in order to render, and nothing else.
 *
 * `auth` is three-valued rather than a boolean because there are three ways in:
 * nothing configured, an access token (the pre-account state), and an account.
 * The desktop build always sees "none" — it talks to the daemon over a Unix
 * socket, so the HTTP port's authentication does not apply to it at all.
 */
export interface AuthSession {
  auth: "none" | "token" | "user";
  /** A token is configured and no account exists yet, so one can be created. */
  setup_available: boolean;
  authenticated: boolean;
  username: string | null;
}
