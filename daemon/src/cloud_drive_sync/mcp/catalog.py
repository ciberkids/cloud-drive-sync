"""Tool catalogue for the MCP server.

Deliberately free of any ``mcp`` SDK import. The SDK is an optional extra, and
CI installs only ``.[dev]`` — a catalogue that imported it could only be tested
where the extra happens to be present. Everything that decides *what* the AI can
do and *how it is dispatched* lives here and is testable everywhere;
``mcp/server.py`` holds the transport wiring that genuinely needs the SDK.

Tools dispatch to ``RequestHandler``, the same JSON-RPC backend the IPC socket
and the HTTP/REST front-end use, so an agent cannot reach behaviour the CLI and
web UI do not already have.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cloud_drive_sync.ipc.protocol import JsonRpcRequest
from cloud_drive_sync.util.logging import get_logger

log = get_logger("mcp.catalog")

SYNC_MODES = ["two_way", "upload_only", "download_only"]
CONFLICT_STRATEGIES = ["keep_both", "newest_wins", "ask_user"]
RESOLUTIONS = ["keep_local", "keep_remote", "keep_both"]
ACTIVITY_FILTERS = ["all", "error", "upload", "download", "delete", "conflict", "sync", "auth", "move"]

# Handler methods intentionally NOT exposed as tools, at any permission level.
# Kept as data so the exclusions are visible and testable rather than implied by
# absence from the table below.
NEVER_EXPOSED: dict[str, str] = {
    "shutdown": "stopping the daemon is never a useful agent action",
    "start_auth": "interactive OAuth flow; the browser step cannot be a tool call",
    "exchange_auth_code": "takes a live OAuth authorization code — a secret",
    "logout": "legacy single-account credential wipe; remove_account supersedes it",
    "get_proxy": "proxy URLs can embed credentials (http://user:pass@host)",
    "set_proxy": "would let an agent route all traffic through a host it chose",
    "get_notification_prefs": "desktop-only concern, no agent value",
    "set_notification_prefs": "desktop-only concern, no agent value",
    "list_local_dirs": "enumerates the host filesystem beyond synced state",
    "resolve_pending_deletions": (
        "the delete fail-safe exists to put a human in the loop; letting an "
        "assistant approve a refused mass deletion defeats its entire purpose"
    ),
    "mkdir_local": "creates directories anywhere the daemon can write",
}


def _schema(properties: dict[str, Any] | None = None, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
        "additionalProperties": False,
    }


_PAIR_ID = {"type": "string", "description": "Sync pair id, e.g. 'pair_0'. Defaults to the first pair."}


@dataclass(frozen=True)
class McpTool:
    """One exposed tool, mapped onto a RequestHandler JSON-RPC method."""

    name: str
    method: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=_schema)
    writes: bool = False


READ_TOOLS: tuple[McpTool, ...] = (
    McpTool(
        "get_status",
        "get_status",
        "Overall sync health: whether the daemon is connected, syncing or paused, the "
        "current error if any, last sync time, total files synced, per-pair counts, "
        "active transfers, and daemon version and uptime. Start here.",
    ),
    McpTool(
        "list_sync_pairs",
        "get_sync_pairs",
        "All configured sync pairs with their local path, remote folder, provider, "
        "account, sync mode and conflict strategy.",
    ),
    McpTool(
        "list_accounts",
        "list_accounts",
        "Connected cloud accounts: email, provider, and per-account transfer limits. "
        "Does not return tokens or credentials.",
    ),
    McpTool(
        "get_activity_log",
        "get_activity_log",
        "Recent sync activity, newest first. Use filter='error' to investigate "
        "failures. Entries older than the retention window are pruned.",
        _schema({
            "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
            "offset": {"type": "integer", "minimum": 0, "default": 0},
            "filter": {"type": "string", "enum": ACTIVITY_FILTERS, "default": "all"},
            "pair_id": _PAIR_ID,
        }),
    ),
    McpTool(
        "list_conflicts",
        "get_conflicts",
        "Unresolved sync conflicts awaiting a decision. Each has a conflict_id "
        "usable with resolve_conflict.",
        _schema({"pair_id": _PAIR_ID}),
    ),
    McpTool(
        "get_sync_rules",
        "get_sync_rules",
        "Include/exclude rules for a sync pair (patterns, size limits, hidden-file handling).",
        _schema({"pair_id": _PAIR_ID}),
    ),
    McpTool(
        "get_bandwidth_limits",
        "get_bandwidth_limits",
        "Current upload and download throttles in KB/s. 0 means unlimited.",
    ),
    McpTool(
        "get_file_status",
        "get_file_status",
        "Sync state of one file: whether it is synced, pending, conflicted or errored, "
        "with its hashes and last-sync time. Use for 'why has this file not synced?'.",
        _schema({"path": {"type": "string", "description": "Path relative to the pair's local root."}}, ["path"]),
    ),
    McpTool(
        "list_pending_deletions",
        "get_pending_deletions",
        "Deletion batches the delete fail-safe refused because they looked like "
        "accidental mass deletion. Each blocks its pair until a human approves or "
        "rejects it. Report the counts and sample paths and let the user decide — "
        "do not approve on their behalf.",
        _schema({"pair_id": _PAIR_ID}),
    ),
    McpTool(
        "get_stop_state",
        "get_stop_state",
        "Whether activity is halted by an emergency stop, application-wide and per "
        "account. A stopped daemon looks idle otherwise, so check this before "
        "concluding that sync is simply up to date.",
    ),
    McpTool(
        "get_delete_failsafe_limit",
        "get_max_deletions",
        "The delete fail-safe limits: the global maximum deletions per sync pass "
        "and any per-pair overrides. 0 means the guard is disabled.",
    ),
    McpTool(
        "list_remote_folders",
        "list_remote_folders",
        "Browse folders in the cloud account, for choosing a remote target. "
        "Omit parent_id to list the account root.",
        _schema({
            "account_id": {"type": "string", "description": "Account email. Defaults to the only account."},
            "parent_id": {"type": "string", "description": "Remote folder id to list children of."},
        }),
    ),
)

WRITE_TOOLS: tuple[McpTool, ...] = (
    McpTool(
        "force_sync",
        "force_sync",
        "Trigger an immediate sync pass instead of waiting for the poll interval.",
        _schema({"pair_id": _PAIR_ID}),
        writes=True,
    ),
    McpTool(
        "pause_sync",
        "pause_sync",
        "Pause syncing for all pairs. In-flight transfers finish; nothing new starts "
        "until resume_sync. Local file changes are still detected and queued.",
        writes=True,
    ),
    McpTool(
        "resume_sync",
        "resume_sync",
        "Resume syncing after pause_sync. Anything that changed while paused is "
        "picked up on the next pass; call force_sync to start it immediately.",
        writes=True,
    ),
    McpTool(
        "resolve_conflict",
        "resolve_conflict",
        "Resolve a conflict from list_conflicts. keep_local uploads the local copy, "
        "keep_remote downloads the remote copy, keep_both renames the local copy and "
        "then downloads the remote one.",
        _schema({
            "conflict_id": {"type": "integer"},
            "resolution": {"type": "string", "enum": RESOLUTIONS},
        }, ["conflict_id", "resolution"]),
        writes=True,
    ),
    McpTool(
        "add_sync_pair",
        "add_sync_pair",
        "Create a sync pair between a local directory and a remote folder. "
        "The local path must already exist.",
        _schema({
            "local_path": {"type": "string", "description": "Absolute path to an existing local directory."},
            "remote_folder_id": {"type": "string", "description": "Remote folder id, or 'root'."},
            "provider": {"type": "string", "description": "gdrive, nextcloud, dropbox, onedrive or box."},
            "account_id": {"type": "string", "description": "Account email to sync with."},
            "sync_mode": {"type": "string", "enum": SYNC_MODES, "default": "two_way"},
            "ignore_hidden": {"type": "boolean", "default": True},
            "ignore_patterns": {"type": "array", "items": {"type": "string"}},
        }, ["local_path"]),
        writes=True,
    ),
    McpTool(
        "remove_sync_pair",
        "remove_sync_pair",
        "Stop syncing a pair and delete its configuration. Does not delete any files, "
        "locally or remotely.",
        _schema({"id": {"type": "string", "description": "Pair id, e.g. 'pair_0'."}}, ["id"]),
        writes=True,
    ),
    McpTool(
        "set_sync_mode",
        "set_sync_mode",
        "Change a pair's direction: two_way, upload_only or download_only.",
        _schema({"pair_id": _PAIR_ID, "sync_mode": {"type": "string", "enum": SYNC_MODES}}, ["sync_mode"]),
        writes=True,
    ),
    McpTool(
        "set_conflict_strategy",
        "set_conflict_strategy",
        "Set the global default conflict strategy for all pairs.",
        _schema({"strategy": {"type": "string", "enum": CONFLICT_STRATEGIES}}, ["strategy"]),
        writes=True,
    ),
    McpTool(
        "set_pair_conflict_strategy",
        "set_pair_conflict_strategy",
        "Override the conflict strategy for one pair.",
        _schema({"pair_id": _PAIR_ID, "strategy": {"type": "string", "enum": CONFLICT_STRATEGIES}}, ["strategy"]),
        writes=True,
    ),
    McpTool(
        "set_ignore_hidden",
        "set_ignore_hidden",
        "Include or exclude dotfiles and hidden directories for a pair.",
        _schema({"pair_id": _PAIR_ID, "ignore_hidden": {"type": "boolean"}}, ["ignore_hidden"]),
        writes=True,
    ),
    McpTool(
        "set_ignore_patterns",
        "set_ignore_patterns",
        "Replace a pair's ignore patterns (gitignore-style globs). Sends the whole list.",
        _schema({
            "pair_id": _PAIR_ID,
            "patterns": {"type": "array", "items": {"type": "string"}},
        }, ["patterns"]),
        writes=True,
    ),
    McpTool(
        "set_bandwidth_limits",
        "set_bandwidth_limits",
        "Throttle transfers in KB/s. 0 means unlimited.",
        _schema({
            "max_upload_kbps": {"type": "integer", "minimum": 0},
            "max_download_kbps": {"type": "integer", "minimum": 0},
        }),
        writes=True,
    ),
    McpTool(
        "set_sync_rules",
        "set_sync_rules",
        "Replace a pair's include/exclude rules. Send the full rules object; "
        "read it with get_sync_rules first.",
        _schema({"pair_id": _PAIR_ID, "rules": {"type": "object"}}, ["rules"]),
        writes=True,
    ),
    McpTool(
        "create_remote_folder",
        "create_remote_folder",
        "Create a folder in the cloud account, for use as a sync target.",
        _schema({
            "name": {"type": "string"},
            "parent_id": {"type": "string", "description": "Parent folder id. Omit for the root."},
            "account_id": {"type": "string"},
        }, ["name"]),
        writes=True,
    ),
    McpTool(
        "set_account_max_transfers",
        "set_account_max_transfers",
        "Cap concurrent transfers for one account.",
        _schema({
            "email": {"type": "string"},
            "max_concurrent_transfers": {"type": "integer", "minimum": 1},
        }, ["email", "max_concurrent_transfers"]),
        writes=True,
    ),
    McpTool(
        "emergency_stop",
        "emergency_stop",
        "Halt all sync activity immediately, cancelling in-flight transfers. Pass "
        "account_id to stop one account, or omit it to stop everything. Persists "
        "across restart. Use when something is actively going wrong; prefer "
        "pause_sync for an orderly stop that lets transfers finish.",
        _schema({"account_id": {"type": "string", "description": "Account email. Omit for all."}}),
        writes=True,
    ),
    McpTool(
        "emergency_resume",
        "emergency_resume",
        "Resume activity after emergency_stop, for one account or everything. A "
        "per-account resume cannot override an application-wide stop.",
        _schema({"account_id": {"type": "string", "description": "Account email. Omit for all."}}),
        writes=True,
    ),
    McpTool(
        "set_delete_failsafe_limit",
        "set_max_deletions",
        "Change the maximum deletions allowed per sync pass, globally or for one "
        "pair. Raising it or setting 0 weakens protection against a wiped local "
        "disk propagating to the cloud, so confirm the user intends that.",
        _schema({
            "max_deletions_per_sync": {"type": "integer", "minimum": 0},
            "pair_id": _PAIR_ID,
        }, ["max_deletions_per_sync"]),
        writes=True,
    ),
    McpTool(
        "repair",
        "repair",
        "Find and optionally delete stale database entries for files that exist on "
        "neither side. Always call with dry_run=true first and report the count. "
        "Only touches database records, never files.",
        _schema({
            "pair_id": _PAIR_ID,
            "dry_run": {"type": "boolean", "default": True, "description": "Report without deleting."},
        }),
        writes=True,
    ),
    McpTool(
        "add_account",
        "add_account",
        "Begin connecting a cloud account. OAuth providers need an interactive "
        "browser step that cannot be completed through this tool — prefer the web "
        "UI or CLI for those.",
        _schema({
            "provider": {"type": "string"},
            "headless": {"type": "boolean", "default": True},
        }, ["provider"]),
        writes=True,
    ),
    McpTool(
        "remove_account",
        "remove_account",
        "Disconnect a cloud account and delete its stored credentials. Sync pairs "
        "using it stop working. Does not delete files.",
        _schema({"email": {"type": "string"}, "provider": {"type": "string"}}, ["email"]),
        writes=True,
    ),
)

ALL_TOOLS: tuple[McpTool, ...] = READ_TOOLS + WRITE_TOOLS


class ToolNotAvailable(Exception):
    """The tool does not exist, or exists but writes are not enabled."""


def tools_for(allow_writes: bool) -> list[McpTool]:
    """Tools to advertise in ``tools/list``.

    Write tools are omitted entirely rather than advertised and refused: an agent
    that sees ``remove_account`` will call it, and a refusal it could not have
    predicted just burns turns.
    """
    return list(ALL_TOOLS if allow_writes else READ_TOOLS)


def lookup(name: str, allow_writes: bool) -> McpTool:
    for tool in tools_for(allow_writes):
        if tool.name == name:
            return tool
    known = {t.name for t in ALL_TOOLS}
    if name in known:
        raise ToolNotAvailable(
            f"Tool {name!r} modifies state and this server is read-only. "
            f"Restart the daemon with --mcp-allow-writes to enable it."
        )
    raise ToolNotAvailable(f"Unknown tool: {name!r}")


async def dispatch(handler: Any, name: str, arguments: dict | None, *, allow_writes: bool) -> Any:
    """Run a tool by delegating to ``RequestHandler``.

    Raises ``ToolNotAvailable`` for unknown or write-gated tools, and
    ``RuntimeError`` carrying the handler's message when the call itself fails.
    """
    tool = lookup(name, allow_writes)
    response = await handler.handle(
        JsonRpcRequest(id=1, method=tool.method, params=arguments or {})
    )
    if response.error:
        raise RuntimeError(response.error.message)
    return response.result
