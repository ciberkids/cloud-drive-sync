import { useState, useEffect, useCallback, useRef } from "react";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import type {
  DaemonStatus,
  SyncPair,
  ConflictRecord,
  LogEntry,
} from "./types";
import * as ipc from "./ipc";

const DEFAULT_STATUS: DaemonStatus = {
  connected: false,
  syncing: false,
  paused: false,
  error: null,
  last_sync: null,
  files_synced: 0,
  active_transfers: 0,
  live_transfers: [],
  daemon: null,
};

export function useStatus(pollIntervalMs = 5000): DaemonStatus {
  const [status, setStatus] = useState<DaemonStatus>(DEFAULT_STATUS);

  useEffect(() => {
    let cancelled = false;

    const poll = async () => {
      try {
        const s = await ipc.getStatus();
        if (!cancelled) setStatus(s);
      } catch {
        if (!cancelled)
          setStatus((prev) => ({ ...prev, connected: false }));
      }
    };

    poll();
    const id = setInterval(poll, pollIntervalMs);

    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [pollIntervalMs]);

  // Listen for real-time status updates
  useEffect(() => {
    let unlisten: UnlistenFn | undefined;
    listen<Partial<DaemonStatus>>("daemon:status_changed", (event) => {
      setStatus((prev) => ({ ...prev, ...event.payload }));
    }).then((fn) => {
      unlisten = fn;
    });

    return () => {
      unlisten?.();
    };
  }, []);

  // Immediately re-poll when daemon connects (manual reconnect or auto-connect)
  useEffect(() => {
    let unlisten: UnlistenFn | undefined;
    listen("daemon-connected", async () => {
      try {
        const s = await ipc.getStatus();
        setStatus(s);
      } catch {
        // ignore
      }
    }).then((fn) => {
      unlisten = fn;
    });

    return () => {
      unlisten?.();
    };
  }, []);

  return status;
}

export function useSyncPairs() {
  const [pairs, setPairs] = useState<SyncPair[]>([]);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const result = await ipc.getSyncPairs();
      setPairs(result);
    } catch {
      // Daemon may not be connected yet
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Re-fetch when daemon connects (initial load may race with connection)
  useEffect(() => {
    let unlisten: UnlistenFn | undefined;
    listen("daemon-connected", () => {
      refresh();
    }).then((fn) => {
      unlisten = fn;
    });

    return () => {
      unlisten?.();
    };
  }, [refresh]);

  const add = useCallback(
    async (localPath: string, remoteFolderId: string) => {
      const pair = await ipc.addSyncPair(localPath, remoteFolderId);
      setPairs((prev) => [...prev, pair]);
      return pair;
    },
    []
  );

  const remove = useCallback(async (id: string) => {
    await ipc.removeSyncPair(id);
    setPairs((prev) => prev.filter((p) => p.id !== id));
  }, []);

  return { pairs, loading, refresh, add, remove };
}

export function useConflicts() {
  const [conflicts, setConflicts] = useState<ConflictRecord[]>([]);

  const refresh = useCallback(async () => {
    try {
      const result = await ipc.getConflicts();
      setConflicts(result);
    } catch {
      // ignore
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Re-fetch when daemon connects
  useEffect(() => {
    let unlisten: UnlistenFn | undefined;
    listen("daemon-connected", () => {
      refresh();
    }).then((fn) => {
      unlisten = fn;
    });

    return () => {
      unlisten?.();
    };
  }, [refresh]);

  useEffect(() => {
    let unlisten: UnlistenFn | undefined;
    listen("daemon:conflict_detected", () => {
      refresh();
    }).then((fn) => {
      unlisten = fn;
    });

    return () => {
      unlisten?.();
    };
  }, [refresh]);

  return { conflicts, refresh };
}

export function useActivityLog(limit = 50) {
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const offsetRef = useRef(0);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await ipc.getActivityLog(limit, 0);
      setEntries(result);
      offsetRef.current = result.length;
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, [limit]);

  const loadMore = useCallback(async () => {
    setLoading(true);
    try {
      const result = await ipc.getActivityLog(limit, offsetRef.current);
      setEntries((prev) => [...prev, ...result]);
      offsetRef.current += result.length;
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, [limit]);

  useEffect(() => {
    load();
  }, [load]);

  // Re-fetch when daemon connects
  useEffect(() => {
    let unlisten: UnlistenFn | undefined;
    listen("daemon-connected", () => {
      load();
    }).then((fn) => {
      unlisten = fn;
    });

    return () => {
      unlisten?.();
    };
  }, [load]);

  // Listen for new activity
  useEffect(() => {
    let unlisten: UnlistenFn | undefined;
    listen("daemon:sync_complete", () => {
      load();
    }).then((fn) => {
      unlisten = fn;
    });

    return () => {
      unlisten?.();
    };
  }, [load]);

  return { entries, loading, refresh: load, loadMore };
}

export function useDaemonEvent<T>(
  eventName: string,
  callback: (payload: T) => void
) {
  const callbackRef = useRef(callback);
  callbackRef.current = callback;

  useEffect(() => {
    let unlisten: UnlistenFn | undefined;
    listen<T>(eventName, (event) => {
      callbackRef.current(event.payload);
    }).then((fn) => {
      unlisten = fn;
    });

    return () => {
      unlisten?.();
    };
  }, [eventName]);
}
