"""Platform-observed names and read-only OneBot labels for Dashboard navigation."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from time import monotonic


CACHE_SECONDS = 300
RETRY_SECONDS = 30
LOOKUP_TIMEOUT = 2


def _name(value):
    return " ".join(value.split())[:128] if isinstance(value, str) else ""


def _identifier(value):
    if type(value) not in (str, int):
        return ""
    text = str(value).strip()
    return text if text.isascii() and text.isdigit() and int(text) > 0 else ""


def platform_label(value, native_id=""):
    """Ignore an ID fallback, including QQ Official's opaque OpenIDs."""
    text = _name(value)
    return "" if text == str(native_id) or not native_id and _identifier(text) else text


def event_person_names(event):
    """Read names for the authenticated sender, never quoted or mentioned users."""
    message = event.message_obj
    sender = getattr(message, "sender", None)
    peer = str(getattr(sender, "user_id", "") or "")
    if not peer:
        return "", ""
    normalized_name = platform_label(getattr(sender, "nickname", None), peer)
    raw = getattr(message, "raw_message", None)
    if getattr(getattr(event, "platform_meta", None), "name", "") in {"qq_official", "qq_official_webhook"}:
        payload = raw if isinstance(raw, Mapping) else getattr(raw, "raw_data", None)
        author = (payload.get("author") if isinstance(payload, Mapping) else None) or getattr(raw, "author", None)
        field = "member_openid" if getattr(message, "group_id", None) else "user_openid"
        author_id = (author.get(field) or author.get("id")) if isinstance(author, Mapping) else (
            getattr(author, field, None) or getattr(author, "id", None))
        username = author.get("username") if isinstance(author, Mapping) else getattr(author, "username", None)
        nickname = platform_label(username, peer) if str(author_id or "") == peer else ""
        return nickname or normalized_name, ""
    if getattr(getattr(event, "platform_meta", None), "name", "") == "wechat_native":
        return normalized_name, ""
    sender_data = raw.get("sender") if isinstance(raw, Mapping) else None
    if not isinstance(sender_data, Mapping):
        return "", ""
    return platform_label(sender_data.get("nickname"), peer), platform_label(sender_data.get("card"), peer)


class PlatformDisplayNames:
    def __init__(self, context):
        self.context = context
        self._cache = {}
        self._locks = {}
        self._slots = asyncio.Semaphore(6)

    def _api(self, platform_id, account_id):
        try:
            platform = self.context.get_platform_inst(platform_id)
            metadata = platform.meta()
            if metadata.name != "aiocqhttp" or metadata.id != platform_id:
                return None
            client = platform.get_client()
            if not getattr(client, "_wsr_api_clients", {}).get(account_id):
                return None
            api = getattr(getattr(client, "_api", None), "_wsr_api", None)
            return api if callable(getattr(api, "call_action", None)) else None
        except Exception:
            return None

    async def _read(self, platform_id, account_id, action, **params):
        api = self._api(platform_id, account_id)
        if api is None:
            return None
        try:
            async with self._slots:
                result = await asyncio.wait_for(
                    api.call_action(action, self_id=account_id, **params), LOOKUP_TIMEOUT)
            if isinstance(result, dict) and ("status" in result or "retcode" in result):
                if result.get("status", "ok") != "ok" or result.get("retcode", 0) != 0:
                    return None
                return result.get("data")
            return result
        except Exception:
            # A failed optional name lookup must not block configuration or permissions.
            return None

    async def _cached(self, key, read):
        cached = self._cache.get(key)
        if cached and cached[0] > monotonic():
            return cached[1]
        async with self._locks.setdefault(key, asyncio.Lock()):
            cached = self._cache.get(key)
            if cached and cached[0] > monotonic():
                return cached[1]
            result = await read()
            self._cache[key] = (monotonic() + (CACHE_SECONDS if result else RETRY_SECONDS), result)
            return result

    async def lookup(self, platform_id, account_id, kind, native_id, *, cached_only=False):
        account_id, native_id = _identifier(account_id), _identifier(native_id)
        if not platform_id or not account_id or not native_id:
            return ""
        key = (platform_id, account_id, kind, native_id)
        if cached_only:
            # Last-known labels are presentation only; never wait for OneBot
            # when the caller is painting an existing local directory.
            cached = self._cache.get(key)
            return cached[1] if cached else ""

        async def read():
            if kind == "account":
                record = await self._read(platform_id, account_id, "get_login_info")
                if isinstance(record, dict) and _identifier(record.get("user_id")) == native_id:
                    return _name(record.get("nickname"))
                return ""
            action, id_field = ("get_group_list", "group_id") if kind == "group" else ("get_friend_list", "user_id")
            records = await self._cached((platform_id, account_id, action),
                lambda: self._read(platform_id, account_id, action))
            for record in records if isinstance(records, list) else ():
                if not isinstance(record, dict) or _identifier(record.get(id_field)) != native_id:
                    continue
                name = (_name(record.get("group_name")) if kind == "group" else
                        _name(record.get("remark")) or _name(record.get("nickname")))
                if name:
                    return name
            detail_action = "get_group_info" if kind == "group" else "get_stranger_info"
            record = await self._read(platform_id, account_id, detail_action, **{id_field: int(native_id)})
            if isinstance(record, dict) and _identifier(record.get(id_field)) == native_id:
                return _name(record.get("group_name" if kind == "group" else "nickname"))
            return ""

        try:
            return await asyncio.wait_for(self._cached(key, read), LOOKUP_TIMEOUT * 2 + 0.5) or ""
        except TimeoutError:
            return ""

    def _platform_family(self, row, platform_id):
        try:
            platform = self.context.get_platform_inst(platform_id)
            if platform.meta().name == "wechat_native" and platform.meta().id == platform_id:
                row["platform_name"] = "wechat_native"
        except (AttributeError, TypeError, ValueError):
            pass

    async def targets(self, targets, *, cached_only=False):
        async def enrich(target):
            row = dict(target)
            platform, account = row.get("platform_id"), row.get("account_id")
            if not cached_only:
                self._platform_family(row, platform)
            kind = "group" if row.get("scope_kind") == "group" or row.get("group_id") else "private"
            native_id = row.get("group_id") if kind == "group" else row.get("participant_id")
            name, account_name = await asyncio.gather(
                self.lookup(platform, account, kind, native_id, cached_only=cached_only),
                self.lookup(platform, account, "account", account, cached_only=cached_only))
            # Keep a name already carried by the native session directory when
            # an optional OneBot lookup is unavailable. A bare id is not a
            # label and must not prevent a later group name from being used.
            fallback = ((platform_label(row.get("group_name"), native_id)
                         or platform_label(row.get("display_name"), native_id))
                        if kind == "group" else _name(row.get("display_name")))
            resolved = (_name(row.get("remark")) if kind == "group" else "") or platform_label(name, native_id) or fallback
            row.update(display_name=resolved,
                       account_name=account_name or row.get("account_name") or "")
            if kind == "group":
                row["group_name"] = resolved or _name(row.get("group_name"))
            return row
        return list(await asyncio.gather(*(enrich(row) for row in targets)))

    async def persons(self, persons, *, cached_only=False):
        async def enrich(person):
            row = dict(person)
            if not cached_only:
                self._platform_family(row, row.get("platform"))
            if (row.get("admin_nickname") or row.get("name_basis") == "administrator"
                    or row.get("name_basis") == "legacy" and row.get("legacy_maintained")):
                return row
            name = await self.lookup(row.get("platform"), row.get("account_id"), "private",
                                     row.get("native_account_id"), cached_only=cached_only)
            if name:
                row.update(display_name=name, name_basis="platform")
            return row
        return list(await asyncio.gather(*(enrich(row) for row in persons)))


def display_names(controller):
    resolver = getattr(controller, "_display_names", None)
    if resolver is None:
        resolver = PlatformDisplayNames(getattr(controller, "context", None))
        controller._display_names = resolver
    return resolver
