"""Trusted host temp-file detection shared by media locators.

AstrBot's own preprocess stage, the Telegram voice download and the Telegram
media preparer all write attachments into ``<astrbot data>/temp``.  A locator
that points at such a file is already host-vouched, so the media loaders may
read it directly instead of treating the network message as local disk
authority.

The trusted root is registered by the host layer at assembly time
(``astrbot_host.telegram_media``); the core tree never imports the host SDK.
Resolve before comparing so ``..`` segments and symlinks cannot escape the
directory.
"""

from __future__ import annotations

from pathlib import Path


_HOST_TEMP_ROOT = None


def register_host_temp_root(value):
    """Register the host-owned temp directory the loaders may trust."""
    global _HOST_TEMP_ROOT
    try:
        _HOST_TEMP_ROOT = Path(value).resolve() if value else None
    except (OSError, RuntimeError, ValueError):
        _HOST_TEMP_ROOT = None


def host_temp_root():
    return _HOST_TEMP_ROOT


def is_host_temp_file(value):
    root = _HOST_TEMP_ROOT
    if root is None or type(value) is not str or not value:
        return False
    if value.startswith(("base64://", "\\\\", "//")):
        return False
    try:
        path = Path(value)
        if not path.is_absolute():
            return False
        resolved = path.resolve()
        return resolved.is_file() and resolved.is_relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return False
