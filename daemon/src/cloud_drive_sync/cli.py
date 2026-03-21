"""CLI entry point using Click."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import click

from cloud_drive_sync.util.logging import get_logger

log = get_logger("cli")


@click.group()
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
@click.pass_context
def start(ctx: click.Context, foreground: bool, demo: bool) -> None:
    """Start the sync daemon."""
    from cloud_drive_sync.daemon import Daemon

    if Daemon.is_running():
        click.echo("Daemon is already running.", err=True)
        sys.exit(1)

    daemon = Daemon(
        config_path=ctx.obj["config_path"],
        log_level=ctx.obj["log_level"],
        demo=demo,
    )

    if foreground:
        click.echo("Starting in foreground...")
        asyncio.run(daemon.run())
    else:
        click.echo("Starting daemon...")
        # Simple fork-based daemonization
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
# Account management
# ---------------------------------------------------------------------------

@cli.group()
def account():
    """Manage cloud storage accounts."""
    pass


@account.command("add")
@click.option("--provider", type=click.Choice(["gdrive", "dropbox", "onedrive", "nextcloud", "box"]), default="gdrive")
@click.option("--headless", is_flag=True, help="Use console-based auth (no browser)")
def account_add(provider: str, headless: bool):
    """Add a new cloud account."""
    click.echo(f"Adding {provider} account...")
    try:
        result = _run_client_call("add_account", {"provider": provider, "headless": headless})
        if isinstance(result, dict) and result.get("status") == "ok":
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
def account_remove(email: str):
    """Remove a cloud account."""
    try:
        _run_client_call("remove_account", {"email": email})
        click.echo(f"Removed account: {email}")
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
    pass


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
        _run_client_call("force_sync", params)
        click.echo("Sync triggered.")
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@cli.command()
@click.argument("pair_id", required=False)
def pause(pair_id: str | None):
    """Pause syncing."""
    try:
        params = {"pair_id": pair_id} if pair_id else {}
        _run_client_call("pause_sync", params)
        click.echo("Sync paused.")
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@cli.command()
@click.argument("pair_id", required=False)
def resume(pair_id: str | None):
    """Resume syncing."""
    try:
        params = {"pair_id": pair_id} if pair_id else {}
        _run_client_call("resume_sync", params)
        click.echo("Sync resumed.")
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


def main() -> None:
    """Entry point for the CLI."""
    cli()
