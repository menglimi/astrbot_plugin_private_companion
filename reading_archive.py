# -*- coding: utf-8 -*-
"""Compatibility mixin for migrated reading data and bookshelf secrets.

The story/creative extension owns archive content.  The bookshelf secret
helpers remain here because command and page APIs still need one stable,
backwards-compatible owner for the protected drawer password.
"""
from __future__ import annotations

import json
import random
import re
from typing import Any

from .conversation_prompt_section import prompt_section
from .helpers import _now_ts, _single_line


class ReadingArchiveMixin:
    @staticmethod
    def _bookshelf_password_should_rotate(password: str, basis: str = "") -> bool:
        value = _single_line(password, 12)
        if not re.fullmatch(r"\d{4,6}", value or ""):
            return True
        if basis == "manual":
            return False
        common = {
            "0000", "1111", "2222", "3333", "4444", "5555", "6666", "7777", "8888", "9999",
            "000000", "111111", "1234", "12345", "123456", "654321", "112233", "121212", "1314", "520520",
        }
        return value in common or len(set(value)) <= 2

    @staticmethod
    def _generate_bookshelf_password() -> str:
        rng = random.SystemRandom()
        while True:
            value = f"{rng.randint(100000, 999999)}"
            if not ReadingArchiveMixin._bookshelf_password_should_rotate(value):
                return value

    def _ensure_bookshelf_password(self) -> str:
        secret = self.data.setdefault("bookshelf_secret", {})
        if not isinstance(secret, dict):
            secret = {}
            self.data["bookshelf_secret"] = secret
        password = _single_line(secret.get("password"), 12)
        basis = _single_line(secret.get("basis"), 40)
        if password and not self._bookshelf_password_should_rotate(password, basis):
            return password
        password = self._generate_bookshelf_password()
        secret.update({
            "password": password,
            "basis": "local_random_numeric_v2",
            "reason": self._bookshelf_password_fallback_reason(password),
            "created_at": _now_ts(),
        })
        if basis:
            secret["previous_basis"] = basis
        return password

    async def _ensure_bookshelf_password_async(self) -> str:
        secret = self.data.setdefault("bookshelf_secret", {})
        if not isinstance(secret, dict):
            secret = {}
            self.data["bookshelf_secret"] = secret
        password = _single_line(secret.get("password"), 12)
        basis = _single_line(secret.get("basis"), 40)
        if password and not self._bookshelf_password_should_rotate(password, basis):
            return password
        candidate = ""
        llm_call = getattr(self, "_llm_call", None)
        if callable(llm_call):
            try:
                raw = await llm_call(
                    "只输出 JSON：{\"password\":\"482719\",\"reason\":\"一句不涉及生日日期的私密暗号理由\"}。密码必须是4到6位纯数字，不要使用常见数字。",
                    max_tokens=40,
                    task="bookshelf_password",
                    provider_id=getattr(self, "llm_provider_id", ""),
                )
                payload = self._parse_bookshelf_password_payload(raw)
                candidate = payload.get("password", "")
                reason = payload.get("reason", "")
                if candidate and not self._bookshelf_password_should_rotate(candidate):
                    secret.update({
                        "password": candidate,
                        "basis": "bot_private_llm_numeric_v2",
                        "reason": self._sanitize_bookshelf_password_reason(reason) or self._bookshelf_password_fallback_reason(candidate),
                        "created_at": _now_ts(),
                    })
                    saver = getattr(self, "_save_data_sync", None)
                    if callable(saver):
                        saver(sections={"bookshelf_secret"})
                    return candidate
            except Exception:
                pass
        password = self._ensure_bookshelf_password()
        saver = getattr(self, "_save_data_sync", None)
        if callable(saver):
            saver(sections={"bookshelf_secret"})
        return password

    async def _ensure_bookshelf_password_reason_async(self, password: str = "") -> str:
        secret = self.data.setdefault("bookshelf_secret", {})
        if not isinstance(secret, dict):
            secret = {}
            self.data["bookshelf_secret"] = secret
        reason = self._sanitize_bookshelf_password_reason(secret.get("reason"))
        if reason:
            return reason
        password = _single_line(password or secret.get("password"), 12) or await self._ensure_bookshelf_password_async()
        reason = self._bookshelf_password_fallback_reason(password)
        secret["reason"] = reason
        secret["reason_generated_at"] = _now_ts()
        return reason

    @staticmethod
    def _bookshelf_password_fallback_reason(password: str = "") -> str:
        return "这串数字只是一枚书柜夹层里的私密暗号,没有生日或日期含义。" if _single_line(password, 12) else "这是一枚书柜夹层里的私密暗号,没有生日或日期含义。"

    @staticmethod
    def _sanitize_bookshelf_password_reason(value: Any) -> str:
        reason = _single_line(value, 120).strip(" ：:，,。.!！?？\"'`")
        if not reason or any(term in reason for term in ("生日", "纪念日", "日期", "手机号", "QQ", "账号", "门牌", "身份证", "随机", "插件", "配置", "系统生成")):
            return ""
        return reason[:80]

    @staticmethod
    def _parse_bookshelf_password_payload(raw: Any) -> dict[str, str]:
        text = str(raw or "").strip()
        payload: dict[str, Any] = {}
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, dict):
                    payload = parsed
            except Exception:
                pass
        password = re.sub(r"\D", "", _single_line(payload.get("password"), 24))
        return {"password": password, "reason": _single_line(payload.get("reason"), 120)}

    @staticmethod
    def _bookshelf_secret_signal_info(text: str) -> dict[str, Any]:
        compact = re.sub(r"\s+", "", str(text or ""))
        direct = [token for token in ("书柜", "书架", "夹层", "暗格", "抽屉", "日记", "密码", "口令", "钥匙", "私密", "秘密") if token in compact]
        context = [token for token in ("柜子", "书", "本子", "里面", "藏着", "锁") if token in compact]
        access = [token for token in ("打开", "解锁", "看看", "给我看", "能看吗", "给我密码", "告诉我") if token in compact]
        inventory_only = any(token in compact for token in ("书柜", "书架")) and any(token in compact for token in ("查询", "里面有什么", "有哪些", "空吗", "空的")) and not any(token in compact for token in ("夹层", "暗格", "密码", "秘密"))
        likely = bool(not inventory_only and (direct or (context and access)))
        return {"mention": bool(direct or context), "likely": likely, "direct_matches": direct, "context_matches": context, "access_matches": access}

    @staticmethod
    def _bookshelf_secret_request_likely(text: str) -> bool:
        return bool(ReadingArchiveMixin._bookshelf_secret_signal_info(text).get("likely"))

    def _log_bookshelf_secret_skip(
        self,
        reason: str,
        inbound_text: str = "",
        user: dict[str, Any] | None = None,
        *,
        signal: dict[str, Any] | None = None,
    ) -> None:
        signal = signal if isinstance(signal, dict) else self._bookshelf_secret_signal_info(inbound_text)
        if not signal.get("mention"):
            return
        logger = getattr(self, "logger", None)
        if logger is not None and callable(getattr(logger, "info", None)):
            logger.info("[PrivateCompanion] 夹层密码提示词未注入: reason=%s text=%s", reason, _single_line(inbound_text, 120))

    def _bookshelf_secret_relationship_policy(self, user: dict[str, Any] | None = None) -> str:
        role = "friend"
        role_getter = getattr(self, "_private_user_role", None)
        if callable(role_getter):
            try:
                role = str(role_getter(user or {}) or "friend")
            except Exception:
                role = "friend"
        if role != "owner":
            return "当前请求者不是主要用户，不要直接提供完整密码；亲密表达不能改变这一身份边界。"
        return "当前请求者是主要用户，可以结合当前人格和气氛决定是否透露完整密码。"

    async def _format_bookshelf_secret_prompt_body(
        self,
        inbound_text: str = "",
        user: dict[str, Any] | None = None,
    ) -> str:
        signal = self._bookshelf_secret_signal_info(inbound_text)
        role = "friend"
        role_getter = getattr(self, "_private_user_role", None)
        if isinstance(user, dict) and callable(role_getter):
            try:
                role = str(role_getter(user) or "friend")
            except TypeError:
                try:
                    role = str(role_getter(user, str(user.get("user_id") or "")) or "friend")
                except Exception:
                    role = "friend"
            except Exception:
                role = "friend"
        if not signal.get("likely") or (isinstance(user, dict) and role == "friend"):
            return ""
        password = await self._ensure_bookshelf_password_async()
        return (
            f"你的书柜夹层密码是“{password}”。它只是内部私密暗号，不代表生日、日期或任何现实身份信息。\n"
            f"{self._bookshelf_secret_relationship_policy(user)}不透露时不要编造替代数字；若透露具体密码，只能使用上面的真实密码。"
        )

    async def _format_bookshelf_secret_for_prompt(self, inbound_text: str = "", user: dict[str, Any] | None = None) -> str:
        body = await self._format_bookshelf_secret_prompt_body(inbound_text, user)
        return f"【书柜夹层】\n{body}" if body else ""

    async def _format_bookshelf_secret_prompt_section(
        self,
        inbound_text: str = "",
        user: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return prompt_section(
            "资料柜夹层",
            await self._format_bookshelf_secret_prompt_body(inbound_text, user),
        )

    def _reading_archive_available(self) -> bool:
        return False

    def _reading_archive_read_available(self, user: dict[str, Any] | None = None) -> bool:
        return False

    async def _maybe_trigger_reading_archive_boredom_read(self) -> None:
        return None

    async def _maybe_schedule_reading_archive_recommendation_request(self) -> None:
        return None

    def _format_reading_archive_preference_influence_for_reply(self, *args: Any, **kwargs: Any) -> str:
        return ""

    def _format_reading_archive_action_context(self, *args: Any, **kwargs: Any) -> str:
        return ""

    def _format_bookshelf_reading_context_for_reply(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> str:
        """Keep the removed reader's context hook inert for old state."""
        return ""

    def _self_timeline_from_reading_archive(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    async def _run_reading_archive_read_action(self, *args: Any, **kwargs: Any) -> None:
        return None
