# -*- coding: utf-8 -*-
"""CreativeCoverMixin。

由 tools/split_mixin_domain.py 从 creative.py 机械抽取（10 个方法 + 0 个模块级名字 + 0 个类级赋值 / 472 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 CreativeMixin）。
"""
from __future__ import annotations
from .creative_shared import Any
from .creative_shared import Path
from .creative_shared import _now_ts
from .creative_shared import _path_text
from .creative_shared import _safe_float
from .creative_shared import _safe_int
from .creative_shared import _single_line
from .creative_shared import asyncio
from .creative_shared import deepcopy
from .creative_shared import json
from .creative_shared import logger
from .creative_shared import re
from .creative_shared import runtime_persona_setting
from .creative_shared import shutil
from .creative_shared import story_legacy_operation



class CreativeCoverMixin:
    """CreativeCoverMixin（从 CreativeMixin 拆出）。"""


    @staticmethod
    def _creative_cover_file_exists(project: dict[str, Any]) -> bool:
        path_text = _path_text(project.get("cover_path"), 1000)
        if not path_text:
            return False
        try:
            path = Path(path_text)
            return path.exists() and path.is_file()
        except (OSError, ValueError):
            return False

    def _creative_cover_person_reference_configured(self) -> bool:
        if not bool(runtime_persona_setting(self, "enable_photo_reference_image", False)):
            return False
        getter = getattr(self, "_photo_persona_reference_image_path", None)
        if callable(getter):
            try:
                if _single_line(getter(), 500):
                    return True
            except Exception:
                pass
        return bool(
            _single_line(
                runtime_persona_setting(self, "photo_persona_reference_image_path", ""),
                1000,
            )
        )

    def _creative_cover_needs_identity_upgrade(self, project: dict[str, Any]) -> bool:
        if not self._creative_cover_file_exists(project):
            return False
        if _safe_float(project.get("cover_generated_at"), 0) <= 0:
            return False
        if _single_line(project.get("cover_generation_person_policy"), 40) == "single_reference_character":
            return False
        return bool(
            self._creative_cover_has_person_subject(project)
            and self._creative_cover_person_reference_configured()
        )

    def _creative_cover_candidate_id(self) -> str:
        if not bool(runtime_persona_setting(self, "enable_creative_cover_generation", False)):
            return ""
        now = _now_ts()
        for project in reversed(self._creative_projects()):
            if not isinstance(project, dict):
                continue
            if self._creative_cover_file_exists(project) and not self._creative_cover_needs_identity_upgrade(project):
                continue
            chunks = project.get("draft_chunks") if isinstance(project.get("draft_chunks"), list) else []
            if not any(isinstance(chunk, dict) and _single_line(chunk.get("text"), 80) for chunk in chunks):
                continue
            attempts = _safe_int(project.get("cover_generation_attempts"), 0, 0)
            if attempts >= 3:
                continue
            if now < _safe_float(project.get("cover_generation_next_retry_at"), 0):
                continue
            if (
                _single_line(project.get("cover_generation_status"), 24) == "generating"
                and now - _safe_float(project.get("cover_generation_attempted_at"), 0) < 20 * 60
            ):
                continue
            project_id = _single_line(project.get("id"), 32)
            if project_id:
                return project_id
        return ""

    @staticmethod
    def _creative_cover_style_instruction(project: dict[str, Any]) -> tuple[str, str]:
        source = re.sub(
            r"\s+",
            "",
            " ".join(
                str(project.get(key) or "")
                for key in ("work_type", "tone", "premise", "source_text")
            ).lower(),
        )
        style_rules = (
            (
                ("古风", "武侠", "仙侠", "历史", "志怪", "水墨"),
                "东方水墨",
                "contemporary Chinese ink illustration with restrained mineral pigments, expressive brush texture, elegant negative space",
            ),
            (
                ("赛博", "cyberpunk", "霓虹", "朋克"),
                "赛博视觉",
                "refined cyberpunk editorial illustration, luminous neon accents, deep urban atmosphere, crisp graphic shapes",
            ),
            (
                ("悬疑", "推理", "惊悚", "犯罪", "侦探", "谜"),
                "悬疑黑色电影",
                "restrained neo-noir editorial illustration, high-contrast lighting, controlled shadows, limited color palette, subtle visual clue",
            ),
            (
                ("恐怖", "怪谈", "诡异", "克苏鲁", "gothic"),
                "哥特暗黑",
                "atmospheric gothic illustration, unsettling quiet, tactile shadows, muted colors, elegant rather than graphic horror",
            ),
            (
                ("科幻", "未来", "宇宙", "星际", "机器人", "人工智能", "奇幻", "魔法", "幻想"),
                "幻想概念插画",
                "cinematic speculative-fiction concept illustration, immersive worldbuilding, dramatic scale, sophisticated atmospheric color",
            ),
            (
                ("诗", "诗歌", "散文", "随笔", "札记", "日记", "意识流"),
                "诗意水彩",
                "poetic editorial watercolor and printmaking, tactile paper texture, symbolic imagery, soft layered color, generous negative space",
            ),
            (
                ("童话", "儿童", "寓言", "轻小说", "校园", "青春"),
                "清新叙事插画",
                "polished narrative illustration with clean shapes, lively color rhythm, gentle detail, contemporary Japanese editorial sensibility",
            ),
            (
                ("喜剧", "搞笑", "幽默", "轻松", "荒诞"),
                "轻快平面插画",
                "playful contemporary graphic illustration, bold readable shapes, witty visual metaphor, bright but balanced color palette",
            ),
            (
                ("治愈", "日常", "生活", "温柔", "恋爱", "爱情", "亲情", "成长"),
                "暖色文学插画",
                "warm literary editorial illustration, natural light, intimate everyday detail, painterly gouache texture, emotionally restrained composition",
            ),
        )
        for keywords, label, instruction in style_rules:
            if any(keyword in source for keyword in keywords):
                return label, instruction
        return (
            "文学编辑插画",
            "sophisticated literary editorial illustration, painterly texture, symbolic central image, restrained color harmony, timeless book-cover composition",
        )

    def _creative_cover_has_person_subject(self, project: dict[str, Any]) -> bool:
        if self._get_project_characters(project):
            return True
        source = " ".join(
            str(project.get(key) or "")
            for key in ("premise", "source_text", "tone", "work_type")
        )
        chunks = project.get("draft_chunks") if isinstance(project.get("draft_chunks"), list) else []
        source += " " + " ".join(
            str(chunk.get("text") or "")
            for chunk in chunks[-3:]
            if isinstance(chunk, dict)
        )
        return bool(
            re.search(
                r"(少女|女孩|女生|女性|女人|男孩|男生|男性|男人|少年|青年|老人|孩子|人物|角色|主角|主人公|"
                r"姑娘|先生|女士|母亲|父亲|妈妈|爸爸|姐姐|妹妹|哥哥|弟弟|她|他|人影|背影|侧脸|面孔|脸庞)",
                source,
            )
        )

    def _creative_cover_prompt_format_mode(self, value: Any = "") -> str:
        raw_value = value if str(value or "").strip() else runtime_persona_setting(
            self, "photo_generation_prompt_format", "traditional"
        )
        format_normalizer = getattr(
            self,
            "_normalize_photo_generation_prompt_format",
            None,
        )
        if callable(format_normalizer):
            normalized = str(format_normalizer(raw_value) or "traditional")
            return (
                normalized
                if normalized in {"traditional", "natural_language", "nai"}
                else "traditional"
            )
        raw_format = str(raw_value or "traditional").strip().lower()
        return (
            "natural_language"
            if raw_format in {"natural", "natural_language", "自然语言", "自然语言描述"}
            else "traditional"
        )

    def _creative_cover_prompt(
        self,
        project: dict[str, Any],
        *,
        person_reference_available: bool = False,
        prompt_format: str = "",
    ) -> str:
        chunks = project.get("draft_chunks") if isinstance(project.get("draft_chunks"), list) else []
        recent_text = " ".join(
            _single_line(chunk.get("text"), 220)
            for chunk in chunks[-3:]
            if isinstance(chunk, dict) and _single_line(chunk.get("text"), 220)
        )
        characters = self._get_project_characters(project)
        primary_character = characters[0] if characters and isinstance(characters[0], dict) else {}
        character_hint = " - ".join(
            part
            for part in (
                _single_line(primary_character.get("name"), 30),
                _single_line(
                    primary_character.get("description") or primary_character.get("personality") or primary_character.get("role"),
                    100,
                ),
            )
            if part
        )
        visual_source = _single_line(
            " ".join(
                part
                for part in (
                    _single_line(project.get("premise"), 280),
                    _single_line(project.get("tone"), 80),
                    _single_line(project.get("source_text"), 180),
                    recent_text,
                )
                if part
            ),
            900,
        )
        _style_label, style_instruction = self._creative_cover_style_instruction(project)
        has_person_subject = self._creative_cover_has_person_subject(project)
        if has_person_subject and person_reference_available:
            subject_instruction = (
                "The supplied reference image defines the only visible character's identity and appearance. "
                "Show exactly one person in the entire artwork and preserve that reference identity. "
                "Do not show any second person, companion, man, woman, face, portrait, silhouette, shadow-person, "
                "human reflection, figure inside a mirror/window/portal/screen, framed photo, statue, or background crowd. "
                "Represent every other story character only through objects, architecture, light, weather, or abstract symbols. "
            )
        elif has_person_subject:
            subject_instruction = (
                "No character reference image is available, so do not depict any person or human-like character. "
                "Use a symbolic object-and-environment composition for the protagonist instead: no people, faces, bodies, "
                "silhouettes, portraits, human reflections, figures in mirrors/windows/portals/screens, framed photos, statues, or crowds. "
            )
        else:
            subject_instruction = (
                "Use a people-free symbolic composition: no people, faces, bodies, silhouettes, portraits, human reflections, "
                "figures in mirrors/windows/portals/screens, framed photos, statues, or crowds. "
            )
        prompt_format = self._creative_cover_prompt_format_mode(prompt_format)
        if prompt_format != "natural_language":
            positive_parts = [
                "polished book cover illustration",
                "vertical 2:3 composition",
                f"genre: {_single_line(project.get('work_type'), 50) or 'fiction'}",
                f"story motifs: {visual_source or 'an intimate original story with a clear central visual symbol'}",
                style_instruction,
                "one focused scene",
                "strong readable silhouette",
                "layered depth",
                "deliberate lighting",
                "quiet title-safe negative space",
            ]
            if has_person_subject and person_reference_available:
                positive_parts.extend(
                    [
                        "exactly one visible person",
                        "same identity and appearance as the supplied reference image",
                        f"primary character: {character_hint}" if character_hint else "single reference character",
                    ]
                )
            else:
                positive_parts.extend(["people-free composition", "symbolic objects and environment"])
            negative_parts = [
                "readable text",
                "letters",
                "typography",
                "logo",
                "watermark",
                "mockup",
                "border",
                "second person",
                "companion character",
                "multiple people",
                "crowd",
                "extra face",
                "human silhouette",
                "human reflection",
                "person in mirror",
                "person in window",
                "person in portal",
                "person on screen",
                "person in framed photo",
                "human statue",
                "shadow person",
            ]
            if not (has_person_subject and person_reference_available):
                negative_parts.extend(["person", "human face", "human body", "portrait"])
            return _single_line(
                "Positive prompt: "
                + ", ".join(_single_line(part, 900) for part in positive_parts if _single_line(part, 900))
                + ". Negative prompt: "
                + ", ".join(negative_parts)
                + ".",
                1800,
            )
        return _single_line(
            "Create a polished book cover illustration with a vertical 2:3 composition. "
            f"Genre: {_single_line(project.get('work_type'), 50) or 'fiction'}. "
            f"Story and visual motifs: {visual_source or 'an intimate original story with a clear central visual symbol'}. "
            + (f"Primary character only: {character_hint}. " if character_hint and person_reference_available else "")
            + subject_instruction
            + f"Art direction: {style_instruction}. "
            + "Use one focused scene, strong silhouette, layered depth, deliberate lighting, and enough quiet space for a title overlay. "
            "Artwork only: no readable text, no letters, no typography, no logo, no watermark, no mockup, no border.",
            1800,
        )

    @story_legacy_operation("creative.cover.store")
    async def _store_creative_cover_image(self, project_id: str, image_path: str) -> str:
        source_text = _path_text(image_path, 1000)
        if not source_text:
            return ""
        try:
            source = Path(source_text).resolve()
            if not source.exists() or not source.is_file():
                return ""
            root = Path(str(getattr(self, "data_dir", "") or ".")).resolve() / "creative_covers"
            await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
            suffix = source.suffix.lower() if source.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} else ".png"
            target = root / f"{_single_line(project_id, 32)}{suffix}"
            if source != target.resolve():
                await asyncio.to_thread(shutil.copy2, source, target)
            return str(target)
        except Exception as exc:
            logger.warning("创作封面保存失败: project=%s error=%s", project_id, _single_line(exc, 160))
            return ""

    @story_legacy_operation("creative.cover.generate")
    async def _maybe_generate_creative_cover(self, project_id: str, *, force: bool = False) -> dict[str, Any] | None:
        if not force and not bool(
            runtime_persona_setting(self, "enable_creative_cover_generation", False)
        ):
            return None
        project_id = _single_line(project_id, 32)
        if not project_id:
            return None
        prompt_format = self._creative_cover_prompt_format_mode()
        locks = getattr(self, "_creative_cover_generation_locks", None)
        if not isinstance(locks, dict):
            locks = {}
            self._creative_cover_generation_locks = locks
        lock = locks.get(project_id)
        if not isinstance(lock, asyncio.Lock):
            lock = asyncio.Lock()
            locks[project_id] = lock
        async with lock:
            async with self._data_lock:
                project = next(
                    (
                        item
                        for item in self._creative_projects()
                        if isinstance(item, dict) and _single_line(item.get("id"), 32) == project_id
                    ),
                    None,
                )
                if not isinstance(project, dict):
                    return None
                identity_upgrade = self._creative_cover_needs_identity_upgrade(project)
                if not force and self._creative_cover_file_exists(project) and not identity_upgrade:
                    return dict(project)
                now = _now_ts()
                attempts = _safe_int(project.get("cover_generation_attempts"), 0, 0)
                if not force and (
                    attempts >= 3
                    or now < _safe_float(project.get("cover_generation_next_retry_at"), 0)
                ):
                    return dict(project)
                project_snapshot = deepcopy(project)

            generator = getattr(self, "_generate_photo_image", None)
            available = getattr(self, "_photo_text_available", None)
            if (
                not bool(runtime_persona_setting(self, "enable_photo_text_action", False))
                or not callable(generator)
                or (callable(available) and not available())
            ):
                async with self._data_lock:
                    project = next(
                        (item for item in self._creative_projects() if isinstance(item, dict) and _single_line(item.get("id"), 32) == project_id),
                        None,
                    )
                    if isinstance(project, dict):
                        project["cover_generation_status"] = "unavailable"
                        project["cover_generation_error"] = "当前没有可用的生图后端"
                        project["cover_generation_next_retry_at"] = _now_ts() + 3600
                        self._save_data_sync(sections={"creative_projects"})
                        return dict(project)
                return None

            has_person_subject = self._creative_cover_has_person_subject(project_snapshot)
            reference_image_path = ""
            if has_person_subject:
                reference_getter = getattr(self, "_photo_persona_reference_image_for_kind_async", None)
                if callable(reference_getter):
                    try:
                        reference_image_path = _path_text(
                            await reference_getter(
                                "portrait",
                                allow_daily_outfit=False,
                                request_text=json.dumps(project_snapshot, ensure_ascii=False),
                            ),
                            1000,
                        )
                    except Exception as exc:
                        logger.warning(
                            "创作封面读取人物参考图失败: project=%s error=%s",
                            project_id,
                            _single_line(exc, 160),
                        )
            if identity_upgrade and not reference_image_path:
                async with self._data_lock:
                    project = next(
                        (item for item in self._creative_projects() if isinstance(item, dict) and _single_line(item.get("id"), 32) == project_id),
                        None,
                    )
                    if isinstance(project, dict):
                        project["cover_generation_status"] = "ready"
                        project["cover_generation_error"] = "旧封面等待人物参考图可用后再升级"
                        project["cover_generation_next_retry_at"] = _now_ts() + 3 * 3600
                        self._save_data_sync(sections={"creative_projects"})
                        return dict(project)
                return None
            prompt_text = self._creative_cover_prompt(
                project_snapshot,
                person_reference_available=bool(reference_image_path),
                prompt_format=prompt_format,
            )
            style_label, _style_instruction = self._creative_cover_style_instruction(project_snapshot)
            attempt_number = attempts + 1
            async with self._data_lock:
                project = next(
                    (item for item in self._creative_projects() if isinstance(item, dict) and _single_line(item.get("id"), 32) == project_id),
                    None,
                )
                if not isinstance(project, dict):
                    return None
                project["cover_generation_status"] = "generating"
                project["cover_generation_attempts"] = attempt_number
                project["cover_generation_attempted_at"] = _now_ts()
                project["cover_generation_error"] = ""
                self._save_data_sync(sections={"creative_projects"})

            backend, generated_path, note = await generator(
                workflow_kind="portrait" if reference_image_path else "text2img",
                prompt_text=prompt_text,
                session_key=f"creative_cover_{project_id}",
                reference_image_path=reference_image_path,
                image_size="",
                allow_daily_outfit_reference=False,
                prompt_format=prompt_format,
            )
            stored_path = await self._store_creative_cover_image(project_id, generated_path) if generated_path else ""
            now = _now_ts()
            async with self._data_lock:
                project = next(
                    (item for item in self._creative_projects() if isinstance(item, dict) and _single_line(item.get("id"), 32) == project_id),
                    None,
                )
                if not isinstance(project, dict):
                    return None
                project["cover_generation_backend"] = _single_line(backend, 80)
                project["cover_generation_prompt"] = _single_line(prompt_text, 1800)
                project["cover_generation_style"] = _single_line(style_label, 40)
                project["cover_generation_reference_image"] = _path_text(reference_image_path, 1000)
                project["cover_generation_person_policy"] = (
                    "single_reference_character"
                    if reference_image_path
                    else ("symbolic_no_people" if has_person_subject else "no_people")
                )
                if stored_path:
                    project["cover_path"] = stored_path
                    project["cover_generated_at"] = now
                    project["cover_generation_status"] = "ready"
                    project["cover_generation_error"] = ""
                    project["cover_generation_next_retry_at"] = 0
                    logger.info("创作封面已生成: project=%s backend=%s path=%s", project_id, _single_line(backend, 80), _single_line(stored_path, 180))
                else:
                    project["cover_generation_status"] = "failed"
                    project["cover_generation_error"] = _single_line(note, 220) or "生图失败"
                    project["cover_generation_next_retry_at"] = now + min(24, max(3, attempt_number * 3)) * 3600
                    logger.info("创作封面未生成: project=%s attempt=%s error=%s", project_id, attempt_number, _single_line(note, 180))
                self._save_data_sync(sections={"creative_projects"})
                return dict(project)
