# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .helpers import _single_line
from .persona_config import runtime_persona_setting


_PLATFORM_KIND_ALIASES = {
    "aiocqhttp": "onebot",
    "onebot": "onebot",
    "napcat": "onebot",
    "llonebot": "onebot",
    "qq": "onebot",
    "qq_official": "qq_official",
    "qqofficial": "qq_official",
    "qqbot": "qq_official",
    "qq_bot": "qq_official",
    "qq官方": "qq_official",
    "官方qq": "qq_official",
    "webchat": "webchat",
    "telegram": "telegram",
    "telegram_bot": "telegram",
    "telegrambot": "telegram",
    "tg": "telegram",
    "weixin_official_account": "wechat_official",
    "wechat_official": "wechat_official",
    "dingtalk": "dingtalk",
}

_BASE_CAPABILITIES = {
    "opaque_identity": True,
    "onebot_actions": False,
    "poke": False,
    "input_status": False,
    "message_recall": False,
    "reply_quote": True,
    "segmented_reply": True,
    "merged_forward": False,
    "image": True,
    "voice": True,
    "file": True,
    "private_proactive": True,
    "group_proactive": True,
    "group_member_query": False,
    "qzone": False,
}

_PLATFORM_PROFILES: dict[str, dict[str, Any]] = {
    "onebot": {
        "label": "QQ / OneBot",
        "identity_label": "QQ号",
        "delivery_policy": "normal",
        "capabilities": {
            **_BASE_CAPABILITIES,
            "opaque_identity": False,
            "onebot_actions": True,
            "poke": True,
            "input_status": True,
            "message_recall": True,
            "reply_quote": True,
            "segmented_reply": True,
            "merged_forward": True,
            "group_member_query": True,
            "qzone": True,
        },
        "limits": [],
    },
    "qq_official": {
        "label": "QQ 官方机器人",
        "identity_label": "openid / 平台用户ID",
        "delivery_policy": "official_restricted",
        "capabilities": {
            **_BASE_CAPABILITIES,
            "opaque_identity": True,
            "onebot_actions": False,
            "poke": False,
            "input_status": False,
            "message_recall": False,
            "reply_quote": False,
            "segmented_reply": False,
            "merged_forward": False,
            "file": False,
            "private_proactive": True,
            "group_proactive": False,
            "group_member_query": False,
        },
        "limits": [
            "主动消息受 QQ 官方额度、时间窗和平台审核限制",
            "不使用 QQ 空间、OneBot 戳一戳、输入状态、原生撤回或合并转发动作",
            "用户身份使用稳定 openid，不要求转换为数字 QQ",
        ],
    },
    "webchat": {
        "label": "AstrBot WebChat",
        "identity_label": "会话用户ID",
        "delivery_policy": "normal",
        "capabilities": {**_BASE_CAPABILITIES},
        "limits": [],
    },
    "telegram": {
        "label": "Telegram",
        "identity_label": "Telegram 用户ID",
        "delivery_policy": "adapter_managed",
        "capabilities": {
            **_BASE_CAPABILITIES,
            "opaque_identity": True,
            "onebot_actions": False,
            "poke": False,
            "input_status": False,
            "message_recall": False,
            "merged_forward": False,
            "group_member_query": False,
            "qzone": False,
        },
        "limits": ["平台特有发送限制由 Telegram 适配器决定"],
    },
    "wechat_official": {
        "label": "微信公众号",
        "identity_label": "平台用户ID",
        "delivery_policy": "official_restricted",
        "capabilities": {
            **_BASE_CAPABILITIES,
            "reply_quote": False,
            "segmented_reply": False,
            "file": False,
            "group_proactive": False,
        },
        "limits": ["主动消息受平台会话窗口限制"],
    },
    "dingtalk": {
        "label": "钉钉",
        "identity_label": "平台用户ID",
        "delivery_policy": "adapter_managed",
        "capabilities": {
            **_BASE_CAPABILITIES,
            "reply_quote": False,
            "segmented_reply": False,
            "file": False,
        },
        "limits": [],
    },
    "generic": {
        "label": "其他 AstrBot 平台",
        "identity_label": "平台用户ID",
        "delivery_policy": "adapter_managed",
        "capabilities": {**_BASE_CAPABILITIES},
        "limits": ["平台特有动作由对应 AstrBot 适配器决定"],
    },
}


class PlatformCompatibilityMixin:
    """Per-event platform capability profile; no user-facing mode switch."""

    @staticmethod
    def _normalize_platform_kind(value: Any) -> str:
        text = _single_line(value, 80).strip().lower()
        compact = text.replace("-", "_").replace(" ", "")
        if compact in _PLATFORM_KIND_ALIASES:
            return _PLATFORM_KIND_ALIASES[compact]
        for token, kind in _PLATFORM_KIND_ALIASES.items():
            if token and token in compact:
                return kind
        return "generic"

    def _platform_kind_from_meta(self, meta: Any) -> str:
        if meta is None:
            return "generic"
        for attr in ("name", "id", "description"):
            kind = self._normalize_platform_kind(getattr(meta, attr, ""))
            if kind != "generic":
                return kind
        return "generic"

    def _platform_kind_for_event(self, event: Any | None) -> str:
        if event is None:
            return "generic"
        getter = getattr(event, "get_platform_name", None)
        if callable(getter):
            try:
                raw = getter()
            except Exception:
                raw = ""
            if raw:
                kind = self._normalize_platform_kind(raw)
                if kind != "generic":
                    return kind
        meta = getattr(event, "platform_meta", None) or getattr(event, "platform", None)
        meta_kind = self._platform_kind_from_meta(meta)
        if meta_kind != "generic":
            return meta_kind
        return self._platform_kind_for_umo(str(getattr(event, "unified_msg_origin", "") or ""))

    def _platform_kind_for_umo(self, umo: Any) -> str:
        text = _single_line(umo, 240)
        prefix = text.split(":", 1)[0] if ":" in text else text
        direct = self._normalize_platform_kind(prefix)
        if direct != "generic":
            return direct
        manager = getattr(getattr(self, "context", None), "platform_manager", None)
        if manager is not None and prefix:
            try:
                platforms = list(manager.get_insts())
            except Exception:
                platforms = list(getattr(manager, "platform_insts", []) or [])
            for platform in platforms:
                try:
                    meta = platform.meta()
                except Exception:
                    continue
                if prefix not in {
                    _single_line(getattr(meta, "id", ""), 80),
                    _single_line(getattr(meta, "name", ""), 80),
                }:
                    continue
                return self._platform_kind_from_meta(meta)
        target = self._normalize_platform_kind(getattr(self, "target_platform", ""))
        return target if target != "generic" and not prefix else "generic"

    def _preferred_platform_instance_id(self, *, kind: str = "") -> str:
        requested = _single_line(kind or getattr(self, "target_platform", ""), 80)
        desired = self._platform_kind_for_umo(requested) if requested else "generic"
        manager = getattr(getattr(self, "context", None), "platform_manager", None)
        if manager is None:
            return ""
        try:
            platforms = list(manager.get_insts())
        except Exception:
            platforms = list(getattr(manager, "platform_insts", []) or [])
        matches: list[str] = []
        active_instances: list[str] = []
        for platform in platforms:
            try:
                meta = platform.meta()
            except Exception:
                continue
            status = getattr(platform, "status", None)
            status_text = _single_line(
                getattr(status, "name", "") or getattr(status, "value", "") or status,
                40,
            ).lower()
            if status_text and "running" not in status_text and any(
                token in status_text for token in ("stop", "disabled", "closed", "error", "failed")
            ):
                continue
            instance_id = _single_line(getattr(meta, "id", "") or getattr(meta, "name", ""), 80)
            if instance_id and instance_id not in active_instances:
                active_instances.append(instance_id)
            if requested and requested in {
                _single_line(getattr(meta, "id", ""), 80),
                _single_line(getattr(meta, "name", ""), 80),
            }:
                return instance_id
            platform_kind = self._platform_kind_from_meta(meta)
            if platform_kind != desired:
                continue
            if instance_id and instance_id not in matches:
                matches.append(instance_id)
        if len(matches) == 1:
            return matches[0]
        if not matches and len(active_instances) == 1:
            return active_instances[0]
        return ""

    def _platform_profile(
        self,
        *,
        event: Any | None = None,
        umo: Any = "",
        kind: str = "",
    ) -> dict[str, Any]:
        resolved = self._normalize_platform_kind(kind) if kind else (
            self._platform_kind_for_event(event) if event is not None else self._platform_kind_for_umo(umo)
        )
        profile = deepcopy(_PLATFORM_PROFILES.get(resolved, _PLATFORM_PROFILES["generic"]))
        profile["kind"] = resolved if resolved in _PLATFORM_PROFILES else "generic"
        if profile["kind"] == "qq_official" and bool(
            runtime_persona_setting(self, "enable_qq_official_segmented_reply", False)
        ):
            profile["capabilities"]["segmented_reply"] = True
        raw_platform = ""
        getter = getattr(event, "get_platform_name", None) if event is not None else None
        if callable(getter):
            try:
                raw_platform = getter()
            except Exception:
                raw_platform = ""
        profile["raw_platform"] = _single_line(raw_platform, 80)
        return profile

    def _platform_supports(
        self,
        capability: str,
        *,
        event: Any | None = None,
        umo: Any = "",
        kind: str = "",
    ) -> bool:
        profile = self._platform_profile(event=event, umo=umo, kind=kind)
        return bool((profile.get("capabilities") or {}).get(str(capability or ""), False))

    def _platform_kind_available(self, kind: str) -> bool:
        desired = self._normalize_platform_kind(kind)
        manager = getattr(getattr(self, "context", None), "platform_manager", None)
        if manager is not None:
            try:
                platforms = list(manager.get_insts())
            except Exception:
                platforms = list(getattr(manager, "platform_insts", []) or [])
            if platforms:
                for platform in platforms:
                    status = getattr(platform, "status", None)
                    status_text = _single_line(
                        getattr(status, "name", "") or getattr(status, "value", "") or status,
                        40,
                    ).lower()
                    if status_text and "running" not in status_text and any(
                        token in status_text for token in ("stop", "disabled", "closed", "error", "failed")
                    ):
                        continue
                    try:
                        meta = platform.meta()
                    except Exception:
                        continue
                    if self._platform_kind_from_meta(meta) == desired:
                        return True
                return False
        data = getattr(self, "data", {}) if isinstance(getattr(self, "data", None), dict) else {}
        users = data.get("users") if isinstance(data.get("users"), dict) else {}
        observed_kinds = {
            self._platform_kind_for_umo(
                user.get("umo") or user.get("last_umo") or user.get("last_unified_msg_origin") or ""
            )
            for user in users.values()
            if isinstance(user, dict)
            and (user.get("umo") or user.get("last_umo") or user.get("last_unified_msg_origin"))
        }
        observed_kinds.discard("generic")
        if observed_kinds:
            return desired in observed_kinds
        target_kind = self._platform_kind_for_umo(getattr(self, "target_platform", ""))
        return target_kind == desired

    def _platform_capability_prompt(self, event: Any | None) -> str:
        profile = self._platform_profile(event=event)
        if profile.get("kind") != "qq_official":
            return ""
        return (
            "【QQ 官方机器人平台边界】\n"
            "当前用户身份是 openid/平台用户ID，可以正常作为稳定私聊身份使用，不要要求用户提供数字 QQ 号。\n"
            "可以通过当前 AstrBot 会话发送普通文字、图片和已配置的语音；只有实际工具/发送结果成功后才能说已发送。\n"
            "当前平台不支持 OneBot 戳一戳、输入状态、原生撤回、指定消息引用或合并转发；不要承诺执行这些动作，应自然改用普通文字。\n"
            "QQ 官方机器人不支持 QQ 空间读取、发布、点赞或评论；不要调用或承诺使用 QQ 空间能力。\n"
            "主动私聊还受 QQ 官方额度、会话时间窗和审核限制，发送失败时如实说明，不能假装已经触达。"
        )

    def _platform_adaptation_overview(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        target_kind = self._platform_kind_for_umo(getattr(self, "target_platform", ""))
        counts[target_kind] = counts.get(target_kind, 0) + 1
        data = getattr(self, "data", {}) if isinstance(getattr(self, "data", None), dict) else {}
        users = data.get("users") if isinstance(data.get("users"), dict) else {}
        for user in users.values():
            if not isinstance(user, dict):
                continue
            umo = user.get("umo") or user.get("last_umo") or user.get("last_unified_msg_origin") or ""
            if not umo:
                continue
            kind = self._platform_kind_for_umo(umo)
            counts[kind] = counts.get(kind, 0) + 1
        profiles = []
        for kind, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
            profile = self._platform_profile(kind=kind)
            profiles.append(
                {
                    "kind": kind,
                    "label": profile.get("label"),
                    "identity_label": profile.get("identity_label"),
                    "delivery_policy": profile.get("delivery_policy"),
                    "observed_targets": count,
                    "capabilities": profile.get("capabilities"),
                    "limits": profile.get("limits"),
                }
            )
        return {
            "auto_detect": True,
            "manual_mode_required": False,
            "target_platform": _single_line(getattr(self, "target_platform", ""), 80),
            "target_kind": target_kind,
            "qq_official_detected": bool(counts.get("qq_official")),
            "profiles": profiles,
        }
