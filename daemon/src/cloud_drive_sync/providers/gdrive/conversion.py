"""Google Docs export/import conversion maps."""

from __future__ import annotations

# Maps Google-native MIME type -> (export MIME type, local file extension)
EXPORT_MAP: dict[str, tuple[str, str]] = {
    "application/vnd.google-apps.document": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".docx",
    ),
    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsx",
    ),
    "application/vnd.google-apps.presentation": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".pptx",
    ),
}

# Maps local file extension -> Google-native MIME type for re-upload with conversion
IMPORT_MAP: dict[str, str] = {
    ".docx": "application/vnd.google-apps.document",
    ".xlsx": "application/vnd.google-apps.spreadsheet",
    ".pptx": "application/vnd.google-apps.presentation",
}

# The set of Google-native MIME types that can be exported
EXPORTABLE_MIMES = frozenset(EXPORT_MAP.keys())

# Native doc types that cannot be exported (skip completely)
NON_EXPORTABLE_NATIVE_MIMES = frozenset(
    {
        "application/vnd.google-apps.form",
        "application/vnd.google-apps.drawing",
        "application/vnd.google-apps.script",
        "application/vnd.google-apps.site",
        "application/vnd.google-apps.jam",
        "application/vnd.google-apps.map",
    }
)


def get_export_info(native_mime: str) -> tuple[str, str] | None:
    """Get (export_mime, extension) for a native doc MIME type.

    Returns None if the MIME type cannot be exported.
    """
    return EXPORT_MAP.get(native_mime)


def get_native_mime_for_extension(ext: str) -> str | None:
    """Get the Google-native MIME type for a local file extension.

    Returns None if the extension is not a convertible type.
    """
    return IMPORT_MAP.get(ext.lower())
