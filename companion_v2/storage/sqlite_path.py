"""SQLite read-only paths, including Windows network shares and Unicode names."""

from pathlib import Path
from urllib.parse import quote


def file_uri(path, *, mode=None):
    text = str(path)
    if text.startswith("file:"):
        if not mode or "mode=" in text:
            return text
        separator = "&" if "?" in text else "?"
        return f"{text}{separator}mode={mode}"
    resolved = Path(path).resolve()
    value = resolved.as_posix()
    # SQLite's Windows build rejects a non-local URI authority. Four slashes
    # retain the UNC path with an empty authority instead of file://server/.
    uri = "file://" + quote(value, safe="/:") if value.startswith("//") else resolved.as_uri()
    return f"{uri}?mode={mode}" if mode else uri


def readonly_uri(path):
    return file_uri(path, mode="ro")
