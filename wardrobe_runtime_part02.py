# -*- coding: utf-8 -*-
"""WardrobePart02Mixin。

由 tools/split_mixin_domain.py 从 wardrobe_runtime.py 机械抽取（18 个方法 + 0 个模块级名字 + 0 个类级赋值 / 576 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 WardrobeMixin）。
"""
from __future__ import annotations

from .wardrobe_runtime_shared import (
    WARDROBE_DETAIL_PROGRESSIVE,
    WARDROBE_DETAIL_TOOL_HINT,
    WARDROBE_INTENT_KEY,
    WARDROBE_INTENT_SOURCE_MODEL,
    WARDROBE_INTENT_TTL_SECONDS,
    WARDROBE_MINIMAL_MAX_CHARS,
    WARDROBE_PROMPT_KEY,
)
from .wardrobe_runtime_shared import Any
from .wardrobe_runtime_shared import Mapping
from .wardrobe_runtime_shared import OWNERSHIP_OWNED
from .wardrobe_runtime_shared import OWNERSHIP_REFERENCE
from .wardrobe_runtime_shared import PromptSection
from .wardrobe_runtime_shared import WARDROBE_PROMPT_MAX_CHARS
from .wardrobe_runtime_shared import _now_ts
from .wardrobe_runtime_shared import _single_line
from .wardrobe_runtime_shared import _today_key
from .wardrobe_runtime_shared import asyncio
from .wardrobe_runtime_shared import build_wardrobe_outfit_request
from .wardrobe_runtime_shared import hashlib
from .wardrobe_runtime_shared import json
from .wardrobe_runtime_shared import logger
from .wardrobe_runtime_shared import outfit_photo_profile
from .wardrobe_runtime_shared import outfit_photo_profile_from_items
from .wardrobe_runtime_shared import parse_wardrobe_outfit_reply
from .wardrobe_runtime_shared import prompt_section
from .wardrobe_runtime_shared import render_generated_outfit
from .wardrobe_runtime_shared import render_reference_profile
from .wardrobe_runtime_shared import render_wardrobe_outfit_prompt
from .wardrobe_runtime_shared import render_wardrobe_prompt
from .wardrobe_runtime_shared import render_worn_items
from .wardrobe_runtime_shared import select_wardrobe_outfit



class WardrobePart02Mixin:
    """WardrobePart02Mixin（从 WardrobeMixin 拆出）。"""


    def _wardrobe_set_intent(
        self, intent: Any = "", *, items: Any = "", outfit: Any = "", user: Any = None
    ) -> dict[str, Any]:
        """把「本会话要穿什么」写进作者那套 dialogue_outfit_override。

        与作者的正则写同一个 key、同一套过期规则（当日 + 12h），不新建存储 ——
        作者的三个消费点（连续性段落 / 日程调整 / scene_context）因此自动生效。
        按约定，后写的覆盖先写的，source 只用于面板展示。
        """

        outcome: dict[str, Any] = {
            "ok": False,
            "instruction": "",
            "source": WARDROBE_INTENT_SOURCE_MODEL,
            "resolved": [],
            "unresolved": [],
            "outfit": "",
            "error": "",
        }
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            outcome["error"] = "当前运行时不支持记录穿衣意图。"
            return outcome
        # 总开关关掉时读写两个工具必须一致：读工具已被按请求摘掉，写工具若不拦，
        # 管理员关掉衣柜后模型仍能改角色着装（还会写进作者的连续性段落）。
        if not self._wardrobe_enabled():
            outcome["error"] = "角色衣柜没有启用。"
            return outcome
        # 门禁：作者那条写同一个 key 的路径只认主要用户（daily_state 里
        # `_private_user_role(user) != "owner"` 直接返回）。这条 key 是全局的
        # （生图与面板读的是不带用户过滤的那一份），放任任何人群里喊一句就覆盖，
        # 等于把主人的意图静默清空、让照片按别人的要求穿。**取不到角色判定时一律拒绝**。
        role_getter = getattr(self, "_private_user_role", None)
        role = ""
        if callable(role_getter):
            try:
                role = _single_line(role_getter(user), 24)
            except Exception:
                role = ""
        if role != "owner":
            logger.info("衣柜穿衣意图写入被拒（非主要用户）: role=%s", role or "unknown")
            outcome["error"] = "只有主要用户可以改变角色今天的着装。"
            return outcome
        picked, unresolved, outfit_id, outfit_name = self._wardrobe_resolve_intent(items, outfit)
        clean_intent = _single_line(intent, 180)
        if not clean_intent and not picked and not outfit_id:
            outcome["error"] = "没有可记录的换装内容：给出想换上的衣物名称，或一句换装描述。"
            return outcome
        if not clean_intent:
            names = "、".join(str(row.get("name") or "") for row in picked) or outfit_name
            clean_intent = f"换上{names}" if names else "换装"
        user_id = (
            _single_line((user or {}).get("user_id"), 80)
            if isinstance(user, Mapping)
            else ""
        )
        now = _now_ts()
        snapshot = {
            "date": _today_key(),
            "instruction": clean_intent,
            "source": WARDROBE_INTENT_SOURCE_MODEL,
            "source_user_id": user_id,
            "created_at": now,
            "expires_at": now + WARDROBE_INTENT_TTL_SECONDS,
            "wardrobe_items": [str(row.get("id") or "") for row in picked],
            "wardrobe_outfit_id": outfit_id,
        }
        data[WARDROBE_INTENT_KEY] = snapshot
        self._schedule_wardrobe_intent_save()
        outcome.update(
            {
                "ok": True,
                "instruction": clean_intent,
                "resolved": [str(row.get("name") or "") for row in picked],
                "unresolved": unresolved,
                "outfit": outfit_name,
                "expires_at": snapshot["expires_at"],
            }
        )
        return outcome

    def _wardrobe_clear_intent(self) -> bool:
        """清掉本会话的换装意图（面板纠正用）；没有就返回 False。"""

        data = getattr(self, "data", None)
        if not isinstance(data, dict) or not data.get(WARDROBE_INTENT_KEY):
            return False
        data[WARDROBE_INTENT_KEY] = {}
        self._schedule_wardrobe_intent_save()
        return True

    def _wardrobe_intent_user(self, event: Any) -> dict[str, Any]:
        """从事件里定位当前用户：意图要写给他本人，而不是「当前人格的某个人」。"""

        getter = getattr(self, "_event_sender_id", None)
        user_id = ""
        if callable(getter):
            try:
                user_id = _single_line(getter(event), 80)
            except Exception:
                user_id = ""
        if not user_id:
            try:
                user_id = _single_line(event.get_sender_id(), 80)
            except Exception:
                user_id = ""
        loader = getattr(self, "_get_user", None)
        if user_id and callable(loader):
            try:
                record = loader(user_id)
            except Exception:
                record = None
            if isinstance(record, Mapping):
                return dict(record)
        return {"user_id": user_id} if user_id else {}

    def _wardrobe_intent_reply(
        self, intent: Any = "", *, items: Any = "", outfit: Any = "", user: Any = None
    ) -> str:
        """工具返回值：JSON 字符串（宿主以 role:"tool" 回灌给模型）。"""

        try:
            outcome = self._wardrobe_set_intent(intent, items=items, outfit=outfit, user=user)
        except Exception as exc:
            logger.warning("记录穿衣意图失败: %s", _single_line(exc, 160))
            return json.dumps(
                {"status": "error", "message": "记录穿衣意图失败。"}, ensure_ascii=False
            )
        return json.dumps(outcome, ensure_ascii=False)

    def _wardrobe_prompt_section(
        self, user: Any = None, text: Any = "", *, detail_tool: bool = False
    ) -> PromptSection | None:
        """Build the wardrobe prompt section, or ``None`` when it should not inject.

        text 是本轮用户消息：渐进披露模式下用它判断"要不要展开完整描述"。

        detail_tool 为真时在段落末尾追加一句「可以调用 pc_query_wardrobe_detail」——
        由调用方先挂好工具再传进来，避免提示词里写了一件这次请求根本没有的工具。
        """

        if not self._wardrobe_enabled() or not self._wardrobe_prompt_mode():
            return None
        tendency = self._wardrobe_tendency()
        items = self._wardrobe_items()
        outfits = self._wardrobe_outfits()
        if not tendency and not items and not outfits:
            return None
        progressive = self._wardrobe_injection_detail() == WARDROBE_DETAIL_PROGRESSIVE
        # 本会话已经明确换装时整段以它为准：绝不能再把轮换裁决出的那一套标成
        # 「当前着装」，否则同一轮提示词里会出现两段互相矛盾的说法 —— 作者的
        # 连续性段落说「最近一次明确换装：泳衣…不得自行恢复旧服装」，我们这边
        # 却写着「当前着装：短裤」。
        body = self._wardrobe_override_body(
            self._wardrobe_dialogue_override(user), tendency
        )
        # 注意这里是**嵌套 if 不是 elif 链**：最小集可能因为「衣柜里全是参考件」返回空串，
        # 用 elif 会被短路成 return None —— 整段衣柜（连参考风格画像）静默消失。
        if not body and progressive and not self._wardrobe_detail_triggered(text):
            # 常驻最小集：每轮都发，但很短；细节等触发。
            body = self._wardrobe_minimal_body(user, tendency)
        if not body and self._wardrobe_outfit_mode() == "select":
            body = self._wardrobe_selected_outfit_body(user, tendency)
        if not body:
            body = render_wardrobe_prompt(
                tendency,
                items,
                max_items=self._wardrobe_prompt_item_limit(),
                max_chars=WARDROBE_PROMPT_MAX_CHARS,
            )
        if not body:
            return None
        # 参考风格画像：把大量参考压成一行，只在放得下时才追加
        # （整套本身比风格画像重要，放不下就牺牲画像）。
        profile_line = self._wardrobe_reference_profile_line()
        if profile_line and len(body) + len(profile_line) + 1 <= WARDROBE_PROMPT_MAX_CHARS:
            body = f"{body}\n{profile_line}"
        # 工具提示排最后：放不下就牺牲它（工具本身还在，只是模型不知道，
        # 退化成现在这套「关键词触发展开」的行为）。
        if detail_tool and len(body) + len(WARDROBE_DETAIL_TOOL_HINT) + 1 <= WARDROBE_PROMPT_MAX_CHARS:
            body = f"{body}\n{WARDROBE_DETAIL_TOOL_HINT}"
        return prompt_section(
            key=WARDROBE_PROMPT_KEY,
            title="角色衣柜",
            source="wardrobe",
            content=body,
        )

    def _wardrobe_resolved_outfit(self, user: Any = None) -> dict[str, Any]:
        """「今天这一身」的**唯一**解析入口：本会话意图 > 生成器缓存 > 规则裁决。

        段落（select 模式）、渐进披露最小集、只读工具的 today、面板预览都必须走这里。
        否则同一天会出现多套答案 —— 实测：生成器开启时提示词按缓存写「奶油色针织开衫」，
        模型照段落里那句提示去调工具，拿回的是规则裁决的「白衬衫黑纱裙」。

        返回值与 :func:`select_wardrobe_outfit` 同形，另外在 override 时多一个
        ``instruction``（意图原文），供最小集与工具复用。
        """

        override = self._wardrobe_dialogue_override(user)
        if override:
            items = self._wardrobe_override_items(override)
            return {
                "source": "dialogue_override",
                "scene": self._wardrobe_current_scene(),
                "style": "",
                "prompt_text": render_worn_items(items),
                "profile": outfit_photo_profile_from_items(items),
                "picked": [
                    {
                        "id": str(item.get("id") or ""),
                        "name": str(item.get("name") or ""),
                        "slot": str(item.get("slot") or ""),
                        "intimate": bool(item.get("intimate")),
                    }
                    for item in items
                ],
                "look_id": "",
                "outfit_name": "",
                "instruction": _single_line(override.get("instruction"), 180),
            }
        generated = self._wardrobe_cached_generated_outfit()
        if generated is not None:
            return self._wardrobe_generated_selection(generated, self._wardrobe_current_scene())
        return self._wardrobe_outfit_selection(user)

    def _wardrobe_selected_outfit_body(self, user: Any = None, tendency: Any = "") -> str:
        """Body for the select mode: only the resolved outfit, not the inventory.

        Falls back to the full listing when nothing can be resolved (an empty
        wardrobe, or no item carrying a usable slot) so the section never
        silently becomes empty.
        """

        selection = self._wardrobe_resolved_outfit(user)
        if selection.get("source") == "dialogue_override":
            # 意图段落由 _wardrobe_override_body 负责（它要带 instruction 行，且不能出现
            # 「当前着装：」标题）；走到这里说明调用方绕过了那条路，交回清单更安全。
            return ""
        if selection.get("source") == "rule":
            # 同步路径不能等模型：只有落到规则裁决时才排后台生成，下一轮就能用上结果。
            self._schedule_wardrobe_outfit_generation(user)
        body = render_wardrobe_outfit_prompt(tendency, selection)
        if body:
            return body
        return render_wardrobe_prompt(
            tendency,
            self._wardrobe_items(),
            max_items=self._wardrobe_prompt_item_limit(),
            max_chars=WARDROBE_PROMPT_MAX_CHARS,
        )

    def _wardrobe_generator_enabled(self) -> bool:
        return self._wardrobe_bool(
            self._wardrobe_setting("enable_wardrobe_outfit_generate", False), False
        )

    def _wardrobe_generator_provider_id(self) -> str:
        return _single_line(self._wardrobe_setting("WARDROBE_OUTFIT_PROVIDER_ID", ""), 160)

    def _wardrobe_current_weather(self) -> str:
        getter = getattr(self, "_format_weather_for_prompt", None)
        if not callable(getter):
            return ""
        try:
            return _single_line(getter(), 120)
        except Exception:
            return ""

    def _wardrobe_outfit_cache_key(self) -> str:
        """Keep each persona's daily result tied to the complete wardrobe."""

        def content(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return [
                {key: value for key, value in row.items() if key not in {"created_at", "updated_at"}}
                for row in rows
            ]

        inputs = {
            "persona": self._wardrobe_persona_id(),
            "scene": self._wardrobe_current_scene(),
            "weather": self._wardrobe_current_weather(),
            "items": content(self._wardrobe_items()),
            "outfits": content(self._wardrobe_outfits()),
            "tendency": self._wardrobe_tendency(),
            "provider": self._wardrobe_generator_provider_id(),
            "prompt": self._wardrobe_setting("task_prompt_overrides", {}),
        }
        digest = hashlib.sha256(
            json.dumps(inputs, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        return f"{_today_key()}|{digest}"

    def _wardrobe_outfit_cache(self) -> dict[str, Any]:
        """In-memory cache for generated outfits.

        Deliberately in memory: persisting it would need a new data key in the
        author's store, which this change set avoids. The consequence is that a
        plugin restart on the same day regenerates the outfit once.
        """

        cache = getattr(self, "_wardrobe_outfit_cache_store", None)
        if not isinstance(cache, dict):
            cache = {}
            self._wardrobe_outfit_cache_store = cache
        return cache

    def _wardrobe_cached_generated_outfit(self) -> dict[str, Any] | None:
        if not self._wardrobe_generator_enabled():
            return None
        cache = getattr(self, "_wardrobe_outfit_cache_store", {})
        cached = cache.get(self._wardrobe_outfit_cache_key()) if isinstance(cache, dict) else None
        return cached if isinstance(cached, dict) and cached else None

    @staticmethod
    def _wardrobe_generated_selection(generated: dict[str, Any], scene: str) -> dict[str, Any]:
        return {
            "source": "generate",
            "scene": scene,
            "prompt_text": render_generated_outfit(generated),
            "profile": outfit_photo_profile(generated),
            "picked": [],
        }

    def _schedule_wardrobe_outfit_generation(self, user: Any = None) -> None:
        """Kick off generation in the background so the reply path never waits.

        The prompt-section builder is synchronous and sits on the reply path, so
        generating inline would add a model round-trip to the user's first
        message of the day. Instead the current turn uses the rule selection and
        the generated outfit is picked up from the next turn onward.
        """

        if not self._wardrobe_generator_enabled():
            return
        key = self._wardrobe_outfit_cache_key()
        cache = getattr(self, "_wardrobe_outfit_cache_store", {})
        if isinstance(cache, dict) and key in cache:
            return
        runner = getattr(self, "_create_lifecycle_background_task", None)
        if not callable(runner):
            return
        tasks = getattr(self, "_wardrobe_generation_tasks", None)
        if not isinstance(tasks, dict):
            tasks = {}
            self._wardrobe_generation_tasks = tasks
        previous = tasks.get(key)
        if previous is not None and not previous.done():
            return
        operation = self._wardrobe_generate_outfit(user)
        try:
            task = runner(operation, label="wardrobe_outfit_generate")
        except Exception as exc:
            operation.close()
            logger.debug("着装生成任务调度失败: %s", _single_line(exc, 160))
            return
        if task is None:
            operation.close()
            return
        tasks[key] = task

        def discard(finished: asyncio.Task) -> None:
            if tasks.get(key) is finished:
                tasks.pop(key, None)

        task.add_done_callback(discard)

    async def _wardrobe_generate_outfit(self, user: Any = None) -> dict[str, Any] | None:
        """Ask the model to compose the outfit for the current (date, scene).

        Returns None when generation is disabled, unavailable, or produced
        nothing usable -- callers then fall back to the rule selector, so a
        model outage degrades instead of injecting an empty outfit.
        """

        if not self._wardrobe_generator_enabled():
            return None
        caller = getattr(self, "_llm_call", None)
        if not callable(caller):
            return None
        key = self._wardrobe_outfit_cache_key()
        cache = self._wardrobe_outfit_cache()
        if key in cache:
            cached = cache[key]
            return cached if isinstance(cached, dict) and cached else None

        request = build_wardrobe_outfit_request(
            self._wardrobe_items(),
            self._wardrobe_outfits(),
            tendency=self._wardrobe_tendency(),
            scene=self._wardrobe_current_scene(),
            weather=self._wardrobe_current_weather(),
        )
        payload: dict[str, Any] | None = None
        try:
            raw = await caller(
                request,
                max_tokens=700,
                provider_id=self._wardrobe_generator_provider_id() or None,
                task="wardrobe_outfit_generate",
            )
            payload = parse_wardrobe_outfit_reply(raw)
        except Exception as exc:
            logger.warning("着装生成调用失败: %s", _single_line(exc, 160))
            payload = None
        # 失败也缓存：同一天内不再反复重试，直接走规则选择器降级。
        today = _today_key() + "|"
        for stale in list(cache):
            if not stale.startswith(today):
                cache.pop(stale, None)
        while len(cache) >= 128:
            cache.pop(next(iter(cache)))
        cache[key] = payload or {}
        if payload is None:
            logger.info("着装生成未产出可用结果，本次回退规则挑选")
        return payload

    def _wardrobe_outfit_preview(
        self,
        *,
        scene: Any = None,
        weather: Any = None,
        seed: Any = "",
    ) -> dict[str, Any]:
        """Diagnostics for the 搭配测试 panel: what would be injected, and why.

        Strictly read-only -- it never writes config, never calls the model and
        never touches the generation cache, so the panel can be refreshed freely.
        Passing scene/weather overrides the auto-detected ones so the panel can
        simulate other occasions without waiting for the real schedule to change.
        """

        items = self._wardrobe_items()
        outfits = self._wardrobe_outfits()
        tendency = self._wardrobe_tendency()
        clean_scene = (
            self._wardrobe_current_scene() if scene is None else _single_line(scene, 20)
        )
        clean_weather = (
            self._wardrobe_current_weather() if weather is None else _single_line(weather, 120)
        )
        clean_seed = _single_line(seed, 160) or self._wardrobe_outfit_seed()
        mode = self._wardrobe_outfit_mode()

        owned_items = [
            item for item in items
            if str(item.get("ownership") or OWNERSHIP_OWNED) == OWNERSHIP_OWNED
        ]
        owned_outfits = [
            outfit for outfit in outfits
            if str(outfit.get("ownership") or OWNERSHIP_OWNED) == OWNERSHIP_OWNED
        ]
        references = [
            outfit for outfit in outfits
            if str(outfit.get("ownership") or "") == OWNERSHIP_REFERENCE
        ]
        selection = select_wardrobe_outfit(
            owned_items,
            owned_outfits,
            scene=clean_scene,
            seed=clean_seed,
            rotation_days=self._wardrobe_outfit_rotation_days(),
        )
        current_context = (
            clean_scene == self._wardrobe_current_scene()
            and clean_weather == self._wardrobe_current_weather()
            and clean_seed == self._wardrobe_outfit_seed()
        )
        generated = self._wardrobe_cached_generated_outfit() if current_context and mode == "select" else None
        generated_view: dict[str, Any] | None = None
        if generated is not None:
            generated_view = {
                "fields": dict(generated),
                "body": render_generated_outfit(generated),
                "profile": outfit_photo_profile(generated),
            }
        selected = (
            self._wardrobe_generated_selection(generated, clean_scene)
            if generated is not None else selection
        )
        # 预览必须与真实注入同源：意图 > 生成器 > 规则，并且要认渐进披露。
        # 预览没有「本轮消息」，渐进披露按「未触发」算 —— 也就是那个常驻最小集。
        # 面板没有用户身份，意图读的是不带过滤的那一份（与 /wardrobe/intent 一致）。
        override = self._wardrobe_override_snapshot()
        effective_source = ""
        injected = ""
        if self._wardrobe_enabled() and self._wardrobe_prompt_mode():
            if override:
                injected = self._wardrobe_override_body(override, tendency)
                effective_source = "dialogue_override"
            elif self._wardrobe_injection_detail() == WARDROBE_DETAIL_PROGRESSIVE:
                injected = self._wardrobe_minimal_body(None, tendency)
                effective_source = "minimal"
            else:
                effective_source = "generated" if generated is not None else "rule"
            if not injected and mode == "select":
                injected = render_wardrobe_outfit_prompt(tendency, selected)
            if not injected:
                injected = render_wardrobe_prompt(
                    tendency, items, max_items=self._wardrobe_prompt_item_limit(),
                    max_chars=WARDROBE_PROMPT_MAX_CHARS,
                )
                effective_source = effective_source or "inventory"
        key = self._wardrobe_outfit_cache_key()
        cache = getattr(self, "_wardrobe_outfit_cache_store", {})
        tasks = getattr(self, "_wardrobe_generation_tasks", {})
        pending = tasks.get(key) if isinstance(tasks, dict) and current_context else None
        generator_state = (
            "disabled" if not self._wardrobe_generator_enabled() or mode != "select"
            else "ready" if generated is not None
            else "pending" if pending is not None and not pending.done()
            else "failed" if current_context and isinstance(cache, dict) and key in cache
            else "idle"
        )
        return {
            "mode": mode,
            "enabled": self._wardrobe_enabled() and self._wardrobe_prompt_mode(),
            "generator_enabled": self._wardrobe_generator_enabled(),
            "generator_ready": generated is not None,
            "generator_state": generator_state,
            "scene": clean_scene,
            "weather": clean_weather,
            "seed": clean_seed,
            "item_count": len(items),
            "outfit_count": len(outfits),
            "reference_count": len(references),
            "style_profile": render_reference_profile(references),
            # 未分类的衣物没有部位可依据，正常不参与组合；数量暴露给面板，方便
            # 提示用户补全，而不是让他纳闷"为什么这几件从来不出现"。
            "unclassified_count": len(
                [item for item in items if not str(item.get("slot") or "")]
            ),
            "request": build_wardrobe_outfit_request(
                owned_items,
                outfits,
                tendency=tendency,
                scene=clean_scene,
                weather=clean_weather,
            ),
            "rule": {
                "source": str(selected.get("source") or ""),
                "outfit_name": str(selected.get("outfit_name") or ""),
                "look_id": str(selected.get("look_id") or ""),
                "picked": [dict(row) for row in (selected.get("picked") or ())],
                "profile": dict(selected.get("profile") or {}),
                "prompt_text": str(selected.get("prompt_text") or ""),
            },
            "generated": generated_view,
            "selected": selected,
            "injected": injected,
            "injected_chars": len(injected),
            "injected_limit": WARDROBE_PROMPT_MAX_CHARS,
            # 这次注入实际由哪条路径决定（意图 / 生成器 / 规则 / 最小集 / 清单），
            # 让面板能解释「为什么注入的是这一份」。
            "effective_source": effective_source,
            # 渐进披露对比：面板可以直接把"常驻最小集"与完整注入并排显示
            "detail_mode": self._wardrobe_injection_detail(),
            "minimal": self._wardrobe_minimal_body(None, tendency),
            "minimal_limit": WARDROBE_MINIMAL_MAX_CHARS,
        }

    async def _append_group_wardrobe_to_request(self, event: Any, req: Any) -> None:
        """Inject the wardrobe into a group request exactly once."""

        if req is None:
            return
        # 先把只读工具按请求挂好，再决定提示词里要不要写它 —— 顺序不能反，
        # 否则会出现「提示词说有、工具表里没有」。
        syncer = getattr(self, "_sync_wardrobe_detail_tool", None)
        detail_tool = False
        if callable(syncer):
            try:
                detail_tool = bool(syncer(req))
            except Exception as exc:
                logger.debug("群聊衣柜细节工具挂载失败: %s", _single_line(exc, 160))
        builder = getattr(self, "_wardrobe_prompt_section", None)
        if not callable(builder):
            return
        try:
            section = builder(None, detail_tool=detail_tool)
        except Exception as exc:
            logger.debug("群聊角色衣柜提示词构建失败: %s", _single_line(exc, 160))
            return
        if section is None or not section.content:
            return
        placer = getattr(self, "_place_conversation_prompt_section", None)
        if not callable(placer):
            return
        marker = "<!-- private_companion_group_wardrobe -->"
        try:
            placer(req, marker, section, priority=13)
        except Exception as exc:
            logger.debug("群聊角色衣柜注入失败: %s", _single_line(exc, 160))
