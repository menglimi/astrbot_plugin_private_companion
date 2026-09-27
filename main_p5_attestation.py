# -*- coding: utf-8 -*-
"""P5 来源证实域。

由 tools/split_main_domain.py 从 main.py 机械抽取（8 个方法 / 192 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPlugin）。
"""
from __future__ import annotations

import hashlib
from .conversation_prompt_section import PromptRenderMode, render_prompt_sections
from .helpers import _resolve_timezone_setting, _single_line
from .p5_attestation import P5AttestationError, REASON_CODES as P5_ATTESTATION_REASON_CODES
from .p5_source_observer import evaluate_source
from astrbot.api.event import AstrMessageEvent
from datetime import datetime
from typing import Any


class PrivateCompanionPluginP5AttestationMixin:
    """P5 来源证实域（从 PrivateCompanionPlugin 拆出）。"""

    @staticmethod
    def _p5_hash(value: Any) -> str:
        return hashlib.sha256(str(value).encode("utf-8", errors="ignore")).hexdigest()

    @staticmethod
    def _p5_event_reference(event: Any | None) -> str:
        if event is None:
            return "private_companion:background"
        for value in (
            getattr(event, "private_companion_p5_event_ref", ""),
            getattr(event, "message_id", ""),
            getattr(event, "unified_msg_origin", ""),
        ):
            text = _single_line(value, 120)
            if text:
                return text
        return f"private_companion:event:{id(event)}"

    @staticmethod
    def _p5_detect_source_kind(event: Any | None, explicit: str = "") -> str:
        candidate = _single_line(explicit, 60)
        if not candidate and event is not None:
            for attr in ("private_companion_p5_source_kind", "p5_source_kind"):
                candidate = _single_line(getattr(event, attr, ""), 60)
                if candidate:
                    break
        if not candidate and event is not None:
            components: list[Any] = []
            for attr in ("message_obj", "message_chain", "message", "message_components"):
                value = getattr(event, attr, None)
                if isinstance(value, (list, tuple)):
                    components.extend(value[:24])
                elif value is not None:
                    components.append(value)
            component_names = {type(item).__name__.lower() for item in components}
            if any("forward" in name for name in component_names):
                candidate = "forwarded_text"
            elif any("reply" in name or "quote" in name for name in component_names):
                candidate = "quoted_text"
            elif any("image" in name or "visual" in name for name in component_names):
                candidate = "vision_summary"
        allowed = {
            "policy_config", "verified_authorization", "current_user_intent", "forwarded_text",
            "quoted_text", "vision_summary", "tool_output", "web_extract", "memory_recall",
            "derived_summary", "legacy_memory", "unknown",
        }
        if candidate in allowed:
            return candidate
        return "current_user_intent" if event is not None else "policy_config"

    def _p5_issue_attestation_for_event(
        self,
        *,
        event: Any | None,
        request: Any | None,
        sink: str,
        source_kind: str = "",
    ) -> tuple[Any, Any] | None:
        """Mint a one-shot handle and bound consumer for a local Bridge call."""
        if not bool(getattr(self, "enable_p5_source_observer", False)):
            return None

        event_anchor = event if event is not None else object()
        request_anchor = request if request is not None else event_anchor
        p3_state = getattr(event_anchor, "private_companion_p5_p3_state", None)
        if p3_state is None:
            p3_state = object()
            try:
                setattr(event_anchor, "private_companion_p5_p3_state", p3_state)
            except Exception:
                pass
        source_kind = self._p5_detect_source_kind(event_anchor, source_kind)
        event_ref = self._p5_event_reference(event_anchor)
        observation = evaluate_source(
            {
                "source_kind": source_kind,
                "trust": {
                    "policy_config": "T0",
                    "verified_authorization": "T1",
                    "current_user_intent": "T2",
                    "forwarded_text": "T3",
                    "quoted_text": "T3",
                    "vision_summary": "T3",
                    "tool_output": "T3",
                    "web_extract": "T3",
                    "memory_recall": "T4",
                    "derived_summary": "T4",
                    "legacy_memory": "T4",
                    "unknown": "T4",
                }[source_kind],
                "sink": sink,
                "event_id": event_ref,
                "security_state": "allowed",
            }
        )
        trust = str(observation.get("trust") or "T4")
        source_event_ref_hash = _single_line(observation.get("safe_ref_hash"), 80) or self._p5_hash(event_ref)
        reasons = [
            code for code in observation.get("reason_codes", [])
            if code in P5_ATTESTATION_REASON_CODES
        ]
        if not reasons:
            reasons = ["invalid_segment"]
        firewall_status = str(observation.get("security_state") or "unknown")
        if firewall_status == "not_supplied":
            firewall_status = "unknown"
        try:
            handle = self.p5_attestation_registry.mint(
                request_anchor,
                event_anchor,
                p3_state,
                request_hash=self._p5_hash(f"request:{id(request_anchor)}"),
                session_hash=self._p5_hash(getattr(event_anchor, "unified_msg_origin", "background")),
                source_kind=source_kind,
                source_trust=trust,
                firewall_status=firewall_status,
                disposition=str(observation.get("disposition") or "shadow_quarantine"),
                reason_codes=reasons,
                source_event_ref_hash=source_event_ref_hash,
                sinks=(sink,),
            )
        except (P5AttestationError, KeyError, TypeError, ValueError):
            return None
        if handle is None:
            return None

        def consume(candidate: Any, requested_sink: str = sink) -> Any:
            return self.p5_attestation_registry.consume(
                candidate,
                request_anchor,
                event_anchor,
                p3_state,
                requested_sink,
            )

        return handle, consume

    def p5_source_observer_status(self) -> dict[str, Any]:
        return {
            "schema_version": "ops.p5.source_observer.v1",
            "enabled": bool(getattr(self, "enable_p5_source_observer", False)),
            "attestation": "available" if bool(getattr(self, "enable_p5_source_observer", False)) else "disabled",
            "bridge_gate": bool(getattr(self, "enable_p5_b1_bridge_gate", False)),
            "recall_gate": bool(getattr(self, "enable_p5_b1_recall_gate", False)),
            "execution_authority": "none",
        }

    @staticmethod
    def _normalize_weather_alert_min_severity(value: Any) -> str:
        """Normalize weather alert color thresholds while accepting common aliases."""
        normalized = str(value or "blue").strip().lower()
        aliases = {
            "蓝": "blue",
            "蓝色": "blue",
            "黄色": "yellow",
            "黄": "yellow",
            "橙": "orange",
            "橙色": "orange",
            "红": "red",
            "红色": "red",
            "全部": "all",
            "全部级别": "all",
            "全部等级": "all",
            "所有": "all",
            "any": "all",
        }
        normalized = aliases.get(normalized, normalized)
        return normalized if normalized in {"blue", "yellow", "orange", "red", "all"} else "blue"

    def _resolve_environment_perception_timezone(self, configured_timezone: Any) -> str:
        global_timezone = ""
        try:
            global_config = self.context.get_config()
            if callable(getattr(global_config, "get", None)):
                global_timezone = str(global_config.get("timezone") or "").strip()
        except Exception:
            global_timezone = ""
        local_tzinfo = datetime.now().astimezone().tzinfo
        system_timezone = str(getattr(local_tzinfo, "key", "") or local_tzinfo or "").strip()
        return _resolve_timezone_setting(
            configured_timezone,
            global_timezone=global_timezone,
            system_timezone=system_timezone,
        )

    async def _format_passive_environment_fragment(
        self,
        event: AstrMessageEvent,
        *,
        lightweight: bool = False,
    ) -> str:
        section = await self._format_passive_environment_prompt_section(
            event,
            lightweight=lightweight,
        )
        return render_prompt_sections(
            [section],
            mode=PromptRenderMode.LABELED_BLOCK,
        )
