# -*- coding: utf-8 -*-
"""DailyStatePlanYesterdaySummaryMixin。

由 tools/split_mixin_domain.py 从 daily_state_plan.py 机械抽取（9 个方法 + 0 个模块级名字 + 0 个类级赋值 / 349 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStatePlanMixin）。
"""
from __future__ import annotations

from .daily_state_plan_shared import _today_key, logger
from .daily_state_plan_shared import Any
from .daily_state_plan_shared import Conversation
from .daily_state_plan_shared import PromptRenderMode
from .daily_state_plan_shared import PromptSection
from .daily_state_plan_shared import _date_key
from .daily_state_plan_shared import _safe_float
from .daily_state_plan_shared import _single_line
from .daily_state_plan_shared import date
from .daily_state_plan_shared import datetime
from .daily_state_plan_shared import json
from .daily_state_plan_shared import prompt_section
from .daily_state_plan_shared import render_prompt_sections
from .daily_state_plan_shared import runtime_persona_setting
from .daily_state_plan_shared import timedelta



class DailyStatePlanYesterdaySummaryMixin:
    """DailyStatePlanYesterdaySummaryMixin（从 DailyStatePlanMixin 拆出）。"""


    async def _ensure_yesterday_conversation_summary(self, force: bool = False) -> dict[str, Any]:
        today = _today_key()
        cached = self.data.get("yesterday_conversation_summary", {})
        if (
            isinstance(cached, dict)
            and cached.get("date") == today
            and cached.get("scope") == "owner_private_only"
            and not force
        ):
            return cached
        raw_text = await self._collect_yesterday_conversation_text()
        if not raw_text:
            summary = {
                "date": today,
                "source_date": _date_key(date.today() - timedelta(days=1)),
                "summary": "暂无可用的昨日完整对话摘要。",
                "residues": [],
                "schedule_reference": "无明确可继承影响。",
                "dream_reference": "无明确可继承碎片。",
                "scope": "owner_private_only",
                "raw_excerpt_chars": 0,
            }
        else:
            summary = await self._summarize_yesterday_conversation_for_schedule(raw_text)
        async with self._data_lock:
            self.data["yesterday_conversation_summary"] = summary
            self._save_data_sync(sections={"yesterday_conversation_summary"})
        return summary

    async def _collect_yesterday_conversation_text(self) -> str:
        users = self.data.get("users", {})
        if not isinstance(users, dict):
            return ""
        now_dt = self._environment_now()
        yesterday = now_dt.date() - timedelta(days=1)
        start = datetime.combine(yesterday, datetime.min.time(), tzinfo=now_dt.tzinfo).timestamp()
        end = start + 24 * 3600
        sections: list[PromptSection] = []
        for user_id, raw_user in users.items():
            if not isinstance(raw_user, dict):
                continue
            if self._private_user_role(raw_user, str(user_id)) != "owner":
                continue
            umo = str(raw_user.get("umo") or "").strip()
            if not umo:
                continue
            try:
                getter = getattr(self, "_get_current_conversation_safely", None)
                if callable(getter):
                    conv = await getter(umo, label="yesterday_conversation_read")
                else:
                    conv_id = await self.context.conversation_manager.get_curr_conversation_id(umo)
                    if not conv_id:
                        continue
                    conv = await self.context.conversation_manager.get_conversation(umo, conv_id)
            except Exception as exc:
                logger.debug("读取昨日对话失败: user=%s err=%s", user_id, exc)
                continue
            if not conv:
                continue
            history = self._load_conversation_history_items(conv)
            dated_lines: list[str] = []
            undated_lines: list[str] = []
            for item in history:
                line = self._format_history_item_for_summary(item)
                if not line:
                    continue
                ts = self._history_item_timestamp(item)
                if ts is None:
                    undated_lines.append(line)
                elif start <= ts < end:
                    dated_lines.append(line)
            selected = dated_lines if dated_lines else undated_lines[-120:]
            if not selected:
                continue
            name = _single_line(raw_user.get("nickname") or user_id, 30)
            source_note = "昨日对话" if dated_lines else "最近对话（history 无时间戳,作为昨日摘要候选）"
            sections.append(
                prompt_section(
                    key=f"background.yesterday_conversation.user_{len(sections)}",
                    title=f"主要用户:{name}｜{source_note}",
                    source="daily_state",
                    content="\n".join(selected),
                )
            )
        return render_prompt_sections(
            sections,
            mode=PromptRenderMode.LABELED_BLOCK,
        ).strip()[-18000:]

    def _load_conversation_history_items(
        self,
        conversation: Conversation | None,
        *,
        tail_only: int | None = None,
    ) -> list[dict[str, Any]]:
        if conversation is None:
            return []
        raw = conversation.history or "[]"
        if tail_only is not None and tail_only > 0:
            tail = self._parse_tail_json_items(raw, tail_only)
            if tail is not None:
                return tail
        try:
            loaded = json.loads(raw)
        except Exception:
            return []
        if not isinstance(loaded, list):
            return []
        return [item for item in loaded if isinstance(item, dict)]

    @staticmethod
    def _parse_tail_json_items(raw: str, count: int) -> list[dict[str, Any]] | None:
        """仅反序列化 JSON 数组从尾部往回数 count 个 dict 元素，避免对超大
        conversation.history 全量 json.loads。

        从右向左逆序扫描元素边界：字符串按"左侧连续反斜杠个数的奇偶性"判定转义
        引号（奇数 => 转义、偶数 => 定界），从而正确区分字符串、嵌套括号与顶层
        逗号；对每个尾部元素单独解码，遇到 dict 才计数，语义与"全量解析后过滤
        dict 再取尾部 count 条"完全一致。任何解析异常、结构不符或不足 count 个
        时返回 None，由调用方回退全量解析，正确性始终有保证。
        """
        if not count or count < 1:
            return None
        text = raw.strip()
        if not (text.startswith("[") and text.endswith("]")):
            return None
        end = len(text) - 1  # 数组右括号下标
        stop = end  # 当前元素区间的右边界(不含)
        i = end - 1
        depth = 0
        in_string = False
        found: list[dict[str, Any]] = []

        def _collect(span_start: int, span_stop: int) -> bool:
            try:
                item = json.loads(text[span_start:span_stop])
            except Exception:
                return False
            if isinstance(item, dict):
                found.append(item)
                return len(found) == count
            return False

        while i >= 0:
            ch = text[i]
            if in_string:
                if ch == '"':
                    # 判定左侧连续反斜杠个数的奇偶：奇数 => 转义引号，属字符串内容
                    j = i - 1
                    bs = 0
                    while j >= 0 and text[j] == "\\":
                        bs += 1
                        j -= 1
                    if bs % 2 == 1:
                        i = j  # 跳过该反斜杠串，继续留在字符串内
                        continue
                    in_string = False
                i -= 1
                continue
            if ch == '"':
                in_string = True
            elif ch in "]}":
                depth += 1
            elif ch in "[{":
                if depth == 0:
                    # 回溯到数组左括号：当前为第一个元素
                    if _collect(i + 1, stop) and len(found) == count:
                        return found[::-1]
                    return None
                depth -= 1
            elif ch == "," and depth == 0:
                span_start = i + 1
                if _collect(span_start, stop):
                    return found[::-1]
                stop = i
            i -= 1
        return None

    def _history_item_timestamp(self, item: dict[str, Any]) -> float | None:
        for key in ("timestamp", "time", "created_at", "updated_at", "created", "date"):
            value = item.get(key)
            if value is None or value == "":
                continue
            numeric = _safe_float(value, 0)
            if numeric > 0:
                return numeric / 1000 if numeric > 10_000_000_000 else numeric
            text = str(value).strip()
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%m-%d %H:%M:%S", "%m-%d %H:%M"):
                try:
                    parsed = datetime.strptime(text, fmt)
                    if fmt.startswith("%m"):
                        parsed = parsed.replace(year=date.today().year)
                    return parsed.timestamp()
                except Exception:
                    continue
        return None

    def _format_history_item_for_summary(self, item: dict[str, Any]) -> str:
        role = _single_line(item.get("role") or item.get("type") or item.get("speaker"), 20).lower()
        if role in {"assistant", "bot", "ai"}:
            speaker = f"{runtime_persona_setting(self, 'bot_name', '小星')}(Bot回复)"
        elif role in {"user", "human"}:
            speaker = "用户"
        else:
            speaker = role or "对话"
        content = self._history_item_content_text(item)
        if not content:
            return ""
        if self._daily_proactive_archive_context_text(content):
            return ""
        ts = self._history_item_timestamp(item)
        time_prefix = self._environment_fromtimestamp(ts).strftime("%m-%d %H:%M") + " " if ts else ""
        return f"{time_prefix}{speaker}: {content}"

    def _history_item_content_text(self, item: dict[str, Any]) -> str:
        value = item.get("content")
        if value is None:
            value = item.get("message") or item.get("text") or item.get("content_text")
        if isinstance(value, str):
            return _single_line(value, 260)
        if isinstance(value, list):
            parts: list[str] = []
            for part in value:
                if isinstance(part, str):
                    parts.append(part)
                elif isinstance(part, dict):
                    text = part.get("text") or part.get("content") or part.get("message")
                    if text:
                        parts.append(str(text))
                    elif str(part.get("type") or "").lower() == "image":
                        parts.append("[图片]")
            return _single_line(" ".join(parts), 260)
        if isinstance(value, dict):
            return _single_line(value.get("text") or value.get("content") or json.dumps(value, ensure_ascii=False), 260)
        return ""

    async def _summarize_yesterday_conversation_for_schedule(self, raw_text: str) -> dict[str, Any]:
        today = _today_key()
        source_date = _date_key(date.today() - timedelta(days=1))
        section = prompt_section(
            key="background.yesterday_summary",
            title="昨日对话残留摘要",
            source="daily_state",
            content=f"""
请阅读下面的昨日/最近完整对话材料,为今天的日程和梦境生成提炼参考摘要。

目标不是复述聊天,而是找出可能延续到今天的“残留影响”：身体状态、饮食/作息、情绪余波、关系变化、未完成约定、收到/送出的东西、外出计划、压力来源、被安慰/被打断的事、梦境可能用到的物件/颜色/气味/半句话等。

重要原则：
1. 只根据对话内容做合理推断,不要硬套固定事件类型。
2. 如果某个行为可能带来身体或日程后果,用抽象逻辑表达：饮食、睡眠、天气、运动、情绪刺激、约定、礼物、争执、安慰等都可能改变今天的体力、胃口、心情、出门意愿或主动话题。
3. 影响可以很轻,也可以没有。不要为了制造剧情强行让今天出事。
4. 摘要要给日程模型用,所以写成可执行参考,不是聊天回复。
5. 梦境参考只提炼碎片和情绪质感,不要编完整梦。
6. 饮食偏好要有衰退：某个菜名、零食或口味反复出现时,只当“近期聊过/需要避错”的软背景,不要要求今天继续安排购买、带饭、留一份或一起吃。用户说“不吃/不喜欢/不要/避开某食物”时,只写成“相关时避开该食物”,不要写成今天必须准备替代餐食。
7. 严格区分说话人：只有“用户”行能写成用户真实信息；“Bot回复”里的我在做什么、身体/心情/日程、动作描写或生活片段，只能视为 Bot 当时的拟人化表达，不能当作用户事实、现实证据或今天必须继承的事件。
8. 只有用户明确确认、提出或约定的事，才可以进入计划/未完成约定；Bot 自称的吃饭、整理、犯困、走动、创作等状态不要转成稳定记忆或现实日程。

对话材料：
{raw_text}

只输出 JSON：
{{
  "summary": "昨日对话的一句话概括",
  "residues": [
    {{"type": "身体/情绪/关系/计划/物件/梦境碎片", "content": "可延续影响", "strength": "轻/中/强"}}
  ],
  "schedule_reference": "今天生成日程时应如何自然继承这些残留；没有就写无明确影响",
  "dream_reference": "今天梦境/梦境碎片可以参考的物件、感官、半句话或情绪；没有就写无明确碎片"
}}
""".strip(),
        )
        prompt = render_prompt_sections(
            [section],
            mode=PromptRenderMode.BODY_ONLY,
        )
        raw = await self._llm_call(
            prompt,
            max_tokens=650,
            provider_id=self._task_provider(
                runtime_persona_setting(self, "history_summary_provider_id", ""),
                runtime_persona_setting(self, "daily_plan_provider_id", ""),
                runtime_persona_setting(self, "mai_style_provider_id", ""),
            ),
            task="yesterday_summary",
        )
        payload = self._extract_json_payload(raw or "")
        if not isinstance(payload, dict):
            return {
                "date": today,
                "source_date": source_date,
                "summary": _single_line(raw_text, 180) or "昨日对话有记录,但摘要生成失败。",
                "residues": [],
                "schedule_reference": "可把昨日互动作为轻微关系和情绪背景,不要强行改写今日主线。",
                "dream_reference": "可从昨日对话里的物件、语气和半句话提取梦境碎片。",
                "scope": "owner_private_only",
                "raw_excerpt_chars": len(raw_text),
            }
        residues = payload.get("residues", [])
        if not isinstance(residues, list):
            residues = []
        normalized_residues = []
        for item in residues[:8]:
            if not isinstance(item, dict):
                continue
            content = _single_line(item.get("content"), 120)
            if not content:
                continue
            normalized_residues.append({
                "type": _single_line(item.get("type"), 24) or "残留",
                "content": content,
                "strength": _single_line(item.get("strength"), 8) or "轻",
            })
        return {
            "date": today,
            "source_date": source_date,
            "summary": _single_line(payload.get("summary"), 180) or "昨日对话有一些可延续的情绪和生活残留。",
            "residues": normalized_residues,
            "schedule_reference": _single_line(payload.get("schedule_reference"), 220) or "作为轻微背景承接,不要强行改写今日主线。",
            "dream_reference": _single_line(payload.get("dream_reference"), 220) or "从昨日对话的物件、感官和半句话中轻取梦境碎片。",
            "scope": "owner_private_only",
            "raw_excerpt_chars": len(raw_text),
        }

    def _format_yesterday_conversation_summary_for_prompt(self) -> str:
        summary = self.data.get("yesterday_conversation_summary", {})
        if not isinstance(summary, dict) or summary.get("date") != _today_key():
            return "暂无昨日完整对话摘要。"
        schedule_reference = self._decay_schedule_food_reference_text(
            summary.get("schedule_reference"),
            field="yesterday_conversation.schedule_reference",
        )
        dream_reference = self._decay_schedule_food_reference_text(
            summary.get("dream_reference"),
            field="yesterday_conversation.dream_reference",
            dream=True,
        )
        lines = [
            f"来源日期：{summary.get('source_date') or '昨日'}",
            f"概括：{_single_line(summary.get('summary'), 180)}",
            f"日程参考：{schedule_reference}",
            f"梦境参考：{dream_reference}",
        ]
        residues = summary.get("residues", [])
        if isinstance(residues, list) and residues:
            lines.append("残留变量：")
            for item in residues[:8]:
                if not isinstance(item, dict):
                    continue
                content = self._decay_schedule_food_reference_text(
                    item.get("content"),
                    field="yesterday_conversation.residue",
                )
                if content:
                    lines.append(f"- {item.get('type') or '残留'}｜{content}｜强度 {item.get('strength') or '轻'}")
        return "\n".join(lines)
