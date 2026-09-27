# -*- coding: utf-8 -*-
"""LlmToolActionsReactionCorePart01Mixin。

由 tools/split_mixin_domain.py 从 llm_tool_actions_reaction_core.py 机械抽取（15 个方法 + 0 个模块级名字 + 0 个类级赋值 / 370 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 LlmToolActionsReactionCoreMixin）。
"""
from __future__ import annotations
from .llm_tool_actions_reaction_core_shared import Any
from .llm_tool_actions_reaction_core_shared import OwnedReactionAssetCatalog
from .llm_tool_actions_reaction_core_shared import _safe_int
from .llm_tool_actions_reaction_core_shared import _single_line
from .llm_tool_actions_reaction_core_shared import _strip_internal_message_blocks
from .llm_tool_actions_reaction_core_shared import html
from .llm_tool_actions_reaction_core_shared import json
from .llm_tool_actions_reaction_core_shared import logger
from .llm_tool_actions_reaction_core_shared import normalize_reaction_expression_intent
from .llm_tool_actions_reaction_core_shared import re
from .llm_tool_actions_reaction_core_shared import reaction_expression_explicit_opt_out
from .llm_tool_actions_reaction_core_shared import reaction_expression_explicit_request
from .llm_tool_actions_reaction_core_shared import runtime_persona_setting



class LlmToolActionsReactionCorePart01Mixin:
    """LlmToolActionsReactionCorePart01Mixin（从 LlmToolActionsReactionCoreMixin 拆出）。"""


    def _find_owned_reaction_asset(
        self,
        query: str,
        *,
        search_context: str = "",
        meme_only: bool = True,
    ) -> dict[str, Any] | None:
        if not bool(runtime_persona_setting(self, 'enable_owned_reaction_asset_workbench', False)):
            return None
        catalog = OwnedReactionAssetCatalog(getattr(self, "data_dir", ""))
        asset, status, confidence = catalog.find(
            runtime_persona_setting(self, 'owned_reaction_assets', []),
            query=query,
            search_context=search_context,
            meme_only=bool(meme_only),
        )
        if asset is None:
            logger.debug(
                "Q6 自有反应图未命中: status=%s",
                status,
            )
            return None
        return {
            "success": True,
            "status": "success",
            "source": "owned_reaction_assets",
            "path": str(asset.path),
            "image_id": asset.asset_id,
            "tags": list(asset.tags),
            "need": _single_line(query, 220),
            "reason": "管理员登记的受管自有反应图标签命中",
            "confidence": confidence,
        }

    def _reaction_image_provider_available(self) -> bool:
        library = self._reaction_asset_library()
        return bool(
            library and library.has_enabled_assets()
        ) or bool(
            runtime_persona_setting(self, 'enable_owned_reaction_asset_workbench', False)
            and runtime_persona_setting(self, 'owned_reaction_assets', [])
        )

    @staticmethod
    def _reaction_expression_opt_out_requested(text: Any) -> bool:
        return reaction_expression_explicit_opt_out(text)

    @staticmethod
    def _reaction_expression_explicit_request_matches(text: Any) -> bool:
        return reaction_expression_explicit_request(text)

    def _reaction_expression_event_storage_id(self, event: Any, user_id: Any) -> str:
        """Resolve an event sender to the platform/account-scoped users key."""
        raw_id = _single_line(user_id, 160)
        if not raw_id:
            return ""
        # Callers may feed the already-resolved storage key back into a later
        # state step. Do not namespace that key a second time.
        try:
            event_sender = _single_line(event.get_sender_id(), 160)
        except Exception:
            event_sender = ""
        platform_getter = getattr(self, "_platform_kind_for_event", None)
        try:
            platform = _single_line(platform_getter(event), 40).lower() if callable(platform_getter) else ""
        except Exception:
            platform = ""
        if event_sender and raw_id != event_sender and platform and raw_id.startswith(f"{platform}:"):
            return raw_id
        resolver = getattr(self, "_private_user_id_for_event", None)
        if callable(resolver):
            try:
                resolved = _single_line(resolver(event, raw_id), 160)
            except Exception:
                resolved = ""
            if resolved:
                return resolved
        return raw_id

    def _reaction_expression_feedback_user(
        self,
        user_id: Any,
        text: Any,
        *,
        create_for_opt_out: bool = False,
        event: Any = None,
    ) -> dict[str, Any] | None:
        """Resolve the canonical user that owns per-conversation feedback."""
        normalized_id = _single_line(user_id, 160)
        if event is not None and self._reaction_expression_scope(event) == "group":
            return self._reaction_expression_state_owner(
                event,
                normalized_id,
                create=bool(create_for_opt_out and reaction_expression_explicit_opt_out(text)),
            )
        if event is not None:
            normalized_id = self._reaction_expression_event_storage_id(event, normalized_id)
        data = getattr(self, "data", None)
        users = data.get("users") if isinstance(data, dict) else None
        if not normalized_id or not isinstance(users, dict):
            return None

        canonical_id = normalized_id
        canonicalizer = getattr(self, "_canonical_private_user_id", None)
        if callable(canonicalizer):
            try:
                canonical_id = (
                    _single_line(canonicalizer(normalized_id), 160)
                    or normalized_id
                )
            except Exception:
                canonical_id = normalized_id

        for candidate_id in dict.fromkeys((normalized_id, canonical_id)):
            candidate = users.get(candidate_id)
            if isinstance(candidate, dict):
                return candidate
        for candidate in users.values():
            if not isinstance(candidate, dict):
                continue
            aliases = candidate.get("alias_user_ids")
            if (
                _single_line(candidate.get("user_id"), 160) == normalized_id
                or isinstance(aliases, list) and normalized_id in aliases
            ):
                return candidate

        if not (
            create_for_opt_out
            and reaction_expression_explicit_opt_out(text)
        ):
            return None
        getter = getattr(self, "_get_user", None)
        if not callable(getter):
            return None
        try:
            created = getter(canonical_id)
        except Exception:
            return None
        return created if isinstance(created, dict) else None

    def _mark_reaction_asset_used(
        self,
        image_id: Any,
        *,
        event: Any = None,
        trace_id: str = "",
    ) -> None:
        normalized = _single_line(image_id, 160)
        if not normalized.startswith("pc-local:"):
            return
        library = self._reaction_asset_library()
        if library is None:
            return
        try:
            library.mark_used(normalized)
        except Exception as exc:
            self._log_reaction_expression_event(
                event,
                trace_id=trace_id,
                stage="degrade",
                decision="failed",
                reason="usage_mark_failed",
                image_id=normalized,
                error_type=type(exc).__name__,
            )

    @staticmethod
    def _mark_private_companion_skip_reaction_expression(event: Any) -> None:
        """Mark this event after a real image delivery to avoid a second reaction image."""
        if event is None:
            return
        try:
            setattr(event, "_private_companion_skip_reaction_expression", True)
        except Exception:
            pass
        setter = getattr(event, "set_extra", None)
        if callable(setter):
            try:
                setter("private_companion_skip_reaction_expression", True)
            except Exception:
                pass

    def _reaction_expression_has_visible_text(self, value: Any) -> bool:
        """Require actual reply text before an experimental image may be attached."""
        text = _strip_internal_message_blocks(str(value or ""), enabled=bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True)))
        text = re.sub(r"<[^>]{1,240}>", "", text, flags=re.DOTALL)
        return bool(re.search(r"\w", text, flags=re.UNICODE))

    def _extract_reaction_expression_hidden_intent(
        self,
        value: Any,
    ) -> tuple[str, dict[str, Any]]:
        """Remove the internal expression tag and parse at most one valid intent."""
        source = str(value or "")
        if not source:
            return "", {}

        literal_open = r"(?:<|\\<)\s*pc_reaction_expression\s*(?:>|\\>)"
        literal_close = r"(?:<|\\<)\s*/\s*pc_reaction_expression\s*(?:>|\\>)"
        escaped_open = r"&lt;\s*pc_reaction_expression\s*&gt;"
        escaped_close = r"&lt;\s*/\s*pc_reaction_expression\s*&gt;"
        complete_pattern = re.compile(
            rf"(?:{literal_open}(.*?){literal_close}|{escaped_open}(.*?){escaped_close})",
            flags=re.IGNORECASE | re.DOTALL,
        )
        parsed_intent: dict[str, Any] = {}

        def parse_payload(payload: str) -> dict[str, Any]:
            candidates = [str(payload or "").strip()]
            unescaped = html.unescape(candidates[0]).strip()
            if unescaped and unescaped not in candidates:
                candidates.append(unescaped)
            for candidate in list(candidates):
                if "\\\"" in candidate:
                    candidates.append(candidate.replace("\\\"", '"'))
            for candidate in candidates:
                if not candidate:
                    continue
                try:
                    payload_obj: Any = json.loads(candidate)
                    if isinstance(payload_obj, str):
                        payload_obj = json.loads(payload_obj)
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if not isinstance(payload_obj, dict):
                    continue
                raw_queries = payload_obj.get("candidate_queries")
                normalized = normalize_reaction_expression_intent(
                    query=payload_obj.get("query", ""),
                    context=payload_obj.get("context", ""),
                    purpose=payload_obj.get("purpose", ""),
                    emotion=payload_obj.get("emotion", ""),
                    intensity=payload_obj.get("intensity", 0),
                    candidate_queries=raw_queries,
                    candidate_limit=_safe_int(
                        runtime_persona_setting(self, 'reaction_expression_candidate_limit', 6),
                        6,
                        1,
                        16,
                    ),
                )
                if self._reaction_expression_bool_arg(payload_obj.get("sticker_only"), False):
                    normalized["sticker_only"] = True
                meaningful = any(
                    str(payload_obj.get(key) or "").strip()
                    for key in ("query", "purpose", "emotion")
                ) or bool(normalized.get("candidate_queries"))
                if not meaningful:
                    continue
                return normalized
            return {}

        def remove_complete(match: re.Match[str]) -> str:
            nonlocal parsed_intent
            if not parsed_intent:
                parsed_intent = parse_payload(match.group(1) or match.group(2) or "")
            return ""

        cleaned = complete_pattern.sub(remove_complete, source)
        # A malformed or truncated internal tag must never become visible chat text.
        cleaned = re.sub(literal_close, "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(escaped_close, "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(
            r"(?:<|\\<|&lt;)\s*/?\s*pc[_-]?reaction.*$",
            "",
            cleaned,
            flags=re.IGNORECASE | re.DOTALL,
        )
        cleaned = re.sub(r"[ \t]+(?=\r?$)", "", cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        return cleaned, parsed_intent

    @staticmethod
    def _reaction_expression_bool_arg(value: Any, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "yes", "on", "是", "发送"}:
            return True
        if normalized in {"0", "false", "no", "off", "否", "不发送"}:
            return False
        return default

    @staticmethod
    def _reaction_expression_scope(event: Any) -> str:
        checker = getattr(event, "is_private_chat", None)
        if callable(checker):
            try:
                if bool(checker()):
                    return "private"
            except Exception:
                pass
        origin = str(getattr(event, "unified_msg_origin", "") or "")
        if ":FriendMessage:" in origin:
            return "private"
        if ":GroupMessage:" in origin:
            return "group"
        return "group" if callable(checker) else "unknown"

    @classmethod
    def _reaction_expression_scope_key(cls, event: Any, user_id: str = "") -> str:
        origin = _single_line(getattr(event, "unified_msg_origin", ""), 240)
        if origin:
            return origin
        scope = cls._reaction_expression_scope(event)
        return f"{scope}:{_single_line(user_id, 160) or 'unknown'}"

    def _reaction_expression_state_owner(
        self,
        event: Any,
        user_id: Any,
        *,
        create: bool = True,
        scope: str = "",
        scope_key: str = "",
    ) -> dict[str, Any] | None:
        """Return the state owner without turning group senders into private users."""
        normalized_id = _single_line(user_id, 160)
        if not normalized_id:
            return None
        resolved_scope = _single_line(scope, 16).casefold()
        if not resolved_scope:
            resolved_scope = self._reaction_expression_scope(event) if event is not None else "private"
        if resolved_scope != "group":
            if event is not None:
                normalized_id = self._reaction_expression_event_storage_id(event, normalized_id)
            getter = getattr(self, "_get_user", None)
            if not callable(getter):
                return None
            try:
                owner = getter(normalized_id)
            except Exception:
                return None
            return owner if isinstance(owner, dict) else None

        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return None
        resolved_scope_key = _single_line(scope_key, 240)
        if not resolved_scope_key:
            resolved_scope_key = self._reaction_expression_scope_key(event, normalized_id)
        state_key = _single_line(f"{resolved_scope_key}|sender:{normalized_id}", 420)
        if not state_key:
            return None
        states = data.get("reaction_expression_group_states")
        if not isinstance(states, dict):
            if not create:
                return None
            states = {}
            data["reaction_expression_group_states"] = states
        owner = states.get(state_key)
        if not isinstance(owner, dict):
            if not create:
                return None
            owner = {}
            states[state_key] = owner
        return owner

    @staticmethod
    def _reaction_expression_authorization(event: Any) -> dict[str, Any]:
        raw = getattr(
            event,
            "_private_companion_reaction_expression_authorization",
            None,
        )
        if isinstance(raw, dict):
            return raw
        getter = getattr(event, "get_extra", None)
        if callable(getter):
            try:
                raw = getter("private_companion_reaction_expression_authorization")
            except Exception:
                raw = None
        if not isinstance(raw, dict):
            extras = getattr(event, "extras", None)
            raw = (
                extras.get("private_companion_reaction_expression_authorization")
                if isinstance(extras, dict)
                else None
            )
        return raw if isinstance(raw, dict) else {}
