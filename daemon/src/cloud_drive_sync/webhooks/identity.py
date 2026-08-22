"""The daemon's own identity, as it appears in ``source.instance_id``.

Two daemons syncing the same account -- a laptop and a NAS, which is a normal
arrangement -- are otherwise indistinguishable at the receiver, and telling them apart
is precisely what a monitoring dashboard needs.

Stored in the data directory rather than in ``config.toml``, for one specific reason:
``Config.load`` must never write. First-run detection is ``not config_path().exists()``,
so a config file created as a side effect of loading one would make every install look
like an upgrade and silently switch off authentication-on-by-default for new installs.
A separate file has no such constraint.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from cloud_drive_sync.util.logging import get_logger
from cloud_drive_sync.util.paths import data_dir

log = get_logger("webhooks.identity")

_FILENAME = "instance_id"


def instance_id(path: Path | None = None) -> str:
    """Return this install's id, creating it on first call.

    Falls back to an ephemeral id if the file cannot be written -- a read-only data
    directory must not stop the daemon, and an id that changes on restart is a
    degraded payload rather than a broken one.
    """
    target = path or (data_dir() / _FILENAME)
    try:
        if target.exists():
            existing = target.read_text().strip()
            if existing:
                return existing
    except OSError as exc:
        log.warning("Could not read %s: %s", target, exc)

    minted = str(uuid.uuid4())
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # Not a secret, but there is no reason for it to be world-readable either, and
        # it sits beside files that are.
        target.touch(mode=0o600, exist_ok=True)
        os.chmod(target, 0o600)
        target.write_text(minted)
    except OSError as exc:
        log.warning(
            "Could not persist the instance id to %s (%s); using an ephemeral one, so "
            "webhook receivers will see a new source.instance_id after each restart",
            target,
            exc,
        )
    return minted
