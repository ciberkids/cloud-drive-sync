"""Shared test fixtures."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def short_tmp(tmp_path: Path):
    """Provide a short temp directory suitable for Unix sockets.

    macOS has a 104-char limit for Unix socket paths.  pytest's default
    ``tmp_path`` lives under ``/var/folders/…`` and can easily exceed that,
    so on macOS we create a directory under ``/tmp`` instead.
    """
    if sys.platform == "darwin":
        with tempfile.TemporaryDirectory(prefix="cds", dir="/tmp") as d:
            yield Path(d)
    else:
        yield tmp_path
