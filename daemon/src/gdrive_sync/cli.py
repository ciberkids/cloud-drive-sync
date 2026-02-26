"""CLI entry point using Click."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import click

from gdrive_sync.util.logging import get_logger

log = get_logger("cli")


@click.group()
@click.option("--config", "config_path", type=click.Path(path_type=Path), default=None, help="Path to config.toml")
@click.option("--log-level", type=click.Choice(["debug", "info", "warning", "error"], case_sensitive=False), default=None)
@click.pass_context
def cli(ctx: click.Context, config_path: Path | None, log_level: str | None) -> None:
    """gdrive-sync-daemon: bidirectional Google Drive sync for Linux."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config_path
    ctx.obj["log_level"] = log_level


@cli.command()
@click.option("--foreground", is_flag=True, help="Run in the foreground (don't daemonize)")
@click.option("--demo", is_flag=True, help="Run in demo mode with mock Google Drive (no credentials needed)")
@click.pass_context
def start(ctx: click.Context, foreground: bool, demo: bool) -> None:
    """Start the sync daemon."""
    from gdrive_sync.daemon import Daemon

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
    from gdrive_sync.daemon import Daemon

    if Daemon.stop_running():
        click.echo("Stop signal sent.")
    else:
        click.echo("No running daemon found.", err=True)
        sys.exit(1)


@cli.command()
def status() -> None:
    """Check daemon status."""
    from gdrive_sync.daemon import Daemon

    if Daemon.is_running():
        from gdrive_sync.util.paths import pid_path

        pid = pid_path().read_text().strip()
        click.echo(f"Daemon is running (PID {pid})")
    else:
        click.echo("Daemon is not running.")


@cli.command()
def auth() -> None:
    """Run the OAuth2 authorization flow."""
    from gdrive_sync.auth.credentials import get_credentials

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


def main() -> None:
    """Entry point for the CLI."""
    cli()
