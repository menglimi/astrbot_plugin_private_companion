# -*- coding: utf-8 -*-
from __future__ import annotations

import time
from typing import Any

from astrbot.api import logger

from .helpers import _safe_float, _single_line
from .external_bridge_resolver import resolve_external_bridge
from .conversation_prompt_section import prompt_section


class RealityCompanionBridgeMixin:
    """Optional bridge to astrbot_plugin_reality_companion.

    The main companion intentionally owns no device implementation. These
    adapters preserve the old call surface for prompts, commands and timers.
    """

    def _reality_companion_api(self) -> Any | None:
        return resolve_external_bridge(
            self,
            cache_key="reality_companion",
            module_names=(
                "data.plugins.astrbot_plugin_reality_companion.main",
                "astrbot_plugin_reality_companion.main",
            ),
            getter_name="get_reality_companion_api",
            star_name="astrbot_plugin_reality_companion",
        )

    @staticmethod
    def _reality_bridge_user_id(user: Any) -> str:
        if isinstance(user, dict):
            return _single_line(user.get("user_id"), 120)
        return _single_line(user, 120)

    def _reality_touch_audio_consented(self, user: dict[str, Any]) -> bool:
        api = self._reality_companion_api()
        checker = getattr(api, "audio_consented", None) if api is not None else None
        return bool(callable(checker) and checker(self._reality_bridge_user_id(user)))

    async def _record_reality_touch_output(
        self,
        user_id: str,
        text: str,
        *,
        source: str = "reality_touch_audio",
        delivered_at: float | None = None,
    ) -> dict[str, Any]:
        normalized_user_id = _single_line(user_id, 120)
        visible = _single_line(text, 500)
        if not normalized_user_id or not visible:
            return {"recorded": False, "reason": "invalid_payload"}
        api = self._reality_companion_api()
        external_recorder = getattr(api, "record_reality_touch_output", None) if api is not None else None
        if callable(external_recorder):
            try:
                return await external_recorder(
                    normalized_user_id,
                    visible,
                    source=_single_line(source, 80) or "reality_touch_audio",
                    delivered_at=delivered_at,
                )
            except Exception as exc:
                logger.warning("[PrivateCompanion] 外部现实触及输出写入失败: %s", _single_line(exc, 160))
                return {"recorded": False, "reason": "reality_companion_write_failed"}
        return {"recorded": False, "reason": "reality_companion_unavailable"}

    def _format_reality_touch_continuity_context(
        self,
        user: dict[str, Any],
        *,
        as_section: bool = False,
    ) -> str | dict[str, Any]:
        user_id = self._reality_bridge_user_id(user)
        api = self._reality_companion_api()
        reader = getattr(api, "recent_output", None) if api is not None else None
        output = None
        if callable(reader):
            try:
                candidate = reader(user_id)
                output = candidate if isinstance(candidate, dict) else None
            except Exception:
                output = None

        # Read-only projection for installations that have not yet imported
        # their historical reality state into the split plugin.
        binder = getattr(self, "_req041_reality_private_binding", None)
        binding = binder(user_id, purpose="memory_read") if callable(binder) else None
        if callable(binder) and (not isinstance(binding, dict) or binding.get("ok") is not True):
            return ""
        continuity_user = binding.get("user") if isinstance(binding, dict) else user
        if not isinstance(continuity_user, dict):
            continuity_user = user
        if not isinstance(output, dict):
            if isinstance(binding, dict):
                root = self.data.get("reality_touch_outputs") if isinstance(getattr(self, "data", None), dict) else None
                output = root.get(_single_line(binding.get("store_key"), 160)) if isinstance(root, dict) else None
            else:
                output = user.get("last_reality_touch_output") if isinstance(user, dict) else None
        if not isinstance(output, dict):
            return ""
        text = _single_line(output.get("text"), 300)
        delivered_at = _safe_float(output.get("delivered_at"), 0.0, 0.0)
        age = time.time() - delivered_at
        if not text or delivered_at <= 0 or age < -60 or age > 2 * 3600:
            return ""
        lines = [
            f"Bot 已通过现实音频设备对用户说：{text}",
        ]
        user_text = _single_line(continuity_user.get("last_user_message"), 300)
        user_at = _safe_float(continuity_user.get("last_user_message_at"), 0.0, 0.0)
        if user_text and user_at >= delivered_at:
            lines.append(f"用户随后在私聊回应：{user_text}")
        lines.append(
            "这是真实发生且与当前私聊连续的对话。自然承接用户此刻的回应；不要把它当作首次问候，也不要重复刚才已经说过的话。"
        )
        body = "\n".join(lines)
        return prompt_section("刚刚发生的跨设备对话", body) if as_section else f"【刚刚发生的跨设备对话】\n{body}"

    def _reality_companion_enabled(self) -> bool:
        api = self._reality_companion_api()
        getter = getattr(api, "status", None) if api is not None else None
        if not callable(getter):
            return False
        try:
            status = getter()
        except Exception:
            return False
        return bool(isinstance(status, dict) and status.get("enabled"))

    def _reality_mobile_context(self, user_id: Any = "") -> dict[str, Any]:
        """Return the short-lived, coarse Android location context when available."""
        api = self._reality_companion_api()
        getter = getattr(api, "mobile_context", None) if api is not None else None
        normalized = _single_line(user_id, 120)
        if not callable(getter) or not normalized:
            return {"available": False, "user_id": normalized, "location": {"available": False}}
        try:
            value = getter(normalized)
        except Exception:
            return {"available": False, "user_id": normalized, "location": {"available": False}}
        return value if isinstance(value, dict) else {"available": False, "user_id": normalized, "location": {"available": False}}

    def _reality_touch_proactive_voice_allowed(self, user: dict[str, Any]) -> bool:
        api = self._reality_companion_api()
        checker = getattr(api, "proactive_voice_allowed", None) if api is not None else None
        return bool(callable(checker) and checker(self._reality_bridge_user_id(user)))

    async def _mirror_reality_touch_proactive_voice(self, user: dict[str, Any], audio_path: str) -> bool:
        api = self._reality_companion_api()
        mirror = getattr(api, "mirror_proactive_voice", None) if api is not None else None
        if not callable(mirror):
            return False
        return bool(await mirror(self._reality_bridge_user_id(user), audio_path))

    def _reality_touch_camera_user_eligible(self, user_id: Any) -> bool:
        api = self._reality_companion_api()
        checker = getattr(api, "camera_user_eligible", None) if api is not None else None
        return bool(callable(checker) and checker(_single_line(user_id, 120)))

    def _reality_touch_camera_proactive_state(
        self,
        user: dict[str, Any],
        *,
        user_id: str = "",
    ) -> dict[str, Any]:
        api = self._reality_companion_api()
        getter = getattr(api, "camera_proactive_state", None) if api is not None else None
        normalized = _single_line(user_id, 120) or self._reality_bridge_user_id(user)
        if not callable(getter):
            return {"available": False, "direct_allowed": False, "reason": "reality_companion_missing"}
        result = getter(normalized)
        return result if isinstance(result, dict) else {"available": False, "direct_allowed": False}

    def _reality_touch_camera_proactive_prompt(
        self,
        user: dict[str, Any],
        *,
        user_id: str = "",
    ) -> str:
        api = self._reality_companion_api()
        getter = getattr(api, "camera_proactive_prompt", None) if api is not None else None
        normalized = _single_line(user_id, 120) or self._reality_bridge_user_id(user)
        return str(getter(normalized) or "") if callable(getter) else ""

    async def _schedule_reality_touch_official_reminder(
        self,
        user_id: str,
        payload: dict[str, Any],
        *,
        source_text: str,
        trigger_umo: str = "",
    ) -> bool:
        api = self._reality_companion_api()
        scheduler = getattr(api, "schedule_reminder", None) if api is not None else None
        if not callable(scheduler):
            logger.info("[PrivateCompanion] 未安装或未启用“我会来到你身边”，现实提醒未创建")
            return False
        return bool(
            await scheduler(
                _single_line(user_id, 120),
                payload,
                source_text=source_text,
                trigger_umo=trigger_umo,
            )
        )

    def _wakeup_alarm_command(self, user: dict[str, Any], value: str) -> tuple[str, Any]:
        api = self._reality_companion_api()
        handler = getattr(api, "legacy_command", None) if api is not None else None
        if not callable(handler):
            return (
                "现实触及已拆分为联动插件“我会来到你身边”。请先安装并启用 "
                "astrbot_plugin_reality_companion。",
                False,
            )
        return handler(
            self._reality_bridge_user_id(user),
            value,
            umo=_single_line(user.get("umo"), 180),
        )

    async def _test_wakeup_alarm(self, user: dict[str, Any]) -> None:
        api = self._reality_companion_api()
        tester = getattr(api, "test_wakeup", None) if api is not None else None
        if callable(tester):
            await tester(self._reality_bridge_user_id(user))

    async def _reality_touch_camera_snapshot_for_user(
        self,
        user_id: str,
        purpose: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        api = self._reality_companion_api()
        snapshotter = getattr(api, "camera_snapshot", None) if api is not None else None
        if not callable(snapshotter):
            return {"status": "unavailable", "message": "未安装或未启用“我会来到你身边”"}
        return await snapshotter(_single_line(user_id, 120), purpose, **kwargs)

    def _reality_touch_apply_pending_confirmation(self, user: dict[str, Any], text: str) -> str | None:
        api = self._reality_companion_api()
        handler = getattr(api, "apply_pending_confirmation", None) if api is not None else None
        return handler(self._reality_bridge_user_id(user), text) if callable(handler) else None
