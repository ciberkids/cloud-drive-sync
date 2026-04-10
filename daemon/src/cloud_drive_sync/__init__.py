"""cloud-drive-sync: Bidirectional Google Drive sync daemon for Linux."""

__version__ = "dev"
__build_date__ = ""

try:
    from importlib.metadata import version as _pkg_version
    __version__ = _pkg_version("cloud-drive-sync")
except Exception:
    pass  # keep the baked-in __version__ (injected by CI, or "dev" in local builds)
