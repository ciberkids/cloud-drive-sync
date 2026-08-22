"""CLI entry point using Click."""

from __future__ import annotations

import asyncio
import sys
from importlib.metadata import version as _pkg_version
from pathlib import Path

import click

from cloud_drive_sync.util.logging import get_logger

log = get_logger("cli")


def _get_version() -> str:
    try:
        return _pkg_version("cloud-drive-sync")
    except Exception:
        return "dev"


@click.group()
@click.version_option(version=_get_version(), prog_name="cloud-drive-sync-daemon")
@click.option("--config", "config_path", type=click.Path(path_type=Path), default=None, help="Path to config.toml")
@click.option("--log-level", type=click.Choice(["debug", "info", "warning", "error"], case_sensitive=False), default=None)
@click.pass_context
def cli(ctx: click.Context, config_path: Path | None, log_level: str | None) -> None:
    """cloud-drive-sync-daemon: bidirectional Google Drive sync for Linux."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config_path
    ctx.obj["log_level"] = log_level


@cli.command()
@click.option("--foreground", is_flag=True, help="Run in the foreground (don't daemonize)")
@click.option("--demo", is_flag=True, help="Run in demo mode with mock Google Drive (no credentials needed)")
@click.option("--http-port", type=int, default=0, help="Enable HTTP REST API on this port (0 = disabled)")
@click.option(
    "--http-host",
    default="0.0.0.0",
    envvar="CDS_HTTP_HOST",
    help="Address the HTTP server binds to. Use 127.0.0.1 to restrict it to this machine.",
)
@click.option(
    "--http-token",
    default=None,
    envvar="CDS_HTTP_TOKEN",
    help="Require this token on /api/* and the web UI. Without one the port is unauthenticated.",
)
@click.option(
    "--mcp-token",
    default=None,
    envvar="CDS_MCP_TOKEN",
    help="Require this bearer token on the MCP endpoint.",
)
@click.option(
    "--mcp-port",
    type=int,
    default=0,
    envvar="CDS_MCP_PORT",
    help="Enable the MCP server for AI assistants on this port (0 = disabled)",
)
@click.option(
    "--mcp-host",
    default="0.0.0.0",
    envvar="CDS_MCP_HOST",
    help="Address the MCP server binds to. Use 127.0.0.1 to restrict it to this machine.",
)
@click.option(
    "--mcp-allow-writes",
    is_flag=True,
    envvar="CDS_MCP_ALLOW_WRITES",
    help="Also expose tools that change state (add/remove pairs and accounts, force sync).",
)
@click.option(
    "--mcp-allowed-host",
    "mcp_allowed_hosts",
    multiple=True,
    envvar="CDS_MCP_ALLOWED_HOSTS",
    help="Host header the MCP server accepts, e.g. 'nas.local:*'. Repeatable. "
    "Defaults to localhost only; use '*' to accept any.",
)
@click.pass_context
def start(
    ctx: click.Context,
    foreground: bool,
    demo: bool,
    http_port: int,
    http_host: str,
    http_token: str | None,
    mcp_token: str | None,
    mcp_port: int,
    mcp_host: str,
    mcp_allow_writes: bool,
    mcp_allowed_hosts: tuple[str, ...],
) -> None:
    """Start the sync daemon."""
    from cloud_drive_sync.daemon import Daemon

    if Daemon.is_running():
        click.echo("Daemon is already running.", err=True)
        sys.exit(1)

    daemon = Daemon(
        config_path=ctx.obj["config_path"],
        log_level=ctx.obj["log_level"],
        demo=demo,
        http_port=http_port,
        http_host=http_host,
        http_token=http_token,
        mcp_token=mcp_token,
        mcp_port=mcp_port,
        mcp_host=mcp_host,
        mcp_allow_writes=mcp_allow_writes,
        mcp_allowed_hosts=mcp_allowed_hosts,
    )

    if foreground:
        click.echo("Starting in foreground...")
        asyncio.run(daemon.run())
    elif sys.platform == "win32":
        # Windows: no fork support, always run in foreground
        click.echo("Starting daemon...")
        asyncio.run(daemon.run())
    else:
        click.echo("Starting daemon...")
        # Unix fork-based daemonization
        import os

        pid = os.fork()
        if pid > 0:
            click.echo(f"Daemon started (PID {pid})")
            sys.exit(0)

        # Child: create new session
        os.setsid()
        # Redirect stdio
        devnull = os.open(os.devnull, os.O_RDWR)
        os.dup2(devnull, 0)
        os.dup2(devnull, 1)
        os.dup2(devnull, 2)
        os.close(devnull)

        asyncio.run(daemon.run())


@cli.command()
def stop() -> None:
    """Stop the running daemon."""
    from cloud_drive_sync.daemon import Daemon

    if Daemon.stop_running():
        click.echo("Stop signal sent.")
    else:
        click.echo("No running daemon found.", err=True)
        sys.exit(1)


@cli.command()
def status() -> None:
    """Check daemon status."""
    from cloud_drive_sync.daemon import Daemon

    if Daemon.is_running():
        from cloud_drive_sync.util.paths import pid_path

        pid = pid_path().read_text().strip()
        click.echo(f"Daemon is running (PID {pid})")
    else:
        click.echo("Daemon is not running.")


@cli.command()
def auth() -> None:
    """Run the OAuth2 authorization flow."""
    from cloud_drive_sync.auth.credentials import get_credentials

    try:
        creds = get_credentials()
        click.echo("Authorization successful.")
        if creds.token:
            click.echo("Credentials stored and ready to use.")
    except FileNotFoundError as exc:
        click.echo(str(exc), err=True)
        sys.exit(1)
    except Exception as exc:
        click.echo(f"Authorization failed: {exc}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Helper for async CLI client calls
# ---------------------------------------------------------------------------

def _run_client_call(method: str, params: dict | None = None):
    """Connect to daemon, call method, return result."""
    async def _do():
        from cloud_drive_sync.ipc.cli_client import CliClient
        client = CliClient()
        try:
            await client.connect()
            return await client.call(method, params)
        finally:
            await client.close()
    return asyncio.run(_do())


# ---------------------------------------------------------------------------
# Webhooks
#
# Note this is the CLI's first settings surface. Every other command here is a read
# or a state action -- there is no `set_*` reachable from the command line at all --
# so there was no in-repo pattern to copy and this invents one.
# ---------------------------------------------------------------------------

@cli.group()
def webhook():
    """Manage outbound event webhooks."""


def _webhook_scope(scope: str) -> dict:
    return {"scope": scope}


@webhook.command("list")
@click.option("--scope", default="global", help="'global' or 'pair:<uid>'.")
@click.option("--raw", is_flag=True, help="Show the stored config rather than the resolved view.")
def webhook_list(scope: str, raw: bool):
    """Show the webhook targets that will actually fire for a scope.

    The resolved view by default, because "which webhooks fire for this folder" is the
    question a three-level merge makes hard to answer by reading the config.
    """
    try:
        if raw:
            result = _run_client_call("get_webhooks", _webhook_scope(scope))
            targets = (result.get("webhooks") or {}).get("targets") or []
            if not targets:
                click.echo(f"No webhook targets defined at {result.get('scope', scope)}.")
                return
            click.echo(f"Targets defined at {result.get('scope', scope)}:")
            for entry in targets:
                flags = []
                if entry.get("define"):
                    flags.append("define")
                if entry.get("enabled") is False:
                    flags.append("disabled")
                suffix = f"  [{', '.join(flags)}]" if flags else ""
                click.echo(f"  {entry.get('name')}{suffix}")
                if entry.get("url"):
                    click.echo(f"      url:    {entry['url']}")
                if entry.get("events"):
                    click.echo(f"      events: {', '.join(entry['events'])}")
            return

        result = _run_client_call("get_resolved_webhooks", _webhook_scope(scope))
        targets = result.get("targets") or []
        problems = result.get("problems") or []
        if not targets:
            click.echo(f"No webhooks will fire for {result.get('scope', scope)}.")
        else:
            click.echo(f"Webhooks that will fire for {result.get('scope', scope)}:")
            for target in targets:
                click.echo(f"  {target['name']}  ({target['target_key']})")
                click.echo(f"      endpoint: {target['endpoint']}")
                click.echo(f"      events:   {', '.join(target['events'])}")
                click.echo(
                    f"      auth:     {target['auth_mode']}"
                    + ("  + signature" if target.get("signed") else "")
                )
                if not target.get("verify_tls", True):
                    click.echo("      warning:  TLS verification is disabled")
        _echo_problems(problems)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


def _echo_problems(problems: list) -> None:
    if problems:
        click.echo("")
        click.echo("Configuration problems:", err=True)
        for problem in problems:
            click.echo(f"  - {problem}", err=True)


@webhook.command("status")
def webhook_status():
    """Delivery health for each target: counts, queue depth, breaker state."""
    try:
        result = _run_client_call("get_webhook_status", {})
        if not result.get("running"):
            click.echo("Webhook delivery is not running (no authenticated account yet).")
            return
        targets = result.get("targets") or []
        if not targets:
            click.echo("No webhook deliveries attempted yet.")
            return
        for target in targets:
            health = "healthy" if target.get("healthy") else "UNHEALTHY"
            click.echo(f"{target['target_key']}  [{health}]")
            click.echo(f"    endpoint:  {target['endpoint']}")
            click.echo(
                f"    delivered: {target['delivered']}   failed: {target['failed']}   "
                f"dropped: {target['dropped']}   queued: {target['queued']}"
            )
            if target.get("last_error"):
                click.echo(f"    last error: {target['last_error']}")
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@webhook.command("test")
@click.option("--scope", default="global", help="'global' or 'pair:<uid>'.")
@click.option("--name", default=None, help="Send to one target only.")
def webhook_test(scope: str, name: str | None):
    """Send a webhook.test event to the resolved targets."""
    params = _webhook_scope(scope)
    if name:
        params["name"] = name
    try:
        result = _run_client_call("test_webhook", params)
        sent = result.get("sent_to") or []
        if not sent:
            click.echo("No targets resolved for that scope; nothing sent.")
        else:
            click.echo(f"Test event queued for: {', '.join(sent)}")
            click.echo("Check 'webhook status' for the delivery result.")
        _echo_problems(result.get("problems") or [])
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Account management
# ---------------------------------------------------------------------------

@cli.group()
def account():
    """Manage cloud storage accounts."""


@account.command("add")
@click.option("--provider", type=click.Choice(["gdrive", "dropbox", "onedrive", "nextcloud", "box"]), default="gdrive")
@click.option("--headless", is_flag=True, help="Use console-based auth (no browser)")
def account_add(provider: str, headless: bool):
    """Add a new cloud account."""
    click.echo(f"Adding {provider} account...")
    try:
        result = _run_client_call("add_account", {"provider": provider, "headless": headless})
        if isinstance(result, dict) and result.get("status") == "auth_url":
            # Two-step flow: daemon returned the auth URL, we need to collect the code
            auth_url = result.get("auth_url", "")
            click.echo(f"\nVisit this URL to authorize:\n\n  {auth_url}\n")
            click.echo("Sign in, click 'Allow', then copy the authorization code.\n")
            code = click.prompt("Enter the authorization code")
            result = _run_client_call("exchange_auth_code", {"provider": provider, "code": code})
            if isinstance(result, dict) and result.get("status") == "ok":
                click.echo(f"Account added: {result.get('email', provider)}")
            elif isinstance(result, dict) and result.get("status") == "error":
                click.echo(f"Failed: {result.get('message', 'Unknown error')}", err=True)
                sys.exit(1)
            else:
                click.echo("Account added.")
        elif isinstance(result, dict) and result.get("status") == "ok":
            email = result.get("email", "unknown")
            click.echo(f"Account added: {email}")
        elif isinstance(result, dict) and result.get("status") == "error":
            click.echo(f"Failed: {result.get('message', 'Unknown error')}", err=True)
            sys.exit(1)
        else:
            click.echo("Account added.")
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@account.command("remove")
@click.argument("email")
@click.option(
    "--provider",
    type=click.Choice(["gdrive", "dropbox", "onedrive", "box", "nextcloud"]),
    default=None,
    help="Which provider's account to remove. Required when the same address is "
         "registered with more than one provider.",
)
def account_remove(email: str, provider: str | None):
    """Remove a cloud account and delete its stored credentials.

    Sync pairs using the account are kept but unbound, so they can be reassigned.
    """
    try:
        params = {"email": email}
        if provider:
            params["provider"] = provider
        _run_client_call("remove_account", params)
        click.echo(f"Removed account: {email}" + (f" ({provider})" if provider else ""))
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@account.command("list")
def account_list():
    """List all cloud accounts."""
    try:
        accounts = _run_client_call("list_accounts")
        if not accounts:
            click.echo("No accounts configured.")
            return
        for acct in accounts:
            status_icon = "\u25cf" if acct.get("status") == "connected" else "\u25cb"
            provider = acct.get("provider", "gdrive")
            click.echo(f"  {status_icon} {acct['email']} [{provider}] ({acct.get('status', 'unknown')})")
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Sync pair management
# ---------------------------------------------------------------------------

@cli.group()
def pair():
    """Manage sync folder pairs."""


@pair.command("add")
@click.option("--local", required=True, type=click.Path(path_type=Path), help="Local folder path")
@click.option("--remote", required=True, help="Remote folder ID")
@click.option("--account", "account_id", default=None, help="Account email")
@click.option("--provider", default=None, help="Provider name")
def pair_add(local: Path, remote: str, account_id: str | None, provider: str | None):
    """Add a new sync pair."""
    params = {
        "local_path": str(local.resolve()),
        "remote_folder_id": remote,
    }
    if account_id:
        params["account_id"] = account_id
    if provider:
        params["provider"] = provider
    try:
        result = _run_client_call("add_sync_pair", params)
        click.echo(f"Sync pair added: {result.get('local_path', local)} <-> {result.get('remote_folder_id', remote)}")
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@pair.command("remove")
@click.argument("pair_id")
def pair_remove(pair_id: str):
    """Remove a sync pair."""
    try:
        _run_client_call("remove_sync_pair", {"id": pair_id})
        click.echo(f"Removed sync pair: {pair_id}")
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@pair.command("list")
def pair_list():
    """List all sync pairs."""
    try:
        pairs = _run_client_call("get_sync_pairs")
        if not pairs:
            click.echo("No sync pairs configured.")
            return
        for p in pairs:
            mode = p.get("sync_mode", "two_way")
            provider = p.get("provider", "gdrive")
            remote = p.get("remote_folder_id", "root")
            if remote == "root":
                remote = "My Drive"
            click.echo(f"  [{p['id']}] {p['local_path']} <-> {remote} ({mode}) [{provider}]")
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Top-level sync commands
# ---------------------------------------------------------------------------

@cli.command("sync")
@click.argument("pair_id", required=False)
def sync_now(pair_id: str | None):
    """Trigger an immediate sync."""
    try:
        params = {"pair_id": pair_id} if pair_id else {}
        result = _run_client_call("force_sync", params) or {}
        # The daemon answers not_found for an id it cannot resolve. This used to be
        # discarded, so `sync 0` printed "Sync triggered." having done nothing.
        if result.get("status") == "not_found":
            click.echo(f"Error: no such sync pair: {pair_id}", err=True)
            sys.exit(1)
        click.echo("Sync triggered." if pair_id else "Sync triggered for all pairs.")
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@cli.command("gen-token")
def gen_token():
    """Print a strong random token for --http-token or --mcp-token."""
    from cloud_drive_sync.http.auth import generate_token

    click.echo(generate_token())


@cli.group("deletions")
def deletions():
    """Inspect and resolve deletions blocked by the delete fail-safe."""


@deletions.command("list")
def deletions_list():
    """Show deletion batches the fail-safe refused."""
    try:
        pending = _run_client_call("get_pending_deletions", {})
        if not pending:
            click.echo("No blocked deletions.")
            return
        for item in pending:
            pair = item.get("pair_id", "?")
            click.echo(
                f"{pair}  {item.get('count', 0)} {item.get('direction', '?')} file(s) blocked "
                f"(limit {item.get('limit', '?')})"
            )
            tracked = item.get("tracked") or 0
            if tracked:
                pct = 100 * item.get("count", 0) / tracked
                click.echo(f"    {pct:.0f}% of {tracked} tracked file(s)")
            for path in (item.get("sample") or [])[:10]:
                click.echo(f"    - {path}")
            extra = item.get("count", 0) - len(item.get("sample") or [])
            if extra > 0:
                click.echo(f"    … and {extra} more")
            click.echo(f"    blocked at {item.get('created_at', '?')}")
        click.echo()
        click.echo("Approve with:  cloud-drive-sync deletions approve <pair_id>")
        click.echo("Reject with:   cloud-drive-sync deletions reject <pair_id>")
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@deletions.command("approve")
@click.argument("pair_id")
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
def deletions_approve(pair_id: str, yes: bool):
    """Allow the blocked deletions for PAIR_ID to proceed.

    The next sync pass re-plans rather than replaying the stored batch, so if the
    files have since been restored nothing is deleted.
    """
    try:
        pending = _run_client_call("get_pending_deletions", {"pair_id": pair_id})
        if not pending:
            click.echo(f"No blocked deletions for {pair_id}.")
            return
        total = sum(item.get("count", 0) for item in pending)
        if not yes:
            click.echo(f"This will allow {total} file(s) to be deleted on {pair_id}:")
            for item in pending:
                for path in (item.get("sample") or [])[:5]:
                    click.echo(f"  - {path}")
            click.confirm("Proceed?", abort=True)
        result = _run_client_call(
            "resolve_pending_deletions", {"pair_id": pair_id, "approve": True}
        )
        click.echo(
            f"Approved {result.get('batches', 0)} batch(es); sync resumed for {pair_id}. "
            "The approval applies to the next pass only."
        )
    except click.Abort:
        click.echo("Aborted — nothing was deleted.")
        sys.exit(1)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@deletions.command("reject")
@click.argument("pair_id")
def deletions_reject(pair_id: str):
    """Refuse the blocked deletions for PAIR_ID. The pair stays paused."""
    try:
        result = _run_client_call(
            "resolve_pending_deletions", {"pair_id": pair_id, "approve": False}
        )
        if result.get("status") == "not_found":
            click.echo(f"No blocked deletions for {pair_id}.")
            return
        click.echo(
            f"Rejected {result.get('batches', 0)} batch(es). Nothing was deleted and "
            f"{pair_id} stays paused — resume it with `resume {pair_id}` once the "
            "cause is resolved."
        )
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@cli.command("stop-activity")
@click.option("--account", "account_id", default=None, help="Stop one account only.")
def stop_activity(account_id: str | None):
    """Stop ALL sync activity immediately, cancelling transfers in progress.

    Unlike `pause`, which lets in-flight transfers finish, this cancels them.
    Persists across restart — use `resume-activity` to undo it.
    """
    try:
        params = {"account_id": account_id} if account_id else {}
        result = _run_client_call("emergency_stop", params)
        click.echo(
            f"Stopped {result.get('pairs_stopped', 0)} pair(s); "
            f"{result.get('operations_cancelled', 0)} operation(s) cancelled."
        )
        click.echo("Transfers already inside a provider call may take a moment to unwind.")
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@cli.command("resume-activity")
@click.option("--account", "account_id", default=None, help="Resume one account only.")
def resume_activity(account_id: str | None):
    """Resume after `stop-activity`."""
    try:
        params = {"account_id": account_id} if account_id else {}
        result = _run_client_call("emergency_resume", params)
        resumed = result.get("pairs_resumed", 0)
        if resumed == 0 and account_id:
            click.echo(
                "Nothing resumed — activity is stopped application-wide. "
                "Run `resume-activity` without --account first."
            )
        else:
            click.echo(f"Resumed {resumed} pair(s).")
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@cli.command()
@click.argument("pair_id", required=False)
def pause(pair_id: str | None):
    """Pause syncing."""
    try:
        params = {"pair_id": pair_id} if pair_id else {}
        result = _run_client_call("pause_sync", params) or {}
        if result.get("status") == "not_found":
            click.echo(f"Error: no such sync pair: {pair_id}", err=True)
            sys.exit(1)
        if pair_id:
            click.echo(f"Paused sync pair {pair_id}.")
        else:
            click.echo(f"Paused all sync pairs ({result.get('pairs', 0)}).")
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@cli.command()
@click.argument("pair_id", required=False)
def resume(pair_id: str | None):
    """Resume syncing."""
    try:
        params = {"pair_id": pair_id} if pair_id else {}
        result = _run_client_call("resume_sync", params) or {}
        if result.get("status") == "not_found":
            click.echo(f"Error: no such sync pair: {pair_id}", err=True)
            sys.exit(1)
        if pair_id:
            click.echo(f"Resumed sync pair {pair_id}.")
        else:
            click.echo(f"Resumed all sync pairs ({result.get('pairs', 0)}).")
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@cli.command()
@click.option("--limit", "-n", default=20, help="Number of entries to show")
def activity(limit: int):
    """Show recent sync activity."""
    try:
        entries = _run_client_call("get_activity_log", {"limit": limit, "offset": 0})
        if not entries:
            click.echo("No recent activity.")
            return
        for e in entries:
            ts = e.get("timestamp", "")[:19].replace("T", " ")
            status_mark = "\u2713" if e.get("status") == "success" else "\u2717" if e.get("status") == "error" else "\u00b7"
            click.echo(f"  {status_mark} {ts}  {e.get('details', '')}  {e.get('path', '')}")
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@cli.command()
def conflicts():
    """Show unresolved conflicts."""
    try:
        items = _run_client_call("get_conflicts")
        if not items:
            click.echo("No unresolved conflicts.")
            return
        for c in items:
            click.echo(f"  [{c['id']}] {c['path']} (detected {c.get('detected_at', 'unknown')})")
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@cli.command()
@click.argument("conflict_id")
@click.argument("resolution", type=click.Choice(["keep_local", "keep_remote", "keep_both"]))
def resolve(conflict_id: str, resolution: str):
    """Resolve a sync conflict."""
    try:
        _run_client_call("resolve_conflict", {"conflict_id": conflict_id, "resolution": resolution})
        click.echo(f"Conflict {conflict_id} resolved with: {resolution}")
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@cli.command("repair")
@click.argument("pair_id", required=False)
@click.option("--dry-run", is_flag=True, help="Report stubs without deleting them")
def repair(pair_id: str | None, dry_run: bool):
    """Delete zero-byte remote stubs left by failed uploads.

    Scans each sync pair for remote files that are 0 bytes while the local
    copy has content, removes them from the remote and clears the stored
    sync state. The next sync cycle will then re-upload the correct files.
    """
    try:
        params: dict = {"dry_run": dry_run}
        if pair_id:
            params["pair_id"] = pair_id
        result = _run_client_call("repair", params)
        repaired = result.get("repaired", 0)
        pairs_scanned = result.get("pairs_scanned", 0)
        stubs = result.get("stubs", [])
        prefix = "[dry-run] " if dry_run else ""
        click.echo(f"{prefix}Scanned {pairs_scanned} pair(s), found {repaired} stub(s).")
        if stubs:
            for path in stubs:
                action = "would delete" if dry_run else "deleted"
                click.echo(f"  {action}: {path}")
        elif not pairs_scanned:
            # Claiming health after examining nothing is the most misleading answer a
            # repair tool can give.
            click.echo("No enabled sync pairs were scanned — nothing was checked.", err=True)
            sys.exit(1)
        elif not repaired:
            click.echo("No stubs found — everything looks healthy.")
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@cli.command("install-nautilus")
def install_nautilus():
    """Install the Nautilus file manager overlay extension."""
    from cloud_drive_sync.extensions.install import install

    try:
        link = install()
        click.echo(f"Nautilus overlay extension installed: {link}")
        click.echo("Restart Nautilus to activate: nautilus -q")
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@cli.command("uninstall-nautilus")
def uninstall_nautilus():
    """Uninstall the Nautilus file manager overlay extension."""
    from cloud_drive_sync.extensions.install import uninstall

    if uninstall():
        click.echo("Nautilus overlay extension uninstalled.")
        click.echo("Restart Nautilus to deactivate: nautilus -q")
    else:
        click.echo("No extension found to uninstall.")


def main() -> None:
    """Entry point for the CLI."""
    cli()
