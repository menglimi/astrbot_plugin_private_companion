# -*- coding: utf-8 -*-
"""PrivateCompanionPageApiBookshelfPart01Mixin。

由 tools/split_mixin_domain.py 从 page_api_bookshelf.py 机械抽取（23 个方法 + 0 个模块级名字 + 0 个类级赋值 / 343 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiBookshelfMixin）。
"""
from __future__ import annotations

from .page_api_bookshelf_shared import BOOKSHELF_ACCESS_TOKEN_MAX_PERSISTED, BOOKSHELF_ACCESS_TOKEN_TTL_SECONDS, logger
from .page_api_bookshelf_shared import Any
from .page_api_bookshelf_shared import _strip_internal_message_blocks
from .page_api_bookshelf_shared import datetime
from .page_api_bookshelf_shared import deepcopy
from .page_api_bookshelf_shared import hashlib
from .page_api_bookshelf_shared import hmac
from .page_api_bookshelf_shared import json
from .page_api_bookshelf_shared import re
from .page_api_bookshelf_shared import request
from .page_api_bookshelf_shared import secrets
from .page_api_bookshelf_shared import time



class PrivateCompanionPageApiBookshelfPart01Mixin:
    """PrivateCompanionPageApiBookshelfPart01Mixin（从 PrivateCompanionPageApiBookshelfMixin 拆出）。"""


    async def unlock_bookshelf(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        password = str(payload.get("password", "")).strip()
        try:
            expected = await self.plugin._ensure_bookshelf_password_async()
            async with self.plugin._data_lock:
                if not self._bookshelf_password_matches(password, expected):
                    return self._error("密码不对。需要在聊天里自然向 Bot 询问。")
                access_token = (self._issue_bookshelf_access_token(persist=True))
                saver = getattr(self.plugin, "_save_data_sync", None)
                if callable(saver):
                    saver(sections={"bookshelf_secret"})
                data = deepcopy(self.plugin.data)
            return self._ok({"bookshelf": await self._bookshelf_summary(data, unlocked=True, access_token=access_token)})
        except Exception as exc:
            logger.error(f"解锁资料柜夹层失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))

    async def get_bookshelf_session(self) -> dict[str, Any]:
        """Restore a previously unlocked bookshelf session from the browser token.

        The browser may call this endpoint after a page reload or a plugin restart. The
        persisted record contains only a SHA-256 token digest, never the bearer token
        itself; the raw token remains available only in the current request/runtime map.
        """
        payload = await request.get_json(silent=True) or {}
        access_token = (self._bookshelf_request_token(payload))
        if not self._bookshelf_access_token_valid(access_token):
            return self._error(self._bookshelf_access_error()["error"])
        try:
            async with self.plugin._data_lock:
                data = deepcopy(self.plugin.data)
            expires_at = self._bookshelf_access_token_expires_at(access_token)
            bookshelf = await self._bookshelf_summary(data, unlocked=True, access_token=access_token)
            bookshelf["access_expires_at"] = int(expires_at) if expires_at > 0 else 0
            return self._ok({"bookshelf": bookshelf})
        except Exception as exc:
            logger.error(f"恢复资料柜夹层会话失败: {exc}", exc_info=True)
            return self._error(str(exc))

    @staticmethod
    def _normalize_bookshelf_password(value: Any) -> str:
        text = _strip_internal_message_blocks(value)
        text = re.sub(r"\s+", "", text)
        text = text.strip("「」『』“”\"'` 。，,.;；:：!！?？、~～（）()[]【】")
        return text.lower()

    def _bookshelf_password_matches(self, provided: Any, expected: Any) -> bool:
        normalized_expected = self._normalize_bookshelf_password(expected)
        normalized_provided = self._normalize_bookshelf_password(provided)
        if not normalized_expected or not normalized_provided:
            return False
        if normalized_provided == normalized_expected:
            return True
        return len(normalized_expected) >= 2 and normalized_expected in normalized_provided

    def _bookshelf_album_id(self, item: Any, *, limit: int = 80) -> str:
        if not isinstance(item, dict):
            return ""
        explicit_album_id = self._single_line(item.get("album_id"), limit)
        album_id = explicit_album_id or self._single_line(item.get("id"), limit)
        if not explicit_album_id and album_id.startswith("archive-"):
            album_id = self._single_line(album_id.removeprefix("archive-"), limit)
        key = self._single_line(item.get("key"), 120)
        if not album_id and key.startswith("archive_item:"):
            album_id = self._single_line(key.split(":", 1)[1], limit)
        if not album_id and key.startswith("archive-"):
            album_id = self._single_line(key.removeprefix("archive-"), limit)
        return album_id

    def _bookshelf_diary_date_key(self, value: Any) -> str:
        text = self._single_line(value, 64)
        if not text:
            return ""
        match = re.search(
            r"(?<!\d)(\d{4})\s*(?:-|/|\.|年)\s*(\d{1,2})\s*(?:-|/|\.|月)\s*(\d{1,2})(?:日)?(?!\d)",
            text,
        )
        if match:
            try:
                return datetime(
                    int(match.group(1)),
                    int(match.group(2)),
                    int(match.group(3)),
                ).strftime("%Y-%m-%d")
            except ValueError:
                pass
        return text

    def _bookshelf_diary_entry_key(
        self,
        value: Any,
        fallback_date: Any = "",
        duplicate_index: int = 0,
    ) -> str:
        if isinstance(value, dict):
            stored_id = self._single_line(value.get("entry_key") or value.get("id"), 160)
            seed: Any = {"stored_id": stored_id} if stored_id else value
        elif isinstance(value, str):
            seed = {"body": value}
        else:
            seed = {"value": str(value)}
        key_payload = {
            "fallback_date": self._single_line(fallback_date, 64),
            "entry": seed,
        }
        if duplicate_index > 0:
            key_payload["duplicate_index"] = duplicate_index
        serialized = json.dumps(
            key_payload,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:24]
        return f"diary:{digest}"

    def _bookshelf_next_diary_entry_key(
        self,
        value: Any,
        fallback_date: Any,
        occurrences: dict[str, int],
    ) -> str:
        base_key = self._bookshelf_diary_entry_key(value, fallback_date)
        duplicate_index = occurrences.get(base_key, 0)
        occurrences[base_key] = duplicate_index + 1
        if duplicate_index == 0:
            return base_key
        return self._bookshelf_diary_entry_key(value, fallback_date, duplicate_index)

    def _bookshelf_diary_entries(self, value: Any) -> list[dict[str, Any]]:
        if isinstance(value, list):
            source = [("", item) for item in value]
            sort_by_date = False
        elif isinstance(value, dict):
            source = list(value.items())
            sort_by_date = True
        else:
            return []

        entries: list[dict[str, Any]] = []
        entry_key_occurrences: dict[str, int] = {}
        for fallback_date, raw in source:
            entry_key = self._bookshelf_next_diary_entry_key(
                raw,
                fallback_date,
                entry_key_occurrences,
            )
            if isinstance(raw, dict):
                item = deepcopy(raw)
            elif isinstance(raw, str) and raw.strip():
                item = {"body": raw.strip()}
            else:
                continue
            diary_date = self._bookshelf_diary_date_key(item.get("date") or fallback_date)
            item["date"] = diary_date or "某天"
            item["entry_key"] = entry_key
            if not item.get("body"):
                item["body"] = item.get("content") or item.get("text") or ""
            entries.append(item)
        if sort_by_date:
            entries.sort(
                key=lambda item: (
                    self._bookshelf_diary_date_key(item.get("date")) == "某天",
                    self._bookshelf_diary_date_key(item.get("date")),
                    self._single_line(item.get("entry_key"), 80),
                )
            )
        return entries

    def _is_bookshelf_archive_item(self, item: Any) -> bool:
        if not isinstance(item, dict):
            return False
        kind = self._single_line(item.get("type") or item.get("kind"), 32)
        if kind:
            return kind == "archive_item"
        key = self._single_line(item.get("key"), 120)
        return key.startswith("archive_item:") or key.startswith("archive-")

    def _bookshelf_deleted_album_ids(self, state: Any) -> set[str]:
        if not isinstance(state, dict):
            return set()
        return {
            self._single_line(value, 80)
            for value in (state.get("deleted_album_ids") if isinstance(state.get("deleted_album_ids"), list) else [])
            if self._single_line(value, 80)
        }

    def _bookshelf_deleted_title_markers(self, state: Any) -> set[str]:
        if not isinstance(state, dict):
            return set()
        return {
            marker
            for value in (state.get("deleted_titles") if isinstance(state.get("deleted_titles"), list) else [])
            if (marker := " ".join(self._single_line(value, 160).split()).casefold())
        }

    def _is_deleted_bookshelf_archive_item(self, item: Any, state: Any) -> bool:
        if not self._is_bookshelf_archive_item(item):
            return False
        album_id = self._bookshelf_album_id(item)
        if album_id:
            return album_id in self._bookshelf_deleted_album_ids(state)
        title = " ".join(self._single_line(item.get("title"), 160).split()).casefold()
        return bool(title and title in self._bookshelf_deleted_title_markers(state))

    def _mark_bookshelf_data_changed(self) -> None:
        marker = getattr(self.plugin, "_mark_bookshelf_store_changed", None)
        if callable(marker):
            marker()

    def _bookshelf_access_tokens(self) -> dict[str, Any]:
        store = getattr(self.plugin, "_bookshelf_access_tokens", None)
        if not isinstance(store, dict):
            store = {}
            setattr(self.plugin, "_bookshelf_access_tokens", store)
        now = time.time()
        current_persona = self._bookshelf_access_persona_id()
        for token, raw_entry in list(store.items()):
            if isinstance(raw_entry, dict):
                expires_at = self._float(raw_entry.get("expires_at"))
            else:
                # Runtime tokens created before persona binding are safe only in
                # single-persona mode because their original owner is unknowable.
                expires_at = self._float(raw_entry) if not current_persona else 0.0
            if self._float(expires_at) <= now:
                store.pop(token, None)
        return store

    @staticmethod
    def _bookshelf_access_token_digest(token: Any) -> str:
        token_text = str(token or "").strip()
        if not token_text:
            return ""
        return hashlib.sha256(token_text.encode("utf-8")).hexdigest()

    def _bookshelf_persisted_access_entries(self) -> list[dict[str, Any]]:
        data = getattr(self.plugin, "data", None)
        if not isinstance(data, dict):
            return []
        secret = data.get("bookshelf_secret")
        if not isinstance(secret, dict):
            return []
        state = secret.get("web_access")
        if not isinstance(state, dict):
            return []
        raw_entries = state.get("tokens")
        if not isinstance(raw_entries, list):
            # Accept the first single-token shape for forwards/backwards compatibility.
            raw_entries = [state] if state.get("token_hash") or state.get("hash") else []
        entries: list[dict[str, Any]] = []
        for raw in raw_entries:
            if not isinstance(raw, dict):
                continue
            digest = self._single_line(raw.get("token_hash") or raw.get("hash"), 128).lower()
            expires_at = self._float(raw.get("expires_at"))
            if not re.fullmatch(r"[0-9a-f]{64}", digest) or expires_at <= 0:
                continue
            persona_id = self._single_line(raw.get("persona_id"), 96)
            if not persona_id:
                # A persisted token already lives inside the active persona's data
                # profile. Bind legacy records to that profile on read.
                persona_id = self._bookshelf_access_persona_id()
            entries.append(
                {
                    "token_hash": digest,
                    "expires_at": expires_at,
                    "persona_id": persona_id,
                }
            )
        return entries

    def _persist_bookshelf_access_token(self, token: str, expires_at: float) -> None:
        data = getattr(self.plugin, "data", None)
        if not isinstance(data, dict):
            return
        secret = data.setdefault("bookshelf_secret", {})
        if not isinstance(secret, dict):
            secret = {}
            data["bookshelf_secret"] = secret
        digest = self._bookshelf_access_token_digest(token)
        if not digest or expires_at <= 0:
            return
        now = time.time()
        persona_id = self._bookshelf_access_persona_id()
        entries = [
            entry
            for entry in self._bookshelf_persisted_access_entries()
            if self._float(entry.get("expires_at")) > now
            and not hmac.compare_digest(str(entry.get("token_hash") or ""), digest)
        ]
        entries.insert(
            0,
            {
                "token_hash": digest,
                "expires_at": float(expires_at),
                "persona_id": persona_id,
            },
        )
        secret["web_access"] = {
            "version": 2,
            "tokens": entries[:BOOKSHELF_ACCESS_TOKEN_MAX_PERSISTED],
            "updated_at": now,
        }

    def _bookshelf_access_token_expires_at(self, token: Any) -> float:
        token_text = self._single_line(token, 120)
        if not token_text:
            return 0.0
        digest = self._bookshelf_access_token_digest(token_text)
        if not digest:
            return 0.0
        runtime_tokens = self._bookshelf_access_tokens()
        now = time.time()
        persona_id = self._bookshelf_access_persona_id()
        for entry in self._bookshelf_persisted_access_entries():
            entry_digest = str(entry.get("token_hash") or "")
            entry_persona = self._single_line(entry.get("persona_id"), 96)
            if entry_persona == persona_id and hmac.compare_digest(entry_digest, digest):
                expires_at = self._float(entry.get("expires_at"))
                if expires_at > now:
                    # Persisted records are authoritative for tokens that were
                    # explicitly saved, even if this process still has an older
                    # in-memory expiry cached for the same token.
                    runtime_tokens[token_text] = {
                        "expires_at": expires_at,
                        "persona_id": persona_id,
                    }
                    return expires_at
                runtime_tokens.pop(token_text, None)
                return 0.0
        runtime_entry = runtime_tokens.get(token_text)
        if isinstance(runtime_entry, dict):
            runtime_expiry = self._float(runtime_entry.get("expires_at"))
            runtime_persona = self._single_line(runtime_entry.get("persona_id"), 96)
            if runtime_persona == persona_id and runtime_expiry > now:
                return runtime_expiry
        elif not persona_id and self._float(runtime_entry) > now:
            return self._float(runtime_entry)
        return 0.0

    def _issue_bookshelf_access_token(self, *, persist: bool = False) -> str:
        token = secrets.token_urlsafe(24)
        expires_at = time.time() + BOOKSHELF_ACCESS_TOKEN_TTL_SECONDS
        self._bookshelf_access_tokens()[token] = {
            "expires_at": expires_at,
            "persona_id": self._bookshelf_access_persona_id(),
        }
        if persist:
            self._persist_bookshelf_access_token(token, expires_at)
        return token

    def _bookshelf_access_token_valid(self, token: Any) -> bool:
        return self._bookshelf_access_token_expires_at(token) > time.time()

    def _bookshelf_request_token(self, payload: dict[str, Any] | None = None) -> str:
        if isinstance(payload, dict):
            token = self._single_line(payload.get("access_token") or payload.get("token"), 120)
            if token:
                return token
        return self._single_line(request.args.get("access_token") or request.args.get("token"), 120)

    def _bookshelf_access_error(self) -> dict[str, str]:
        return {"error": "夹层访问已过期，请重新输入密码打开抽屉"}
