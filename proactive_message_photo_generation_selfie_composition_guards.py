# -*- coding: utf-8 -*-
"""自拍构图与防镜像防背影约束域。

由 tools/split_mixin_domain.py 从 proactive_message_photo_generation.py 机械抽取（19 个方法 + 0 个模块级名字 + 0 个类级赋值 / 537 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessagePhotoGenerationMixin）。
"""
from __future__ import annotations

import os
import re
from .helpers import _photo_group_request_matches, _single_line
from .photo_prompt_context import _clip as _clip_photo_prompt_text
from .photo_reference_intent import ReferenceIntent
from .photo_wardrobe_decision import PhotoWardrobeDecision
from .proactive_message_photo_generation_shared import logger
from .scene_context import infer_companion_scene_category
from pathlib import Path
from typing import Any



class ProactiveMessagePhotoGenerationSelfieCompositionGuardsMixin:
    """自拍构图与防镜像防背影约束域（从 ProactiveMessagePhotoGenerationMixin 拆出）。"""


    def _photo_generation_selfie_schedule_scene_hint(
        self,
        user_id: str = "",
        *,
        include_dialogue_outfit: bool = True,
    ) -> str:
        snapshot_builder = getattr(self, "_build_companion_scene_snapshot", None)
        snapshot_formatter = getattr(self, "_format_companion_scene_snapshot", None)
        if callable(snapshot_builder) and callable(snapshot_formatter):
            try:
                scene_user: dict[str, Any] | None = None
                normalized_user_id = _single_line(user_id, 80)
                if normalized_user_id:
                    user_getter = getattr(self, "_get_user", None)
                    if callable(user_getter):
                        try:
                            candidate = user_getter(normalized_user_id)
                            if isinstance(candidate, dict):
                                scene_user = dict(candidate)
                                scene_user.setdefault("user_id", normalized_user_id)
                        except Exception:
                            scene_user = {"user_id": normalized_user_id, "relationship_role": "owner"}
                try:
                    snapshot = snapshot_builder(
                        scene_user,
                        include_dialogue_outfit=include_dialogue_outfit,
                    )
                except TypeError:
                    snapshot = snapshot_builder(scene_user)
                snapshot_text = _single_line(
                    snapshot_formatter(snapshot, purpose="selfie_scene"),
                    700,
                )
                if snapshot_text:
                    return snapshot_text
            except Exception as exc:
                logger.debug(
                    "自拍场景读取统一情境快照失败，已回退旧路径: %s",
                    _single_line(exc, 160),
                )
        plan = self.data.get("daily_plan", {}) if isinstance(getattr(self, "data", {}), dict) else {}
        plan = plan if isinstance(plan, dict) else {}
        state = self.data.get("daily_state", {}) if isinstance(getattr(self, "data", {}), dict) else {}
        state = state if isinstance(state, dict) else {}

        current_schedule = ""
        try:
            current_item = self._proactive_current_plan_item(plan)
            if isinstance(current_item, dict):
                current_schedule = _single_line(self._format_plan_item_for_prompt(current_item), 260)
        except Exception:
            current_schedule = ""
        if not current_schedule and callable(getattr(self, "_format_schedule_context_for_prompt", None)):
            try:
                current_schedule = _single_line(self._format_schedule_context_for_prompt(plan), 260)
            except Exception:
                current_schedule = ""

        location = ""
        if callable(getattr(self, "_current_location_state_text", None)):
            try:
                location = _single_line(self._current_location_state_text(state), 60)
            except Exception:
                location = ""
        coarse_location = ""
        if location and callable(getattr(self, "_coarse_roleplay_location_text", None)):
            try:
                coarse_location = _single_line(self._coarse_roleplay_location_text(location), 40)
            except Exception:
                coarse_location = ""
        location_text = coarse_location or location
        if location and coarse_location and location != coarse_location:
            location_text = f"{coarse_location}（{location}）"

        parts: list[str] = []
        if current_schedule:
            parts.append(f"当前日程：{current_schedule}")
        if location_text:
            parts.append(f"当前位置：{location_text}")
        _, scene_category_label = infer_companion_scene_category(current_schedule, location_text)
        if scene_category_label:
            parts.append(f"当前场景：{scene_category_label}")
        return _single_line("；".join(parts), 460)

    def _photo_reference_schedule_history_context(self) -> str:
        """Format today's started schedule items for reference selection only."""

        snapshot_builder = getattr(self, "_build_companion_scene_snapshot", None)
        if not callable(snapshot_builder):
            return ""
        try:
            snapshot = snapshot_builder()
        except Exception:
            return ""
        schedule = snapshot.get("schedule") if isinstance(snapshot, dict) else {}
        history = schedule.get("history") if isinstance(schedule, dict) else []
        if not isinstance(history, list):
            return ""
        labels = {
            "active": "进行中",
            "completed": "已完成",
            "changed": "已变更",
        }
        lines: list[str] = []
        for item in history[:24]:
            if not isinstance(item, dict):
                continue
            status = _single_line(item.get("status"), 20).lower()
            if status not in labels:
                continue
            start = _single_line(item.get("time"), 12)
            end = _single_line(item.get("end"), 12)
            activity = _single_line(item.get("activity"), 160)
            mood = _single_line(item.get("mood"), 32)
            if not activity:
                continue
            window = "-".join(part for part in (start, end) if part)
            lines.append(
                "｜".join(
                    part
                    for part in (
                        window,
                        labels[status],
                        activity,
                        f"情绪：{mood}" if mood else "",
                    )
                    if part
                )
            )
        return self._photo_prompt_clip("\n".join(lines), 2400)

    @staticmethod
    def _photo_reference_paths_equal(left: str, right: str) -> bool:
        left_text = str(left or "").strip()
        right_text = str(right or "").strip()
        if not left_text or not right_text:
            return False
        try:
            left_text = str(Path(left_text).expanduser().resolve())
            right_text = str(Path(right_text).expanduser().resolve())
        except (OSError, ValueError):
            pass
        return os.path.normcase(left_text) == os.path.normcase(right_text)

    @staticmethod
    def _photo_persona_fallback_allowed(
        workflow_kind: str,
        reference_intent: ReferenceIntent,
    ) -> bool:
        requested_roles = set(reference_intent.requested_roles or ())
        excluded_roles = set(reference_intent.excluded_roles or ())
        return (
            str(workflow_kind or "").strip().lower()
            in {"selfie", "portrait", "自拍", "人像"}
            and reference_intent.continuity_mode != "new_topic"
            and "identity" in requested_roles
            and "identity" not in excluded_roles
        )

    def _photo_generation_recent_continuity_constraint(
        self,
        workflow_kind: str,
        *,
        reference_image_path: str,
        continuity_key: str,
        wardrobe: PhotoWardrobeDecision | None = None,
    ) -> tuple[str, bool]:
        normalized_kind = str(workflow_kind or "").strip().lower()
        if normalized_kind not in {"selfie", "portrait", "自拍", "人像"}:
            return "", False
        recent = self._recent_sent_photo_continuity_candidate(continuity_key)
        if not recent or not self._photo_reference_paths_equal(
            reference_image_path,
            recent.get("path", ""),
        ):
            return "", False
        effective_roles = set(getattr(wardrobe, "effective_reference_roles", ()) or ())
        preserved = ["identity", "face", "hairstyle"]
        if "outfit" in effective_roles:
            preserved.append("exact outfit and accessories")
        if effective_roles & {"scene", "continuity"}:
            preserved.extend(("room or location", "lighting", "time of day"))
        continuity_instruction = (
            "Recent-photo continuity: this reference is the last image actually sent in the same conversation. "
            f"Unless the current request explicitly changes them, preserve {', '.join(preserved)}. "
            "Change only the requested action, pose, expression, gaze, camera angle, or framing. "
            "Any explicit new clothing, person, place, time, or scene request still has priority."
        )
        return continuity_instruction, True

    @staticmethod
    def _photo_prompt_clip(value: Any, limit: int, *, preserve_tail: bool = False) -> str:
        return _clip_photo_prompt_text(value, limit, preserve_tail=preserve_tail)

    @staticmethod
    def _photo_prompt_split_formatted(prompt_text: str) -> tuple[str, str]:
        prompt = str(prompt_text or "").strip()
        positive_match = re.search(
            r"positive\s+prompt\s*:\s*(.*?)(?=negative\s+prompt\s*:|$)",
            prompt,
            flags=re.I | re.S,
        )
        if not positive_match:
            avoid_match = re.search(
                r"(?:^|(?<=[.!?。！？]))\s*avoid\s+(.+?)\s*[.!?。！？]?\s*$",
                prompt,
                flags=re.I | re.S,
            )
            if avoid_match:
                positive = prompt[:avoid_match.start()].rstrip(" \t\r\n.!?。！？")
                negative = avoid_match.group(1).strip(" \t\r\n.!?。！？")
                return positive, negative
            return prompt, ""
        negative_match = re.search(r"negative\s+prompt\s*:\s*(.*)$", prompt, flags=re.I | re.S)
        return positive_match.group(1).strip(), (negative_match.group(1).strip() if negative_match else "")

    @staticmethod
    def _photo_generation_reference_wardrobe_section(
        reference: dict[str, Any] | None,
        wardrobe: PhotoWardrobeDecision,
    ) -> tuple[str, str]:
        reference = reference or {}
        effective_roles = tuple(wardrobe.effective_reference_roles)
        roles = ", ".join(effective_roles)
        parts: list[str] = []
        if reference:
            active_outfit_category = (
                _single_line(reference.get("outfit_category"), 40) or "unspecified"
                if "outfit" in effective_roles
                else "not active"
            )
            parts.append(
                "Reference responsibility: "
                f"effective roles={roles or 'none'}; "
                f"outfit category={active_outfit_category}."
            )
            if reference.get("kind") == "relation_role":
                role_name = _single_line(reference.get("role_name"), 80) or "the named relationship role"
                relationship = _single_line(reference.get("relationship"), 100)
                role_context = f" ({relationship})" if relationship else ""
                parts.append(
                    "Named relationship-role reference: "
                    f"the image identifies {role_name}{role_context}, not Bot. "
                    "Use it to depict that role only when the current request explicitly asks that role to appear or share the frame; "
                    "otherwise keep the role off-camera and use natural contextual cues. Do not transfer this identity, face, or body to Bot."
                )
        if wardrobe.positive_instruction:
            parts.append(f"Wardrobe decision: {wardrobe.positive_instruction}")
        return " ".join(parts), wardrobe.negative_instruction

    @classmethod
    def _photo_generation_compact_scene_hint(cls, scene_hint: str, *, limit: int = 420) -> str:
        text = _single_line(scene_hint, 1600)
        if not text or len(text) <= limit:
            return text
        parts = [part.strip() for part in re.split(r"[；;]+", text) if part.strip()]
        if len(parts) <= 1:
            return cls._photo_prompt_clip(text, min(limit, 260), preserve_tail=True)
        priorities = (
            (r"^(?:当前位置|地点|位置)[：:]", 90),
            (r"^(?:当前场景|场景)[：:]", 60),
            (r"^(?:时间|当前时间)[：:]", 60),
            (r"^(?:当前日程|日程)[：:]", 130),
            (r"^(?:今日穿搭|当天基础穿搭|当天穿搭|日常穿搭|today'?s outfit|daily outfit)[：:]", 120),
            (r"^(?:天气背景|天气|当前天气)[：:]", 90),
            (r"^(?:状态|状态余波|情绪)[：:]", 80),
            (r"^(?:视觉话题|背景)[：:]", 80),
        )
        ordered: list[tuple[str, int]] = []
        used: set[int] = set()
        for pattern, field_limit in priorities:
            for index, part in enumerate(parts):
                if index not in used and re.search(pattern, part, flags=re.I):
                    ordered.append((part, field_limit))
                    used.add(index)
        ordered.extend((part, 80) for index, part in enumerate(parts) if index not in used)
        kept: list[str] = []
        for part, field_limit in ordered:
            compact = cls._photo_prompt_clip(part, field_limit, preserve_tail=True)
            candidate = "；".join((*kept, compact))
            if len(candidate) <= limit:
                kept.append(compact)
                continue
            remaining = limit - len("；".join(kept)) - (1 if kept else 0)
            if remaining >= 36:
                kept.append(cls._photo_prompt_clip(compact, remaining, preserve_tail=True))
            break
        return "；".join(kept)

    @staticmethod
    def _photo_generation_selfie_scene_constraint(
        workflow_kind: str,
        scene_hint: str,
        *,
        has_reference: bool,
    ) -> str:
        normalized = str(workflow_kind or "").strip().lower()
        if normalized not in {"selfie", "portrait", "自拍", "人像"} or not scene_hint:
            return ""
        reference_boundary = (
            "The reference controls only the roles declared by the wardrobe ruling. "
            if has_reference
            else "Do not assume an unavailable reference was supplied. "
        )
        return (
            "Resolved selfie scene facts: "
            f"{scene_hint}. An explicit scene or location in the current request overrides conflicting facts; otherwise use these facts for time, location, activity, mood, weather, and light. "
            f"{reference_boundary}"
            "Do not restore a conflicting schedule location or wardrobe, and avoid unrelated rooms."
        )

    def _photo_generation_composition_sections(
        self,
        workflow_kind: str,
        prompt_text: str,
        *,
        allow_group_photo: bool = False,
    ) -> tuple[str, str]:
        normalized = str(workflow_kind or "").strip().lower()
        if normalized not in {"selfie", "portrait", "自拍", "人像"}:
            return "", ""
        explicit_mirror = self._photo_generation_explicit_mirror_request(prompt_text)
        explicit_back_view = self._photo_generation_explicit_back_view_request(prompt_text)
        if allow_group_photo and _photo_group_request_matches(prompt_text):
            positive = (
                "Referenced multi-person composition: preserve every person represented by the submitted visual references, "
                "their count, identity, and relative placement in one continuous scene; do not invent anyone else."
            )
            negative = "unreferenced extra people, invented faces, duplicated people, comparison panels, split screen, collage"
        elif explicit_back_view:
            positive = (
                "Back-view character composition: exactly one recognizable character wearing one coherent outfit in one continuous scene; "
                "the requested back view or facing-away pose is intentional, preserve the reference hairstyle silhouette and stable appearance, "
                "and compose a natural environmental portrait without requiring the face to be visible."
            )
            negative = "duplicated subject, twins, multiple people, outfit alternatives, comparison panels, split screen, side-by-side panels, collage, character sheet"
        elif explicit_mirror:
            positive = (
                "Selfie composition: exactly one character wearing one coherent outfit in one continuous scene; "
                "one mirror reflection of that same outfit is allowed; keep the complete face visible and do not let the phone cover it."
            )
            negative = "duplicated subject, outfit alternatives, comparison panels, split screen, side-by-side panels, collage, character sheet, phone covering face"
        else:
            positive = (
                "Selfie composition: exactly one character wearing one coherent outfit in one continuous scene; keep the face visible, "
                "prefer a handheld selfie or natural environmental portrait with upper-body to three-quarter framing, and place the character naturally in the resolved scene."
            )
            negative = (
                "duplicate character, twins, multiple people, multiple outfits, outfit comparison, before and after, split screen, "
                "side-by-side panels, diptych, collage, character sheet, mirror selfie, full-length mirror selfie, dressing-room mirror, phone covering face"
            )
        return positive, negative

    @staticmethod
    def _photo_generation_subject_count_contract(
        workflow_kind: str,
        request_text: str,
        *,
        explicit_reference_supplied: bool,
    ) -> tuple[str, str]:
        normalized = str(workflow_kind or "").strip().lower()
        if normalized in {"edit", "改图", "修图", "重绘", "p图"}:
            return "", ""
        group_photo_requested = _photo_group_request_matches(request_text)
        if explicit_reference_supplied and group_photo_requested:
            return (
                "Multi-person composition is permitted only because the current request supplied an explicit source reference; "
                "preserve the referenced people's identities and do not invent additional people.",
                "unreferenced extra people, invented faces, duplicated people",
            )
        if normalized not in {"selfie", "portrait", "自拍", "人像"} and not group_photo_requested:
            return "", ""
        return (
            "Subject-count boundary: show at most one recognizable human character in one continuous scene. "
            "Other people may be implied only by non-human traces such as a second cup, gift, note, or off-camera context; "
            "do not show another face, body, silhouette, reflection, or portrait.",
            "group photo, group portrait, couple photo, two people, multiple people, extra person, second person, "
            "companion in frame, crowd, invented face",
        )

    @staticmethod
    def _photo_generation_explicit_back_view_request(text: str) -> bool:
        raw = _single_line(text, 1200)
        if not raw:
            return False
        positive = re.split(r"negative prompt\s*:", raw, maxsplit=1, flags=re.I)[0]
        positive = re.sub(
            r"(?:不要|避免|别|不许|禁止).{0,18}(?:背影|背对镜头|背对相机)|"
            r"\b(?:no|not|avoid|without)\s+(?:a\s+)?(?:back[-\s]?view|facing\s+away)[^,.;；。]*",
            " ",
            positive,
            flags=re.I,
        )
        return bool(
            any(marker in positive for marker in ("背影", "背对镜头", "背对相机", "从背后", "身后视角"))
            or re.search(r"\b(?:back[-\s]?view|from\s+behind|facing\s+away)\b", positive, flags=re.I)
        )

    @staticmethod
    def _photo_generation_edit_contract(workflow_kind: str) -> tuple[str, str]:
        normalized = str(workflow_kind or "").strip().lower()
        if normalized not in {"edit", "改图", "修图", "重绘", "p图"}:
            return "", ""
        return (
            "Image edit contract: use the user-provided image as the sole source canvas and visual identity reference. "
            "Treat the request strictly as a constrained edit of that supplied canvas. Preserve every subject, face, body, outfit, pose, composition, camera angle, and background detail unless the user explicitly asks to change it. "
            "Apply only the requested edit and keep unrelated pixels and details as close to the source as possible.",
            "a selfie or a new character portrait, replacing the source person with the assistant persona, restoring today's outfit, unrelated redesigns",
        )

    @staticmethod
    def _photo_generation_explicit_mirror_request(text: str) -> bool:
        raw = _single_line(text, 1200)
        if not raw:
            return False
        lowered = raw.lower()
        detection_text = re.split(r"negative prompt\s*:", lowered, maxsplit=1, flags=re.I)[0]
        positive_scan = re.sub(
            r"(?:不要|避免|别|不许|禁止).{0,18}(?:镜前|对镜|镜中|镜子|全身镜|穿衣镜|试衣镜)",
            " ",
            detection_text,
            flags=re.I,
        )
        positive_scan = re.sub(
            r"(?:no|not|avoid|without)\s+(?:a\s+)?(?:mirror|mirror\s+selfie|full[-\s]?length\s+mirror|"
            r"full[-\s]?body\s+mirror|mirror\s+shot|mirror\s+photo|mirror\s+portrait)[^,.;；。]*",
            " ",
            positive_scan,
            flags=re.I,
        )
        positive_scan = re.sub(r"\bnon[-\s]?mirror\b", " ", positive_scan, flags=re.I)
        positive_scan = re.sub(r"unless[^,.;；。]*mirror[^,.;；。]*", " ", positive_scan, flags=re.I)
        if re.search(
            r"镜前|对镜|镜中|镜子|全身镜|穿衣镜|试衣镜|\bmirror\b|looking\s+in\s+the\s+mirror|in\s+front\s+of\s+(?:a\s+)?mirror",
            positive_scan,
            flags=re.I,
        ):
            return True
        return False

    @staticmethod
    def _append_photo_negative_terms(prompt_text: str, terms: list[str], *, limit: int = 1800) -> str:
        prompt = str(prompt_text or "").strip()
        if not prompt:
            return ""
        existing = prompt.lower()
        missing = [term for term in terms if term and term.lower() not in existing]
        if not missing:
            return _single_line(prompt, limit)
        suffix = ", ".join(missing)
        if re.search(r"negative prompt\s*:", prompt, flags=re.I):
            prompt = prompt.rstrip().rstrip(".")
            return _single_line(f"{prompt}, {suffix}.", limit)
        return _single_line(f"{prompt}. Negative prompt: {suffix}.", limit)

    def _sanitize_unrequested_mirror_selfie_prompt(
        self,
        prompt_text: str,
        *,
        context_text: str = "",
        limit: int = 1800,
    ) -> str:
        prompt = str(prompt_text or "").strip()
        if not prompt:
            return ""
        if self._photo_generation_explicit_mirror_request(context_text):
            return _single_line(prompt, limit)
        replacements = (
            (r"\bfull[-\s]?length\s+mirror\s+(?:selfie|shot|photo|portrait)\b", "natural upper-body to three-quarter portrait"),
            (r"\bfull[-\s]?body\s+mirror\s+(?:selfie|shot|photo|portrait)\b", "natural upper-body to three-quarter portrait"),
            (r"\bmirror\s+(?:selfie|shot|photo|portrait)\b", "handheld selfie or natural environmental portrait"),
            (r"\bstanding\s+in\s+front\s+of\s+(?:a\s+)?mirror\b", "standing naturally in the current location"),
            (r"\bdressing[-\s]?room\s+mirror\b", "current-location background"),
            (r"\bphone\s+covering\s+(?:the\s+)?face\b", "visible face"),
            (r"全身镜自拍|全身对镜|对镜自拍|镜前自拍|镜中自拍|穿衣镜|试衣镜", "自然半身或四分之三身随手拍"),
        )
        cleaned = prompt
        for pattern, replacement in replacements:
            cleaned = re.sub(pattern, replacement, cleaned, flags=re.I)
        cleaned = re.sub(r"\s*,\s*,+", ", ", cleaned)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,;；")
        negative_terms = [
            "mirror selfie",
            "full-length mirror selfie",
            "full body mirror shot",
            "dressing room mirror",
            "phone covering face",
        ]
        return self._append_photo_negative_terms(cleaned or prompt, negative_terms, limit=limit)

    def _apply_photo_generation_selfie_composition_guard(self, prompt_text: str, workflow_kind: str) -> str:
        prompt = str(prompt_text or "").strip()
        normalized = str(workflow_kind or "").strip().lower()
        if normalized not in {"selfie", "portrait", "自拍", "人像"}:
            return _single_line(prompt, 1800)
        explicit_mirror = self._photo_generation_explicit_mirror_request(prompt)
        explicit_back_view = self._photo_generation_explicit_back_view_request(prompt)
        if explicit_back_view:
            guard = (
                "Back-view character composition guard: exactly one recognizable character wearing exactly one coherent outfit in one continuous scene; "
                "the explicitly requested back-view pose is allowed, preserve the reference hairstyle silhouette and stable appearance, "
                "and do not require the face to be visible."
            )
        elif explicit_mirror:
            guard = (
                "Selfie composition guard: exactly one character wearing exactly one coherent outfit in one continuous scene; "
                "a single mirror reflection of that same outfit is allowed, but do not create outfit alternatives, comparison panels, duplicated subjects, or a collage; "
                "keep the face visible and avoid the phone covering the face."
            )
        else:
            guard = (
                "Default selfie composition guard: exactly one character wearing exactly one coherent outfit in one continuous scene; "
                "no duplicated subject, outfit alternatives, comparison layout, split screen, side-by-side panels, diptych, collage, or character sheet; "
                "no mirror selfie or full-length mirror shot unless explicitly requested; keep the face visible, avoid phone covering face, "
                "use upper-body to three-quarter framing, and place the character naturally in the current scene."
            )
        merged = f"{prompt}\n\n{guard}".strip()
        negative_terms = [
            "duplicate character",
            "twins",
            "multiple people",
            "multiple outfits",
            "outfit comparison",
            "before and after",
            "split screen",
            "side-by-side panels",
            "diptych",
            "collage",
            "character sheet",
        ]
        if not explicit_mirror and not explicit_back_view:
            negative_terms.extend(
                ["mirror selfie", "full-length mirror selfie", "full body mirror shot", "dressing room mirror"]
            )
        if not explicit_back_view:
            negative_terms.append("phone covering face")
        return self._append_photo_negative_terms(
            merged,
            negative_terms,
            limit=1800,
        )

    def _apply_photo_generation_edit_guard(self, prompt_text: str, workflow_kind: str) -> str:
        prompt = str(prompt_text or "").strip()
        normalized = str(workflow_kind or "").strip().lower()
        if normalized not in {"edit", "改图", "修图", "重绘", "p图"}:
            return _single_line(prompt, 1800)
        guard = (
            "Image edit contract: use the user-provided image as the sole source canvas and visual identity reference. "
            "This is not a selfie or a new character portrait. Preserve every subject, face, body, outfit, pose, "
            "composition, camera angle, and background detail unless the user explicitly asks to change it. "
            "Never replace a person with the assistant persona, a configured persona reference, or today's outfit. "
            "Apply only the requested edit and keep all unrelated pixels and details as close to the source as possible."
        )
        return _single_line(f"{guard}\n\nEdit request and existing prompt: {prompt}".strip(), 1800)
