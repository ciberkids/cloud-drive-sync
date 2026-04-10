"""cloud-drive-sync: Bidirectional Google Drive sync daemon for Linux."""

try:
    from importlib.metadata import version as _pkg_version
    __version__ = _pkg_version("cloud-drive-sync")
except Exception:
    __version__ = "dev"

__build_date__ = ""
