# -*- coding: utf-8 -*-
"""每日穿搭出图主流程域。

由 tools/split_mixin_domain.py 从 proactive_message_photo_generation.py 机械抽取（15 个方法 + 0 个模块级名字 + 0 个类级赋值 / 548 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessagePhotoGenerationMixin）。
"""
from __future__ import annotations

import asyncio
import hashlib
from .conversation_prompt_section import PhotoPromptContent
from .helpers import _path_text, _safe_int, _single_line, _today_key
from .persona_config import runtime_persona_setting
from .proactive_message_photo_generation_shared import _now_ts, logger
from .wardrobe_photo import resolve_daily_outfit_profile as resolve_wardrobe_daily_outfit_profile
from datetime import datetime, timedelta
from typing import Any



class ProactiveMessagePhotoGenerationDailyOutfitGenerationMixin:
    """每日穿搭出图主流程域（从 ProactiveMessagePhotoGenerationMixin 拆出）。"""


    async def _ensure_daily_outfit_photo(
        self,
        diary: dict[str, Any] | None = None,
        *,
        force: bool = False,
    ) -> dict[str, Any] | None:
        if not force and not runtime_persona_setting(self, "enable_daily_outfit_photo", False):
            return None
        today = _today_key()
        async with self._data_lock:
            existing = self.data.get("daily_outfit_photo") if isinstance(self.data.get("daily_outfit_photo"), dict) else {}
            if not force and existing.get("date") == today:
                return dict(existing)
        lock = getattr(self, "_daily_outfit_photo_generation_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._daily_outfit_photo_generation_lock = lock
        async with lock:
            async with self._data_lock:
                existing = self.data.get("daily_outfit_photo") if isinstance(self.data.get("daily_outfit_photo"), dict) else {}
                if not force and existing.get("date") == today:
                    return dict(existing)
            return await self._ensure_daily_outfit_photo_unlocked(diary, force=force, today=today)

    async def _ensure_daily_outfit_photo_unlocked(
        self,
        diary: dict[str, Any] | None = None,
        *,
        force: bool = False,
        today: str = "",
    ) -> dict[str, Any] | None:
        today = today or _today_key()
        if not runtime_persona_setting(self, "enable_photo_text_action", True):
            return await self._record_daily_outfit_photo_result(today, "", "主动拍照/生图未开启")
        scope_checker = getattr(self, "_photo_generation_scope_allowed", None)
        if callable(scope_checker) and not scope_checker(proactive=True):
            return await self._record_daily_outfit_photo_result(today, "", "主动生图不在当前配置的使用范围内")
        if not self._photo_text_available():
            return await self._record_daily_outfit_photo_result(today, "", "当前没有可用的生图后端")
        memory_context = ""
        composer = getattr(self, "_memory_companion_compose_feature_context", None)
        if callable(composer):
            try:
                memory_context = await composer(
                    kind="daily_outfit_photo",
                    query=(
                        "今日穿搭生成：历史穿搭、今天日程、天气、地点、用户常问衣服颜色、"
                        "最近自拍、服装连续性、需要避免的造型重复"
                    ),
                    top_k=5,
                    max_chars=900,
                )
            except Exception as exc:
                logger.debug("每日穿搭 我会牢牢记住你 上下文读取失败: %s", _single_line(exc, 120))
        schedule_hint = self._daily_outfit_schedule_text()
        weather = self._format_weather_for_prompt() if callable(getattr(self, "_format_weather_for_prompt", None)) else ""
        outfit_profile = self._select_daily_outfit_profile(
            schedule_hint=schedule_hint,
            weather=weather,
            date_key=today,
        )
        prompt_text = self._build_daily_outfit_photo_prompt(
            diary if isinstance(diary, dict) else {},
            memory_context=memory_context,
            outfit_profile=outfit_profile,
        )
        prompt_sections = self._build_daily_outfit_photo_prompt_sections(
            diary if isinstance(diary, dict) else {},
            memory_context=memory_context,
            outfit_profile=outfit_profile,
        )
        backend_name, image_path, note = await self._generate_photo_image(
            workflow_kind="selfie",
            prompt_text=prompt_text,
            request_text=prompt_text,
            session_key="daily_outfit",
            image_size="1024x1024",
            allow_daily_outfit_reference=False,
            prompt_sections=prompt_sections,
        )
        if image_path:
            return await self._record_daily_outfit_photo_result(
                today,
                image_path,
                "",
                backend=backend_name,
                prompt=prompt_text,
                note=note,
                outfit_profile=outfit_profile,
            )
        return await self._record_daily_outfit_photo_result(
            today,
            "",
            _single_line(note, 220) or "生图失败",
            backend=backend_name,
            prompt=prompt_text,
            note=note,
            outfit_profile=outfit_profile,
        )

    async def _record_daily_outfit_photo_result(
        self,
        date_key: str,
        image_path: str,
        error: str = "",
        *,
        backend: str = "",
        prompt: str = "",
        note: str = "",
        outfit_profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        item = {
            "date": _single_line(date_key, 20),
            "path": _path_text(image_path, 1000),
            "error": _single_line(error, 240),
            "backend": _single_line(backend, 80),
            "prompt": _single_line(prompt, 500),
            "note": _single_line(note, 220),
            "generated_at": _now_ts(),
            "outfit_profile": self._normalize_daily_outfit_profile(outfit_profile),
        }
        async with self._data_lock:
            history = self._daily_outfit_history_items(include_current=True)
            if image_path:
                history.insert(0, dict(item))
            self.data["daily_outfit_history"] = history[:30]
            self.data["daily_outfit_photo"] = item
            self._save_data_sync(
                sections={"daily_outfit_history", "daily_outfit_photo"}
            )
        if image_path:
            await self._memory_companion_record_daily_outfit(item)
            logger.info(
                "每日穿搭照片已生成: backend=%s path=%s",
                _single_line(backend, 80) or "-",
                _single_line(image_path, 160),
            )
        else:
            logger.info("每日穿搭照片未生成: %s", _single_line(error or note, 180))
        return item

    def _daily_outfit_schedule_text(self) -> str:
        plan = self.data.get("daily_plan", {}) if isinstance(getattr(self, "data", {}), dict) else {}
        if not isinstance(plan, dict):
            return ""
        items = plan.get("items")
        if not isinstance(items, list) or not items:
            return ""
        lines: list[str] = []
        for item in items[:12]:
            if not isinstance(item, dict):
                continue
            time_text = _single_line(item.get("time"), 12)
            activity = _single_line(item.get("activity"), 120)
            mood = _single_line(item.get("mood"), 24)
            if not activity:
                continue
            line = f"{time_text} {activity}".strip()
            if mood:
                line = f"{line}（{mood}）"
            lines.append(line)
        return _single_line("；".join(lines), 620)

    @staticmethod
    def _normalize_daily_outfit_profile(profile: Any) -> dict[str, str]:
        if not isinstance(profile, dict):
            return {}
        limits = {
            "look_id": 80,
            "scene": 32,
            "weather": 32,
            "palette": 120,
            "silhouette": 120,
            "top": 160,
            "outer": 160,
            "bottom": 140,
            # 衣柜生图投影里有 footwear：不加进来会被静默丢弃
            "footwear": 140,
            "accessory": 140,
        }
        return {
            key: value
            for key, maximum in limits.items()
            if (value := _single_line(profile.get(key), maximum))
        }

    def _daily_outfit_history_items(self, *, include_current: bool = True) -> list[dict[str, Any]]:
        data = self.data if isinstance(getattr(self, "data", {}), dict) else {}
        candidates: list[Any] = []
        if include_current:
            candidates.append(data.get("daily_outfit_photo"))
        raw_history = data.get("daily_outfit_history")
        if isinstance(raw_history, list):
            candidates.extend(raw_history)

        history: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw_item in candidates:
            if not isinstance(raw_item, dict):
                continue
            path = _path_text(raw_item.get("path"), 1000)
            if not path:
                continue
            profile = self._normalize_daily_outfit_profile(raw_item.get("outfit_profile"))
            item = {
                "date": _single_line(raw_item.get("date"), 20),
                "path": path,
                "generated_at": _single_line(raw_item.get("generated_at"), 40),
                "outfit_profile": profile,
            }
            identity = "|".join(
                (
                    item["path"],
                    item["generated_at"],
                    item["date"],
                    profile.get("look_id", ""),
                )
            )
            if identity in seen:
                continue
            seen.add(identity)
            history.append(item)
        return history[:30]

    def _daily_outfit_rotation_history(self) -> list[dict[str, Any]]:
        rotation_days = _safe_int(
            runtime_persona_setting(self, "daily_outfit_rotation_days", 10),
            10,
            1,
            30,
        )
        # 与写入侧 _today_key() 保持同一插件时区，避免截止日跨时区偏差一天。
        cutoff = datetime.strptime(_today_key(), "%Y-%m-%d").date() - timedelta(
            days=rotation_days - 1
        )
        history: list[dict[str, Any]] = []
        for item in self._daily_outfit_history_items():
            profile = self._normalize_daily_outfit_profile(item.get("outfit_profile"))
            if not profile:
                continue
            date_key = _single_line(item.get("date"), 20)
            try:
                item_date = datetime.strptime(date_key, "%Y-%m-%d").date()
            except (TypeError, ValueError):
                item_date = None
            if item_date and item_date < cutoff:
                continue
            history.append({**item, "outfit_profile": profile})
        return history[:30]

    @staticmethod
    def _daily_outfit_scene_kind(schedule_hint: str, weather: str) -> str:
        text = f"{schedule_hint} {weather}".lower()
        if any(token in text for token in ("运动", "跑步", "健身", "体育", "workout", "gym", "running")):
            return "sport"
        if any(token in text for token in ("校服", "上课", "教室", "学校", "自习", "放学", "school", "class")):
            return "school"
        if any(token in text for token in ("上班", "工作", "会议", "通勤", "办公室", "office", "commute", "meeting")):
            return "commute"
        if any(token in text for token in ("家", "房间", "卧室", "午休", "起床", "睡前", "入睡", "home", "bedroom")):
            return "home"
        return "daily"

    @staticmethod
    def _daily_outfit_weather_kind(weather: str) -> str:
        text = _single_line(weather, 240).lower()
        if any(token in text for token in ("冷", "降温", "低温", "寒", "雪", "snow", "cold")):
            return "cold"
        if any(token in text for token in ("热", "高温", "闷", "暑", "hot", "heat")):
            return "hot"
        if any(token in text for token in ("雨", "阵雨", "雷", "storm", "rain", "wet")):
            return "rainy"
        return "mild"

    @staticmethod
    def _daily_outfit_outer_options(scene: str, weather_kind: str) -> list[str]:
        if scene == "sport":
            options = {
                "cold": [
                    "lightweight insulated track jacket",
                    "technical hooded running jacket",
                    "short quilted sports jacket",
                    "fleece zip-up athletic layer",
                ],
                "hot": [
                    "no heavy outer layer, breathable short-sleeve overshirt",
                    "no outer layer, airy sun-protection layer tied at the waist",
                    "no outer layer, light mesh sports layer",
                    "no outer layer, sleeveless technical vest",
                ],
                "rainy": [
                    "water-resistant hooded running jacket",
                    "compact rain shell with reflective trim",
                    "light technical windbreaker",
                    "hooded quick-dry sports jacket",
                ],
                "mild": [
                    "clean zip-up track jacket",
                    "lightweight athletic windbreaker",
                    "soft cropped sports jacket",
                    "open technical overshirt",
                ],
            }
            return options[weather_kind]
        if scene == "home":
            options = {
                "cold": [
                    "soft oversized knit cardigan",
                    "warm zip-up hoodie",
                    "light quilted home jacket",
                    "plush lounge cardigan",
                ],
                "hot": [
                    "no outer layer, loose breathable overshirt",
                    "no outer layer, thin open cotton shirt",
                    "no outer layer, airy short-sleeve layer",
                    "no outer layer, light linen cardigan",
                ],
                "rainy": [
                    "soft hooded cardigan for a rainy day indoors",
                    "light zip-up hoodie",
                    "cozy knit cardigan",
                    "thin water-resistant overshirt near the doorway",
                ],
                "mild": [
                    "soft open cardigan",
                    "light zip-up hoodie",
                    "relaxed cotton overshirt",
                    "thin knit vest",
                ],
            }
            return options[weather_kind]
        options = {
            "cold": [
                "camel wool coat with a soft scarf",
                "short dark quilted jacket",
                "navy duffle coat",
                "light gray padded jacket",
            ],
            "hot": [
                "no heavy outer layer, breathable overshirt left open",
                "no outer layer, thin sun-protection cardigan",
                "no outer layer, rolled-sleeve linen overshirt",
                "no outer layer, light short-sleeve shirt layer",
            ],
            "rainy": [
                "water-resistant hooded jacket",
                "light trench coat suitable for rain",
                "compact windbreaker with a hood",
                "short rain shell with clean lines",
            ],
            "mild": [
                "soft knit cardigan",
                "light denim jacket",
                "short bomber jacket",
                "unstructured lightweight blazer",
            ],
        }
        return options[weather_kind]

    def _daily_outfit_candidate_profiles(self, scene: str, weather_kind: str) -> list[dict[str, str]]:
        base_options: dict[str, list[dict[str, str]]] = {
            "school": [
                {"palette": "navy, ivory, and muted burgundy", "silhouette": "neat layered campus silhouette", "top": "crisp white shirt with a navy knit vest", "bottom": "straight-cut charcoal trousers", "accessory": "small burgundy ribbon and a simple watch"},
                {"palette": "pale blue, gray, and silver", "silhouette": "clean relaxed academic silhouette", "top": "pale blue oxford shirt under a fine gray cardigan", "bottom": "neat navy trousers", "accessory": "slim silver hair clip or lapel pin"},
                {"palette": "cream, forest green, and warm brown", "silhouette": "soft collegiate silhouette", "top": "cream sweatshirt with a collared shirt edge showing", "bottom": "tailored brown trousers", "accessory": "small canvas shoulder bag"},
                {"palette": "black, white, and dusty rose", "silhouette": "compact modern campus silhouette", "top": "fine striped tee beneath a clean black overshirt", "bottom": "straight dark jeans", "accessory": "subtle rose-toned hair tie or keychain"},
                {"palette": "sage green, ivory, and charcoal", "silhouette": "quiet knitwear silhouette", "top": "sage knit polo layered over a light tee", "bottom": "relaxed charcoal slacks", "accessory": "small geometric earrings or a simple ring"},
                {"palette": "lavender, cream, and deep gray", "silhouette": "light preppy silhouette", "top": "lavender crewneck knit over a white collar", "bottom": "clean deep-gray trousers", "accessory": "thin patterned scarf or a neat ribbon"},
            ],
            "commute": [
                {"palette": "charcoal, ivory, and cobalt blue", "silhouette": "clean tailored commute silhouette", "top": "ivory ribbed knit top with a cobalt accent", "bottom": "straight charcoal trousers", "accessory": "minimal metal watch and structured tote"},
                {"palette": "sand, white, and muted olive", "silhouette": "relaxed smart-casual silhouette", "top": "white shirt under a muted olive knit vest", "bottom": "sand-colored tapered trousers", "accessory": "small leather crossbody bag"},
                {"palette": "black, soft gray, and wine red", "silhouette": "sleek layered city silhouette", "top": "soft gray mock-neck top with a wine-red scarf accent", "bottom": "black straight-leg pants", "accessory": "simple silver earrings or cufflinks"},
                {"palette": "dusty blue, cream, and camel", "silhouette": "light professional silhouette", "top": "dusty-blue blouse or shirt with a cream knit layer", "bottom": "camel tailored trousers", "accessory": "thin belt and understated wristwatch"},
                {"palette": "deep green, black, and ivory", "silhouette": "structured modern silhouette", "top": "deep-green fine knit with a crisp ivory collar", "bottom": "black pleated trousers", "accessory": "small enamel pin and clean shoulder bag"},
                {"palette": "warm taupe, navy, and white", "silhouette": "comfortable polished silhouette", "top": "warm taupe long-sleeve tee beneath a navy overshirt", "bottom": "white or light-stone straight trousers", "accessory": "subtle patterned scarf"},
            ],
            "sport": [
                {"palette": "cobalt blue, white, and graphite", "silhouette": "clean athletic silhouette", "top": "cobalt quick-dry training tee", "bottom": "graphite track pants", "accessory": "simple sports watch and compact water bottle"},
                {"palette": "black, lime green, and gray", "silhouette": "light running silhouette", "top": "black technical long-sleeve top with lime trim", "bottom": "gray joggers", "accessory": "small sweatband and running watch"},
                {"palette": "coral, navy, and white", "silhouette": "bright casual sports silhouette", "top": "coral breathable tee under a navy sleeveless layer", "bottom": "navy athletic pants", "accessory": "minimal cap or hair band"},
                {"palette": "sage green, cream, and black", "silhouette": "relaxed outdoor exercise silhouette", "top": "sage performance polo with a cream inner layer", "bottom": "black tapered joggers", "accessory": "small crossbody sports pouch"},
                {"palette": "lavender, charcoal, and silver", "silhouette": "soft technical silhouette", "top": "lavender moisture-wicking zip collar top", "bottom": "charcoal training pants", "accessory": "reflective wrist band"},
                {"palette": "rust orange, white, and deep blue", "silhouette": "energetic training silhouette", "top": "rust-orange athletic tee with a white panel", "bottom": "deep-blue track pants", "accessory": "compact earbud case or sports watch"},
            ],
            "home": [
                {"palette": "cream, pale blue, and soft gray", "silhouette": "soft relaxed home silhouette", "top": "cream cotton lounge top", "bottom": "pale-blue relaxed pants", "accessory": "simple fabric hair band or soft slippers"},
                {"palette": "sage green, ivory, and warm brown", "silhouette": "cozy knitwear silhouette", "top": "sage knit tee over an ivory inner layer", "bottom": "warm-brown lounge trousers", "accessory": "small mug held naturally"},
                {"palette": "lavender, charcoal, and white", "silhouette": "quiet oversized silhouette", "top": "lavender oversized sweatshirt", "bottom": "charcoal soft joggers", "accessory": "thin reading glasses or a simple hair clip"},
                {"palette": "dusty rose, cream, and gray", "silhouette": "light comfortable silhouette", "top": "dusty-rose long-sleeve tee", "bottom": "cream cotton pants", "accessory": "small pendant necklace"},
                {"palette": "navy, light gray, and muted yellow", "silhouette": "casual layered home silhouette", "top": "navy striped lounge shirt", "bottom": "light-gray relaxed pants", "accessory": "muted-yellow blanket edge or soft socks"},
                {"palette": "white, olive, and soft black", "silhouette": "minimal restful silhouette", "top": "white breathable henley shirt", "bottom": "olive lounge pants", "accessory": "small wireless earbud case"},
            ],
            "daily": [
                {"palette": "denim blue, white, and red", "silhouette": "casual layered street silhouette", "top": "white tee under a denim-blue overshirt", "bottom": "dark straight-leg jeans", "accessory": "small red hair tie or keychain"},
                {"palette": "cream, black, and forest green", "silhouette": "clean relaxed silhouette", "top": "cream ribbed knit top with a forest-green collar layer", "bottom": "black tapered trousers", "accessory": "minimal canvas crossbody bag"},
                {"palette": "dusty rose, charcoal, and ivory", "silhouette": "soft modern silhouette", "top": "dusty-rose sweatshirt over an ivory tee", "bottom": "charcoal straight trousers", "accessory": "small silver pendant"},
                {"palette": "sage green, navy, and light gray", "silhouette": "easy outdoor silhouette", "top": "sage polo layered with a light-gray tee", "bottom": "navy relaxed trousers", "accessory": "simple cap or structured backpack"},
                {"palette": "lavender, white, and deep blue", "silhouette": "light casual silhouette", "top": "lavender knit tee with a white collar detail", "bottom": "deep-blue jeans", "accessory": "thin patterned scarf"},
                {"palette": "warm brown, ivory, and muted orange", "silhouette": "textured everyday silhouette", "top": "ivory henley shirt beneath a warm-brown knit vest", "bottom": "muted-orange straight trousers", "accessory": "small leather bracelet or watch"},
            ],
        }
        outer_options = self._daily_outfit_outer_options(scene, weather_kind)
        candidates: list[dict[str, str]] = []
        for base_index, base in enumerate(base_options.get(scene, base_options["daily"])):
            for outer_index, outer in enumerate(outer_options):
                candidates.append(
                    {
                        **base,
                        "scene": scene,
                        "weather": weather_kind,
                        "outer": outer,
                        "look_id": f"{scene}-{base_index + 1}-{outer_index + 1}",
                    }
                )
        return candidates

    def _select_daily_outfit_profile(
        self,
        *,
        schedule_hint: str,
        weather: str,
        date_key: str = "",
    ) -> dict[str, str]:
        # 衣柜接管：有可用裁决时优先用它（取不到就落回作者的候选表）
        wardrobe_profile = resolve_wardrobe_daily_outfit_profile(self, date_key=date_key)
        if wardrobe_profile:
            return wardrobe_profile
        scene = self._daily_outfit_scene_kind(schedule_hint, weather)
        weather_kind = self._daily_outfit_weather_kind(weather)
        candidates = self._daily_outfit_candidate_profiles(scene, weather_kind)
        if not candidates:
            return {}
        history = self._daily_outfit_rotation_history()
        fields = ("palette", "silhouette", "top", "outer", "bottom", "footwear", "accessory")
        weights = {
            "palette": 16,
            "silhouette": 12,
            "top": 20,
            "outer": 18,
            "bottom": 10,
            "footwear": 9,
            "accessory": 8,
        }

        def cooldown_score(candidate: dict[str, str]) -> int:
            score = 0
            for index, item in enumerate(history):
                previous = self._normalize_daily_outfit_profile(item.get("outfit_profile"))
                if not previous:
                    continue
                recency_weight = max(1, 8 - index)
                matching_fields = sum(
                    1
                    for field in fields
                    if candidate.get(field) and candidate.get(field) == previous.get(field)
                )
                score += sum(
                    weights[field] * recency_weight
                    for field in fields
                    if candidate.get(field) and candidate.get(field) == previous.get(field)
                )
                if candidate.get("look_id") == previous.get("look_id"):
                    score += 600 * recency_weight
                if index == 0:
                    changed_fields = len(fields) - matching_fields
                    if changed_fields < 2:
                        score += 10000
                    elif changed_fields < 3:
                        score += 800
            return score

        rotation_seed = f"{date_key or _today_key()}|{len(history)}"

        def tie_breaker(candidate: dict[str, str]) -> int:
            digest = hashlib.sha1(
                f"{rotation_seed}|{candidate.get('look_id', '')}".encode("utf-8")
            ).hexdigest()
            return int(digest[:12], 16)

        return min(candidates, key=lambda candidate: (cooldown_score(candidate), tie_breaker(candidate)))

    def _daily_outfit_rotation_reference(self) -> str:
        history = self._daily_outfit_rotation_history()
        if not history:
            return ""

        def collect(fields: dict[str, str]) -> list[str]:
            fragments: list[str] = []
            for field, label in fields.items():
                values: list[str] = []
                for item in history:
                    profile = self._normalize_daily_outfit_profile(item.get("outfit_profile"))
                    value = _single_line(profile.get(field), 56)
                    if value and value not in values:
                        values.append(value)
                    if len(values) >= 2:
                        break
                if values:
                    fragments.append(f"{label}: {' / '.join(values)}")
            return fragments

        fragments = collect(
            {
                "palette": "color palettes",
                "outer": "outer layers",
                "silhouette": "silhouettes",
            }
        )
        if not fragments:
            # 衣柜接管的投影只有 top/outer/bottom/footwear（没有 palette/silhouette），
            # 不退一步的话这句 "avoid repeating" 约束会整段从照片提示词里消失。
            fragments = collect({"top": "tops", "bottom": "bottoms", "footwear": "footwear"})
        return _single_line("; ".join(fragments), 280)

    def _format_weather_for_prompt(self) -> str:
        data = getattr(self, "data", None)
        weather = data.get("daily_weather") if isinstance(data, dict) else None
        if not isinstance(weather, dict):
            return ""
        formatter = getattr(self, "_weather_summary_text", None)
        if callable(formatter):
            try:
                text = _single_line(formatter(weather), 120)
                if text and text != "暂无天气信息":
                    return text
            except Exception:
                pass
        text = _single_line(weather.get("prompt"), 120)
        return "" if text == "暂无天气信息" else text

    def _build_daily_outfit_photo_prompt(
        self,
        diary: dict[str, Any],
        *,
        memory_context: str = "",
        outfit_profile: dict[str, Any] | None = None,
    ) -> str:
        sections = self._build_daily_outfit_photo_prompt_sections(
            diary,
            memory_context=memory_context,
            outfit_profile=outfit_profile,
        )
        prompt = (
            "Positive prompt: "
            + ", ".join(
                section.content.positive
                for section in sections
                if isinstance(section.content, PhotoPromptContent)
                and section.content.positive
            )
            + ". Negative prompt: "
            + ", ".join(
                section.content.negative
                for section in sections
                if isinstance(section.content, PhotoPromptContent)
                and section.content.negative
            )
            + "."
        )
        return _single_line(prompt, 1400)
