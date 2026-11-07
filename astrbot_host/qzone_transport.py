"""One place that decides which OneBot client and which manual session QQ Zone uses.

Both hosts (single target and multi target) need the same two answers, and both
must keep credentials out of the plugin configuration. The manual session is read
from ``QZONE_COOKIE`` or ``<data root>/qzone_cookie.txt`` -- never from settings,
never from the previous plugin, and never written back anywhere.
"""

from __future__ import annotations

from os import environ

from .qzone_client import QzoneHttpClient


COOKIE_FILE_NAME = "qzone_cookie.txt"


def qzone_bot_candidates(context):
    """Every loaded aiocqhttp client, in host order."""
    manager = getattr(context, "platform_manager", None)
    found = []
    for platform in (getattr(manager, "platform_insts", None) or ()):
        meta = getattr(platform, "meta", None)
        if not callable(meta) or getattr(meta(), "name", "") != "aiocqhttp":
            continue
        client = platform.get_client() if callable(getattr(platform, "get_client", None)) else None
        if client is not None and all(client is not item for item in found):
            found.append(client)
    return tuple(found)


def manual_qzone_cookie(data_root=None):
    text = str(environ.get("QZONE_COOKIE") or "").strip()
    if text:
        return text
    if data_root is None:
        return ""
    try:
        candidate = data_root / COOKIE_FILE_NAME
        return candidate.read_text(encoding="utf-8").strip() if candidate.is_file() else ""
    except OSError:
        return ""


def build_qzone_transport(context, *, data_root=None, bot_selector=None):
    """Build an available transport; absent accounts and credentials expose no tools.

    Qzone is a QQ feature: only ``aiocqhttp`` clients can supply the runtime
    session, so a Telegram-only deployment gets ``None`` here.  The Qzone tool
    port is then not declared at all, and the dashboard route reports the
    readable ``qzone_unavailable`` state instead of failing.  The explicit
    manual cookie stays a separate operator opt-in because it needs no OneBot
    connection at all.
    """
    if not qzone_bot_candidates(context) and not manual_qzone_cookie(data_root):
        return None

    def bot():
        candidates = qzone_bot_candidates(context)
        if bot_selector is not None:
            candidates = tuple(item for item in candidates if bot_selector(item)) or candidates
        return candidates[0] if candidates else None

    return QzoneHttpClient(bot_provider=bot, manual_cookie=lambda: manual_qzone_cookie(data_root))


__all__ = ["COOKIE_FILE_NAME", "build_qzone_transport", "manual_qzone_cookie", "qzone_bot_candidates"]
