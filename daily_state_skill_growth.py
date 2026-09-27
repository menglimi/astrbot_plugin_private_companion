# -*- coding: utf-8 -*-
"""skill_growth 域。

由 tools/split_mixin_domain.py 从 daily_state.py 机械抽取（29 个方法 + 0 个模块级名字 + 0 个类级赋值 / {13188, 13189, 13190, 13191, 13205, 13211, 13192, 13193, 13194, 13195, 13146, 13196, 13197, 13198, 13199, 10535, 10536, 10537, 10538, 10539, 10540, 10541, 10542, 10543, 10544, 13200, 10546, 10547, 10548, 10549, 10550, 10551, 10552, 10554, 10555, 10556, 10558, 10559, 10560, 10561, 10562, 10563, 10564, 10565, 10566, 13201, 10568, 10569, 10570, 10571, 10572, 10573, 10574, 10575, 10576, 10577, 10578, 10579, 10580, 10581, 10582, 10583, 10584, 10585, 10586, 10587, 10588, 10589, 10590, 10591, 10592, 10593, 10594, 10595, 10596, 10597, 10598, 10599, 10600, 10601, 10602, 10603, 10604, 10605, 10606, 10607, 10608, 10609, 10610, 10611, 10612, 10613, 10614, 10615, 10616, 10617, 13203, 10619, 10620, 10621, 10622, 10623, 10624, 10625, 10626, 10628, 10629, 10630, 10631, 10632, 10633, 10634, 10635, 10636, 10637, 10638, 10639, 10640, 10641, 10642, 10643, 10644, 10645, 10646, 10647, 10648, 10649, 10650, 10651, 10652, 10653, 10654, 10655, 10656, 10657, 10658, 10659, 10660, 10661, 10662, 10663, 10664, 10665, 10666, 10667, 10668, 10669, 10670, 10671, 10672, 10673, 10674, 10675, 10676, 10677, 10678, 10679, 10680, 10681, 10682, 10683, 10684, 10685, 10686, 10687, 10688, 10689, 10690, 10691, 10692, 10693, 10694, 10695, 10696, 13206, 10698, 10699, 10700, 10701, 10702, 10703, 10704, 10705, 10706, 10708, 10709, 10710, 10711, 10712, 10713, 10714, 10715, 10716, 10717, 10718, 10719, 10720, 10721, 10722, 13207, 10724, 10725, 10726, 10727, 10728, 10729, 10730, 10731, 10732, 10733, 10734, 10735, 10736, 10737, 10738, 10739, 10740, 10741, 10742, 10743, 10744, 13208, 10746, 10747, 10748, 10749, 10750, 10751, 10752, 10753, 10754, 10755, 10756, 10757, 10758, 10759, 10760, 10761, 10762, 10763, 10764, 10765, 10766, 10767, 10768, 10769, 10770, 10771, 10772, 10773, 10774, 10775, 10776, 10777, 10778, 10779, 10780, 10781, 10782, 10783, 10784, 10785, 10786, 10787, 10788, 10789, 10790, 10791, 10792, 10793, 10794, 10795, 10796, 10797, 10798, 10799, 10800, 10801, 10802, 10803, 10804, 10805, 10806, 10807, 10808, 10809, 10810, 10811, 10812, 10813, 10814, 10815, 10816, 10817, 10818, 10819, 10820, 13140, 10822, 10823, 10824, 10825, 13141, 10827, 10828, 10829, 10830, 10831, 10832, 10833, 10834, 10835, 10836, 13127, 10838, 10839, 10840, 10841, 10842, 10843, 10844, 10845, 13144, 10847, 10848, 10849, 10850, 10851, 10852, 10853, 10854, 10855, 10856, 10857, 10858, 10859, 13131, 10861, 10862, 10863, 10864, 10865, 10866, 10867, 10868, 10869, 10870, 10871, 10872, 10873, 10874, 10875, 10876, 10877, 10878, 10879, 10880, 10881, 10882, 10883, 10884, 10885, 10886, 10887, 10888, 10889, 10890, 10891, 10892, 10893, 10894, 10895, 10896, 10897, 10898, 10899, 10900, 10901, 10902, 10903, 10904, 10905, 10906, 10907, 10908, 10909, 10910, 10911, 10912, 10913, 10914, 10915, 13159, 10917, 10918, 10919, 13160, 10921, 10922, 10923, 10924, 10925, 10926, 10927, 10928, 10929, 10930, 10931, 10932, 10933, 10934, 10935, 10936, 10937, 10938, 10939, 10940, 10941, 10942, 10943, 10944, 10945, 10946, 10947, 10948, 13165, 10950, 10951, 10952, 13166, 10954, 10955, 10956, 10957, 10958, 10959, 10960, 10961, 10962, 10963, 10964, 10965, 10966, 10967, 10968, 10969, 10970, 10971, 10972, 10973, 10974, 10975, 10976, 10977, 10978, 10979, 10980, 13136, 10982, 10983, 10984, 10985, 10986, 10987, 10988, 10989, 10990, 10991, 10992, 10993, 10994, 10995, 10996, 10997, 10998, 10999, 11000, 11001, 11002, 11003, 11004, 11005, 11006, 11007, 11008, 11009, 11010, 11011, 11012, 11013, 11014, 11015, 11016, 11017, 11018, 11019, 11020, 11021, 11022, 11023, 11024, 11025, 11026, 11027, 11028, 11029, 11030, 11031, 11032, 11033, 11034, 11035, 11036, 11037, 11038, 11039, 11040, 11041, 11042, 11043, 11044, 11045, 11046, 11047, 11048, 11049, 11050, 11051, 11052, 11053, 11054, 11055, 11056, 11057, 11058, 11059, 11060, 11061, 11062, 11063, 11064, 11065, 11066, 11067, 11068, 11069, 11070, 11071, 11072, 11073, 11074, 11075, 11076, 11077, 11078, 11079, 11080, 11081, 11082, 11083, 11084, 11085, 11086, 11087, 11088, 11089, 11090, 11091, 11092, 11093, 11094, 11095, 11096, 11097, 11098, 11099, 11100, 11101, 11102, 11103, 11104, 11105, 11106, 11107, 11108, 11109, 11110, 11111, 11112, 11113, 11114, 11115, 11116, 11117, 13158, 11119, 11120, 11121, 11122, 11123, 11124, 11125, 11126, 11127, 11128, 11129, 11130, 11131, 11132, 11133, 11134, 11135, 11136, 11137, 11138, 11139, 11140, 11141, 11142, 13130, 11144, 11145, 11146, 11147, 11148, 11149, 11150, 11151, 11152, 11153, 11154, 11155, 11156, 11157, 11158, 11159, 11160, 11161, 11162, 11163, 11164, 11165, 11166, 11167, 11168, 11169, 11170, 11171, 11172, 11173, 11174, 11175, 11176, 11177, 11178, 11179, 11180, 11181, 11182, 11183, 11184, 11185, 11186, 11187, 11188, 11189, 11190, 11191, 11192, 11193, 11194, 13214, 11196, 11197, 11198, 13215, 11200, 11201, 11202, 11203, 11204, 11205, 11206, 11207, 11208, 11209, 11210, 11211, 11212, 11213, 11214, 11215, 11216, 11217, 11218, 11219, 11220, 11221, 11222, 11223, 11224, 11225, 11226, 11227, 11228, 11229, 11230, 11231, 11232, 11233, 11234, 11235, 11236, 11237, 11238, 11239, 11240, 11241, 11242, 11243, 11244, 11245, 11246, 11247, 11248, 11249, 11250, 11251, 11252, 11253, 11254, 11255, 11256, 11257, 11258, 11259, 11260, 11261, 11262, 11263, 11264, 13148, 13149, 13213, 13150, 13151, 13152, 13132, 13153, 13154, 13128, 13155, 13156, 13133, 13161, 13162, 13134, 13163, 13164, 13210, 13167, 13135, 13168, 13212, 13169, 13170, 13145, 13171, 13173, 13174, 13175, 13176, 13202, 13177, 13142, 13137, 13178, 13179, 13129, 13180, 13209, 13181, 13182, 13138, 13183, 13184, 13185, 13147, 13186, 13204, 13187, 13139} 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateMixin）。
"""
from __future__ import annotations

import hashlib
import random
import re
from .conversation_prompt_section import PromptRenderMode, PromptSection, prompt_section, render_prompt_sections
from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _today_key
from .persona_config import runtime_persona_setting
from copy import deepcopy
from typing import Any





# ---- 宿主 patch 兼容层（由 tools/inject_host_patch_shim.py 注入）----
# PyTest 里 patch("...daily_state._today_key") 期望改动能被本模块感知。
# 原 import 会被下面的同名函数覆盖，方法体调用时实时转发到宿主模块。
def _today_key(*args, **kwargs):
    from . import daily_state as _host
    return getattr(_host, "_today_key")(*args, **kwargs)


def _now_ts(*args, **kwargs):
    from . import daily_state as _host
    return getattr(_host, "_now_ts")(*args, **kwargs)

class DailyStateSkillGrowthMixin:
    """skill_growth 域（从 DailyStateMixin 拆出）。"""


    @staticmethod
    def _skill_level_title(level: int) -> str:
        return {
            1: "一窍不通",
            2: "会一点点",
            3: "勉强能做",
            4: "基本熟练",
            5: "很熟练",
            6: "很有心得",
        }.get(max(1, min(6, int(level or 1))), "一窍不通")

    @staticmethod
    def _skill_level_from_exp(exp: float) -> int:
        level = 1
        for idx, threshold in enumerate([0, 100, 260, 520, 900, 1400], start=1):
            if exp >= threshold:
                level = idx
        return max(1, min(6, level))

    @staticmethod
    def _skill_next_exp(level: int) -> int | None:
        return {1: 100, 2: 260, 3: 520, 4: 900, 5: 1400}.get(max(1, min(6, int(level or 1))))

    def _skill_growth_persona_text(self) -> str:
        return "\n".join(part for part in (
            runtime_persona_setting(self, "bot_name", "小星"),
            self._get_default_persona_prompt(),
            runtime_persona_setting(self, "schedule_persona_prompt", ""),
            runtime_persona_setting(self, "schedule_worldview_prompt", ""),
            runtime_persona_setting(self, "worldview_adaptation_prompt", ""),
            " ".join(str(item) for item in self.data.get("can_do", []) if item),
        ) if part)

    def _skill_growth_default_catalog(self) -> list[dict[str, Any]]:
        text = self._skill_growth_persona_text()
        catalog: list[dict[str, Any]] = []

        def add(name: str, category: str, keywords: list[str]) -> None:
            if not any(item["name"] == name for item in catalog):
                catalog.append({"name": name, "category": category, "keywords": keywords})

        for raw in re.split(r"[,，、\n]+", str(runtime_persona_setting(self, "skill_growth_custom_skills", "") or "")):
            name = _single_line(raw, 24)
            if name:
                add(name, "自定义", [name])
        if any(token in text for token in ("学生", "上课", "学校", "高中", "初中", "大学", "作业", "考试")):
            subject_keywords = {
                "语文": ["语文", "作文", "阅读理解", "文言文", "课文"],
                "数学": ["数学", "算题", "公式", "函数", "几何"],
                "英语": ["英语", "单词", "语法", "听力", "阅读"],
                "物理": ["物理", "力学", "电路", "实验", "公式"],
                "化学": ["化学", "方程式", "实验", "元素", "反应"],
                "生物": ["生物", "细胞", "遗传", "实验", "背诵"],
                "历史": ["历史", "时间线", "事件", "人物", "背诵"],
                "地理": ["地理", "地图", "气候", "区域", "地形"],
            }
            for name, keywords in subject_keywords.items():
                add(name, "学科学习", [name, *keywords, "作业", "复习", "考试"])
            add("课堂整理", "学习习惯", ["课堂整理", "笔记", "错题", "课本", "复盘"])
            add("写作", "表达创作", ["写作", "作文", "小说", "日记", "语文"])
            add("绘画", "艺术兴趣", ["绘画", "画画", "涂鸦", "素描", "草稿纸"])
        elif any(token in text for token in ("异世界", "冒险", "魔法", "骑士", "精灵", "公会", "地下城")):
            add("剑术", "冒险能力", ["剑", "训练", "挥剑", "战斗", "练习"])
            add("魔法", "冒险能力", ["魔法", "咒文", "法术", "魔力", "术式"])
            add("草药学", "冒险知识", ["草药", "药水", "采集", "治疗"])
            add("野外生存", "冒险知识", ["野外", "露营", "探索", "地图", "生火"])
            add("委托交涉", "互动关系", ["交涉", "委托", "公会", "谈判", "聊天"])
        else:
            add("生活观察", "生活感知", ["观察", "记录", "日记", "生活", "想"])
            add("资料阅读", "信息整理", ["阅读", "看书", "资料", "新闻", "搜索"])
            add("文本创作", "表达创作", ["写作", "小说", "日记", "灵感", "创作"])
            add("空间整理", "生活技能", ["整理", "收拾", "计划", "课本", "房间"])
            add("聊天表达", "互动关系", ["聊天", "群聊", "私聊", "回复", "分享"])
        if any(token in text for token in ("电脑", "代码", "编程", "程序", "开发", "模型", "AI", "网页", "搜索")):
            add("电脑操作", "信息整理", ["电脑", "文件", "网页", "搜索", "整理"])
            add("代码阅读", "信息整理", ["代码", "编程", "程序", "开发", "报错"])
        if any(token in text for token in ("音乐", "唱歌", "钢琴", "吉他")):
            add("音乐", "艺术兴趣", ["音乐", "唱歌", "练琴", "旋律"])
        if any(token in text for token in ("料理", "做饭", "烹饪", "厨房")):
            add("烹饪", "生活技能", ["烹饪", "做饭", "厨房", "料理"])
        if any(token in text for token in ("漫画", "番剧", "视频", "B站", "小说", "阅读")):
            add("内容品鉴", "兴趣理解", ["漫画", "番剧", "视频", "小说", "阅读", "推荐"])
        return catalog[:24]

    def _skill_growth_stable_bonus(self, name: str, text: str) -> float:
        seed = f"{runtime_persona_setting(self, 'bot_name', '小星')}|{name}|{hashlib.sha1(text.encode('utf-8', errors='ignore')).hexdigest()[:12]}"
        bonus = float(int(hashlib.sha1(seed.encode("utf-8")).hexdigest()[:6], 16) % 60)
        if name and name in text:
            bonus += 55
        if any(token in text for token in (f"擅长{name}", f"喜欢{name}", f"{name}很好", f"{name}优秀")):
            bonus += 70
        return min(180.0, bonus)

    def _ensure_skill_growth_profile_locked(self) -> dict[str, Any]:
        state = self.data.setdefault("skill_growth", {})
        if not isinstance(state, dict):
            state = {}
            self.data["skill_growth"] = state
        skills = state.setdefault("skills", {})
        if not isinstance(skills, dict):
            skills = {}
            state["skills"] = skills
        text = self._skill_growth_persona_text()
        profile_changed = False
        for item in self._skill_growth_default_catalog():
            name = _single_line(item.get("name"), 24)
            if not name:
                continue
            skill_id = hashlib.sha1(name.encode("utf-8")).hexdigest()[:12]
            target_id = skill_id
            if not isinstance(skills.get(target_id), dict):
                for existing_id, existing in skills.items():
                    if not isinstance(existing, dict):
                        continue
                    aliases = existing.get("aliases") if isinstance(existing.get("aliases"), list) else []
                    alias_set = {_single_line(alias, 24) for alias in aliases}
                    if name in alias_set:
                        target_id = str(existing_id)
                        break
            if not isinstance(skills.get(target_id), dict):
                base_exp = self._skill_growth_stable_bonus(name, text)
                level = self._skill_level_from_exp(base_exp)
                skills[target_id] = {
                    "id": target_id,
                    "name": name,
                    "category": _single_line(item.get("category"), 20) or "能力",
                    "keywords": item.get("keywords") if isinstance(item.get("keywords"), list) else [name],
                    "aliases": [],
                    "hidden": False,
                    "frozen": False,
                    "exp": round(base_exp, 2),
                    "level": level,
                    "level_title": self._skill_level_title(level),
                    "created_ts": _now_ts(),
                    "last_trained_ts": 0,
                    "training_count": 0,
                    "recent_logs": [],
                }
                profile_changed = True
            else:
                skill = skills.get(target_id)
                if isinstance(skill, dict):
                    new_category = _single_line(item.get("category"), 20) or "能力"
                    old_category = _single_line(skill.get("category"), 20)
                    if old_category in {"", "学科", "兴趣", "生活", "冒险", "社交", "关系", "能力"} and new_category != old_category:
                        skill["category"] = new_category
                        profile_changed = True
                    old_keywords = skill.get("keywords") if isinstance(skill.get("keywords"), list) else []
                    merged_keywords: list[str] = []
                    for raw_keyword in [*old_keywords, *(item.get("keywords") if isinstance(item.get("keywords"), list) else [name])]:
                        keyword = _single_line(raw_keyword, 24)
                        if keyword and keyword not in merged_keywords:
                            merged_keywords.append(keyword)
                    if merged_keywords and merged_keywords != old_keywords:
                        skill["keywords"] = merged_keywords[:16]
                        profile_changed = True
        if profile_changed:
            state["_profile_changed"] = True
        state.setdefault("processed_schedule_keys", [])
        state.setdefault("last_settled_day", "")
        state.setdefault("updated_ts", _now_ts())
        return state

    def _skill_growth_terms(self, skill: dict[str, Any], *, include_keywords: bool = True) -> list[str]:
        keywords = skill.get("keywords") if include_keywords and isinstance(skill.get("keywords"), list) else []
        aliases = skill.get("aliases") if isinstance(skill.get("aliases"), list) else []
        terms: list[str] = []
        for raw in [_single_line(skill.get("name"), 24), *keywords, *aliases]:
            term = _single_line(raw, 24)
            if term and term not in terms:
                terms.append(term)
        return terms

    def _skill_growth_match_weight(self, skill: dict[str, Any], activity_text: str) -> float:
        if skill.get("hidden") or skill.get("frozen"):
            return 0.0
        text = str(activity_text or "")
        if not text:
            return 0.0
        matched = sum(1 for key in self._skill_growth_terms(skill) if key in text)
        if matched <= 0:
            return 0.0
        weight = 1.0 + min(2.0, matched * 0.35)
        if any(token in text for token in ("练", "训练", "复习", "预习", "作业", "创作", "写", "阅读", "搜索", "学习")):
            weight += 0.45
        if any(token in text for token in ("休息", "睡", "发呆", "刷手机")) and matched == 1:
            weight *= 0.55
        return max(0.0, weight)

    @staticmethod
    def _skill_growth_user_text_token_false_positive(token: str, query: str) -> bool:
        if token == "历史":
            false_contexts = (
                "历史记录",
                "聊天历史",
                "会话历史",
                "浏览历史",
                "历史消息",
                "历史归档",
                "历史失败",
                "历史调用",
                "历史注入",
                "历史缓存",
                "历史版本",
            )
            if any(item in query for item in false_contexts):
                return True
            if re.search(r"历史\s*(记录|消息|会话|聊天|浏览|归档|失败|调用|注入|缓存|版本|数据|日志|摘要)", query):
                return True
        return False

    async def _maybe_settle_skill_growth(self, *, force: bool = False) -> None:
        if not runtime_persona_setting(self, "enable_skill_growth_simulation", True):
            return
        now_ts = _now_ts()
        async with self._data_lock:
            state = self._ensure_skill_growth_profile_locked()
            profile_changed = bool(state.pop("_profile_changed", False))
            if not force and now_ts - _safe_float(state.get("last_check_ts"), 0) < 20 * 60:
                if profile_changed:
                    state["updated_ts"] = now_ts
                    self._save_data_sync(sections={"skill_growth"})
                return
            state["last_check_ts"] = now_ts
            plan = self.data.get("daily_plan", {})
            if not isinstance(plan, dict):
                if profile_changed:
                    state["updated_ts"] = now_ts
                    self._save_data_sync(sections={"skill_growth"})
                return
            items = plan.get("items") if isinstance(plan.get("items"), list) else plan.get("schedule")
            if not isinstance(items, list):
                if profile_changed:
                    state["updated_ts"] = now_ts
                    self._save_data_sync(sections={"skill_growth"})
                return
            day_key = _single_line(plan.get("date"), 20) or _today_key()
            processed = state.get("processed_schedule_keys") if isinstance(state.get("processed_schedule_keys"), list) else []
            if state.get("last_settled_day") != day_key:
                processed = [key for key in processed if str(key).startswith(day_key + "|")]
                state["processed_schedule_keys"] = processed
                state["last_settled_day"] = day_key
            now_minutes = self._environment_now_minutes()
            skills = state.get("skills") if isinstance(state.get("skills"), dict) else {}
            changed = profile_changed
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                time_text = _single_line(item.get("time"), 12)
                minutes = self._parse_hhmm_to_minutes(time_text)
                if minutes is None or minutes > now_minutes:
                    continue
                key = f"{day_key}|{index}|{time_text}"
                if key in processed:
                    continue
                activity_text = " ".join(_single_line(item.get(field), 120) for field in ("activity", "title", "summary", "message_seed", "mood") if _single_line(item.get(field), 120))
                for skill in skills.values():
                    if not isinstance(skill, dict):
                        continue
                    if skill.get("hidden") or skill.get("frozen"):
                        continue
                    weight = self._skill_growth_match_weight(skill, activity_text)
                    if weight <= 0:
                        continue
                    old_level = _safe_int(skill.get("level"), 1, 1)
                    gained = round(max(0.25, weight * 4.0 * float(runtime_persona_setting(self, "skill_growth_rate", 1.0) or 1.0)), 2)
                    skill["exp"] = round(_safe_float(skill.get("exp"), 0) + gained, 2)
                    new_level = self._skill_level_from_exp(_safe_float(skill.get("exp"), 0))
                    skill["level"] = new_level
                    skill["level_title"] = self._skill_level_title(new_level)
                    skill["last_trained_ts"] = now_ts
                    skill["training_count"] = _safe_int(skill.get("training_count"), 0, 0) + 1
                    logs = skill.setdefault("recent_logs", [])
                    if not isinstance(logs, list):
                        logs = []
                        skill["recent_logs"] = logs
                    logs.append({"ts": now_ts, "source": "schedule", "activity": _single_line(activity_text, 80), "exp": gained, "level_up": new_level > old_level})
                    del logs[:-8]
                    changed = True
                processed.append(key)
                changed = True
            if len(processed) > 120:
                del processed[:-120]
            if changed:
                state["updated_ts"] = now_ts
                self._save_data_sync(sections={"skill_growth"})

    @staticmethod
    def _personal_goal_status(value: Any) -> str:
        normalized = _single_line(value, 20).lower()
        return normalized if normalized in {"active", "paused", "completed", "abandoned"} else "active"

    def _personal_goal_terms(self, goal: dict[str, Any]) -> list[str]:
        raw_terms = goal.get("keywords") if isinstance(goal.get("keywords"), list) else []
        terms: list[str] = []
        # Category is display metadata, not evidence. Using broad labels such as
        # "阅读" here would advance every goal in that category from one activity.
        for raw in [goal.get("title"), goal.get("next_step"), *raw_terms]:
            term = _single_line(raw, 32)
            if term and term not in terms:
                terms.append(term)
        return terms[:16]

    def _personal_goal_matches_activity(self, goal: dict[str, Any], activity_text: str) -> bool:
        text = _single_line(activity_text, 500)
        if not text:
            return False
        terms = self._personal_goal_terms(goal)
        if not terms:
            return False
        return any(len(term) >= 2 and term in text for term in terms)

    def _personal_goal_owner_users(self) -> list[tuple[str, dict[str, Any]]]:
        users = self.data.get("users") if isinstance(self.data.get("users"), dict) else {}
        targets: list[tuple[str, dict[str, Any]]] = []
        for raw_user_id, user in users.items():
            user_id = str(raw_user_id or "").strip()
            if not user_id or not isinstance(user, dict) or not user.get("umo"):
                continue
            if self._private_user_role(user, user_id) != "owner":
                continue
            if not self._user_enabled_for_proactive(user_id, user):
                continue
            targets.append((user_id, user))
        return targets

    def _queue_personal_goal_candidate_locked(
        self,
        goal: dict[str, Any],
        event: dict[str, Any],
        *,
        now: float,
    ) -> int:
        title = _single_line(goal.get("title"), 60)
        if not title or not isinstance(event, dict):
            return 0
        event_kind = _single_line(event.get("kind"), 24) or "progress"
        progress = _safe_int(goal.get("progress"), 0, 0, 100)
        if event_kind == "completed":
            topic = f"{title}终于完成了"
            motive = "自己持续推进的目标终于完成，想自然分享这次真实结果"
            score = 90
        elif event_kind == "stalled":
            topic = f"{title}最近一直没顾上"
            motive = "意识到自己的长期目标停了一阵，想低压力提一句，不把责任推给用户"
            score = 70
        else:
            topic = f"{title}推进到 {progress}%"
            motive = "自己持续推进的目标跨过了一个明确里程碑，想简短分享进展"
            score = 80
        offered = 0
        context = {
            "goal_id": _single_line(goal.get("id"), 40),
            "title": title,
            "category": _single_line(goal.get("category"), 24),
            "status": self._personal_goal_status(goal.get("status")),
            "progress": progress,
            "next_step": _single_line(goal.get("next_step"), 100),
            "note": _single_line(goal.get("note"), 140),
            "event": deepcopy(event),
        }
        for user_id, user in self._personal_goal_owner_users():
            scheduled = now + random.uniform(5, 20) * 60
            candidate = {
                "source": "personal_goal",
                "reason": "personal_goal_progress",
                "action": "message",
                "scheduled_ts": scheduled,
                "window_start_at": scheduled,
                "preferred_ts": scheduled,
                "best_until_at": scheduled + 2 * 3600,
                "expire_at": scheduled + 6 * 3600,
                "topic": topic,
                "motive": motive,
                "score": score,
                "context_key": "planned_personal_goal_context",
                "context": deepcopy(context),
            }
            if self._offer_proactive_candidate(user_id, user, candidate):
                offered += 1
        return offered

    def _format_personal_goal_prompt(self, user: dict[str, Any], *, reason: str = "") -> str:
        section = self._format_personal_goal_prompt_section(user, reason=reason)
        return render_prompt_sections([section], mode=PromptRenderMode.LABELED_BLOCK)

    def _format_personal_goal_prompt_section(
        self,
        user: dict[str, Any],
        *,
        reason: str = "",
    ) -> PromptSection:
        def build_section(content: str = "") -> PromptSection:
            return prompt_section(
                key="personal_goal.progress",
                title="非创作型个人目标",
                source="daily_state",
                content=content,
            )

        if reason != "personal_goal_progress" or not isinstance(user, dict):
            return build_section()
        context = user.get("planned_personal_goal_context")
        if not isinstance(context, dict):
            return build_section()
        event = context.get("event") if isinstance(context.get("event"), dict) else {}
        body = (
            f"- 目标：{_single_line(context.get('title'), 60)}\n"
            f"- 当前进度：{_safe_int(context.get('progress'), 0, 0, 100)}%\n"
            f"- 本次变化：{_single_line(event.get('kind'), 24)}；证据：{_single_line(event.get('evidence'), 120) or '目标状态记录'}\n"
            f"- 下一步：{_single_line(context.get('next_step'), 100) or '尚未指定'}\n"
            "只表达这次真实进展、停滞或完成，不虚构做过的步骤，不写后台进度字段，不把目标变成向用户索取监督的任务。"
        )
        return build_section(body)

    def _format_personal_goals_schedule_context(self, limit: int = 5) -> str:
        section = self._format_personal_goals_schedule_context_prompt_section(limit=limit)
        return render_prompt_sections([section], mode=PromptRenderMode.LABELED_BLOCK)

    def _format_personal_goals_schedule_context_prompt_section(
        self,
        limit: int = 5,
    ) -> PromptSection:
        def build_section(content: str = "") -> PromptSection:
            return prompt_section(
                key="personal_goal.schedule_context",
                title="Bot 自己的非创作型个人目标",
                source="daily_state",
                content=content,
            )

        if not bool(runtime_persona_setting(self, "enable_personal_goals", True)):
            return build_section()
        goals = self.data.get("personal_goals") if isinstance(self.data.get("personal_goals"), list) else []
        active = [goal for goal in goals if isinstance(goal, dict) and self._personal_goal_status(goal.get("status")) == "active"]
        if not active:
            return build_section()
        lines = [
            "这些目标已经明确建立，可以在身份主线、状态和当天硬安排允许时留出少量真实推进时间；不要每天全部安排，也不要伪造已经完成。",
        ]
        for goal in active[: max(1, int(limit or 1))]:
            lines.append(
                f"- {_single_line(goal.get('title'), 60)}｜进度 {_safe_int(goal.get('progress'), 0, 0, 100)}%｜"
                f"下一步：{_single_line(goal.get('next_step'), 100) or '自然推进'}｜匹配词：{'、'.join(self._personal_goal_terms(goal)[:6])}"
            )
        return build_section("\n".join(lines))

    async def _maybe_settle_personal_goals(self, *, force: bool = False) -> None:
        if not bool(runtime_persona_setting(self, "enable_personal_goals", True)):
            return
        now = _now_ts()
        async with self._data_lock:
            goals = self.data.get("personal_goals")
            if not isinstance(goals, list) or not goals:
                return
            state = self.data.setdefault("personal_goal_state", {})
            if not isinstance(state, dict):
                state = {}
                self.data["personal_goal_state"] = state
            if not force and now - _safe_float(state.get("last_check_at"), 0) < 20 * 60:
                return
            state["last_check_at"] = now
            plan = self.data.get("daily_plan", {})
            items = plan.get("items") if isinstance(plan, dict) and isinstance(plan.get("items"), list) else []
            day_key = _single_line(plan.get("date"), 20) if isinstance(plan, dict) else ""
            day_key = day_key or _today_key()
            processed = state.get("processed_schedule_keys") if isinstance(state.get("processed_schedule_keys"), list) else []
            if state.get("processed_day") != day_key:
                processed = []
                state["processed_schedule_keys"] = processed
                state["processed_day"] = day_key
            now_minutes = self._effective_plan_now_minutes(day_key)
            starts = self._normalized_plan_item_starts(items)
            auto_progress = bool(runtime_persona_setting(self, "enable_personal_goal_auto_progress", True))
            changed = False
            if auto_progress:
                for index, item in enumerate(items):
                    if not isinstance(item, dict):
                        continue
                    time_text = _single_line(item.get("time"), 8)
                    start = starts[index] if index < len(starts) else None
                    next_start = next((value for value in starts[index + 1 :] if value is not None), None)
                    end = self._plan_item_end_minutes(int(start), item, next_start=next_start) if start is not None else None
                    runtime_status = self._plan_item_runtime_status(plan, item, index)
                    # Personal-goal auto progress is based on a completed
                    # self-authored schedule window. Canonical agenda status
                    # intentionally stays conservative when no execution
                    # evidence exists, so use the clock window as the local
                    # completion signal only for this internal settlement.
                    if runtime_status != "completed" and start is not None and end is not None:
                        if self._normalize_schedule_lifecycle_status(item.get("lifecycle_status")) != "cancelled":
                            runtime_status = self._schedule_window_runtime_status(
                                int(start),
                                int(end),
                                plan_date=day_key,
                                explicit_status=item.get("lifecycle_status"),
                            )
                    if runtime_status != "completed":
                        continue
                    if start is None or end is None or now_minutes is None or end > now_minutes:
                        continue
                    activity = " ".join(
                        _single_line(item.get(field), 120)
                        for field in ("activity", "message_seed", "mood")
                        if _single_line(item.get(field), 120)
                    )
                    signature = hashlib.sha1(
                        f"{time_text}|{_single_line(item.get('end'), 8)}|{activity}".encode("utf-8")
                    ).hexdigest()[:12]
                    key = f"{day_key}|{index}|{signature}"
                    if key in processed:
                        continue
                    for goal in goals:
                        if not isinstance(goal, dict) or self._personal_goal_status(goal.get("status")) != "active":
                            continue
                        if not self._personal_goal_matches_activity(goal, activity):
                            continue
                        old_progress = _safe_int(goal.get("progress"), 0, 0, 100)
                        step = _safe_int(goal.get("auto_step"), 10, 1, 50)
                        new_progress = min(100, old_progress + step)
                        if new_progress <= old_progress:
                            continue
                        old_bucket = old_progress // 25
                        new_bucket = new_progress // 25
                        goal["progress"] = new_progress
                        goal["last_progress_at"] = now
                        goal["updated_at"] = now
                        goal["stalled_notified_at"] = 0
                        logs = goal.setdefault("recent_logs", [])
                        if not isinstance(logs, list):
                            logs = []
                            goal["recent_logs"] = logs
                        logs.append({"ts": now, "kind": "progress", "progress": new_progress, "evidence": _single_line(activity, 120)})
                        del logs[:-12]
                        if new_progress >= 100:
                            goal["status"] = "completed"
                            goal["completed_at"] = now
                            goal["pending_share_event"] = {"kind": "completed", "evidence": _single_line(activity, 120)}
                        elif new_bucket > old_bucket:
                            goal["pending_share_event"] = {"kind": "progress", "milestone": new_bucket * 25, "evidence": _single_line(activity, 120)}
                        changed = True
                    processed.append(key)
                    changed = True
            stall_seconds = max(1, _safe_int(runtime_persona_setting(self, "personal_goal_stall_days", 3), 3, 1, 30)) * 86400
            for goal in goals:
                if not isinstance(goal, dict) or self._personal_goal_status(goal.get("status")) != "active":
                    continue
                last_progress = _safe_float(goal.get("last_progress_at"), 0) or _safe_float(goal.get("created_at"), 0)
                if last_progress <= 0 or now - last_progress < stall_seconds or _safe_float(goal.get("stalled_notified_at"), 0) > 0:
                    continue
                if not isinstance(goal.get("pending_share_event"), dict):
                    goal["pending_share_event"] = {"kind": "stalled", "evidence": f"已连续 {max(1, int((now - last_progress) / 86400))} 天没有匹配到真实推进"}
                    changed = True
            del processed[:-160]
            cooldown_hours = min(168.0, max(1.0, _safe_float(runtime_persona_setting(self, "personal_goal_share_cooldown_hours", 12.0), 12.0)))
            cooldown = cooldown_hours * 3600
            pending_events = [
                (goal, goal.get("pending_share_event"))
                for goal in goals
                if isinstance(goal, dict)
                and self._personal_goal_status(goal.get("status")) in {"active", "completed"}
                and isinstance(goal.get("pending_share_event"), dict)
            ]
            for goal, event in pending_events:
                if event.get("kind") != "completed" and now - _safe_float(goal.get("last_shared_at"), 0) < cooldown:
                    continue
                if self._queue_personal_goal_candidate_locked(goal, event, now=now) > 0:
                    goal["last_shared_at"] = now
                    goal["last_shared_event"] = _single_line(event.get("kind"), 24)
                    if event.get("kind") == "stalled":
                        goal["stalled_notified_at"] = now
                    goal.pop("pending_share_event", None)
                    changed = True
            if changed:
                state["updated_at"] = now
                self._save_data_sync(
                    sections={
                        "personal_goal_state",
                        "personal_goals",
                        "users",
                        "proactive_candidate_pool",
                    }
                )

    def _format_skill_growth_prompt_section(self, limit: int = 8) -> PromptSection:
        def build_section(content: str = "") -> PromptSection:
            return prompt_section(
                key="skill.growth",
                title="能力熟悉度",
                source="daily_state",
                content=content,
            )

        if not runtime_persona_setting(self, "enable_skill_growth_simulation", True):
            return build_section()
        state = self.data.get("skill_growth") if isinstance(self.data.get("skill_growth"), dict) else {}
        skills = state.get("skills") if isinstance(state.get("skills"), dict) else {}
        if not skills:
            return build_section()
        ranked = sorted([item for item in skills.values() if isinstance(item, dict) and not item.get("hidden")], key=lambda item: (_safe_int(item.get("level"), 1, 1), _safe_float(item.get("exp"), 0)), reverse=True)[:limit]
        lines: list[str] = []
        for skill in ranked:
            level = _safe_int(skill.get("level"), 1, 1)
            name = _single_line(skill.get("name"), 24)
            if name:
                lines.append(f"- {name}水平：{self._skill_level_title(level)}")
        body = "\n".join(lines)
        return build_section(body)

    def _format_skill_growth_for_user_text_prompt_section(
        self,
        text: str,
        limit: int = 3,
    ) -> PromptSection:
        def build_section(content: str = "") -> PromptSection:
            return prompt_section(
                key="skill.growth_match",
                title="本轮相关技能",
                source="daily_state",
                content=content,
            )

        if not runtime_persona_setting(self, "enable_skill_growth_simulation", True):
            return build_section()
        query = _single_line(text, 500)
        if not query:
            return build_section()
        state = self.data.get("skill_growth") if isinstance(self.data.get("skill_growth"), dict) else {}
        skills = state.get("skills") if isinstance(state.get("skills"), dict) else {}
        matched: list[tuple[int, float, dict[str, Any]]] = []
        for skill in skills.values():
            if not isinstance(skill, dict):
                continue
            if skill.get("hidden"):
                continue
            name = _single_line(skill.get("name"), 24)
            tokens = self._skill_growth_terms(skill, include_keywords=False)
            if not tokens:
                continue
            score = sum(
                1
                for token in dict.fromkeys(tokens)
                if token
                and token in query
                and not self._skill_growth_user_text_token_false_positive(token, query)
            )
            if score <= 0:
                continue
            matched.append((score, _safe_float(skill.get("exp"), 0), skill))
        if not matched:
            return build_section()
        matched.sort(key=lambda item: (item[0], _safe_int(item[2].get("level"), 1, 1), item[1]), reverse=True)
        lines: list[str] = []
        for _, _, skill in matched[: max(1, int(limit or 1))]:
            level = _safe_int(skill.get("level"), 1, 1)
            name = _single_line(skill.get("name"), 24)
            if name:
                lines.append(f"- {name}水平：{self._skill_level_title(level)}")
        body = "\n".join(lines)
        return build_section(body)

    def _format_skill_growth_schedule_context(self, limit: int = 8) -> str:
        section = self._format_skill_growth_schedule_context_prompt_section(limit=limit)
        return render_prompt_sections([section], mode=PromptRenderMode.LABELED_BLOCK)

    def _format_skill_growth_schedule_context_prompt_section(
        self,
        limit: int = 8,
    ) -> PromptSection:
        def build_section(content: str = "") -> PromptSection:
            return prompt_section(
                key="skill.schedule_influence",
                title="技能成长对日程的能力边界影响",
                source="daily_state",
                content=content,
            )

        if not runtime_persona_setting(self, "enable_skill_growth_simulation", True) or not runtime_persona_setting(self, "enable_skill_growth_schedule_influence", True):
            return build_section()
        strength = max(0.0, min(1.0, _safe_float(runtime_persona_setting(self, "skill_growth_schedule_influence_strength", 0.35), 0.35)))
        if strength <= 0:
            return build_section()
        state = self.data.get("skill_growth") if isinstance(self.data.get("skill_growth"), dict) else {}
        skills = state.get("skills") if isinstance(state.get("skills"), dict) else {}
        if not skills:
            return build_section()
        now_ts = _now_ts()
        ranked: list[tuple[float, dict[str, Any]]] = []
        for raw in skills.values():
            if not isinstance(raw, dict):
                continue
            if raw.get("hidden"):
                continue
            level = _safe_int(raw.get("level"), 1, 1)
            exp = _safe_float(raw.get("exp"), 0)
            training_count = _safe_int(raw.get("training_count"), 0, 0)
            last_trained = _safe_float(raw.get("last_trained_ts"), 0)
            recency = 0.0
            if last_trained > 0:
                age_days = max(0.0, (now_ts - last_trained) / 86400)
                recency = max(0.0, 3.0 - age_days)
            score = level * 20 + exp / 20 + min(12, training_count) + recency * 4
            ranked.append((score, raw))
        ranked.sort(key=lambda item: item[0], reverse=True)
        if not ranked:
            return build_section()
        strength_text = "很轻" if strength < 0.25 else "轻" if strength < 0.55 else "中等" if strength < 0.8 else "较强"
        lines = [
            f"影响强度：{strength_text}。这些技能主要用于保持能力边界一致,优先级低于日期语境、身份主线、状态、天气和用户介入；不要把今天写成训练清单。",
            "安排方式：能力状态会改变 Bot/角色面对相关任务的表现。这里的任务可以是题目、创作、料理、训练、战斗、交涉、研究、手工或任何符合人格的活动。基本熟练以后不要再写 Bot/角色被常规任务难住、完全不会或长期卡死；很熟练/很有心得时,面对普通任务应表现为自然、快速、能检查/讲清楚或优化做法。只有高阶、陌生、超纲、状态极差或复杂综合场景,才可以短暂停顿。",
            "低等级技能仍可以被基础任务卡住；中等级技能可以偶尔卡在细节上,但应能通过复习、查资料、请教、试错或换思路推进。",
        ]
        for _, skill in ranked[:limit]:
            level = _safe_int(skill.get("level"), 1, 1)
            name = _single_line(skill.get("name"), 24)
            category = _single_line(skill.get("category"), 18) or "能力"
            count = _safe_int(skill.get("training_count"), 0, 0)
            last = self._format_timestamp_elapsed(skill.get("last_trained_ts", 0))
            if level >= 5:
                tendency = "普通相关任务不应再被写成难住或不会；可体现效率、判断、优化做法或教别人。只有高阶/陌生/超纲/复杂场景才短暂停顿。"
            elif level >= 4:
                tendency = "常规相关任务不应卡死,最多是检查细节、换思路、试错后推进,或被进阶内容短暂拖住。"
            elif level >= 3:
                tendency = "常规相关任务能独立推进,但效率一般；可以卡在细节上,再通过复习、查资料或换思路解决。"
            elif count > 0:
                tendency = "可以被基础任务难住或需要指导,适合偶尔补一点基础练习或入门尝试,体现仍在慢慢学。"
            else:
                tendency = "只在当天身份和场景很合适时轻轻出现,不要强行安排。"
            lines.append(f"- {name}（{category}, {self._skill_level_title(level)}, 训练{count}次, 最近{last}）：{tendency}")
        return build_section("\n".join(lines))

    def _skill_levels_for_plan_bounds(self) -> dict[str, int]:
        if not runtime_persona_setting(self, "enable_skill_growth_simulation", True) or not runtime_persona_setting(self, "enable_skill_growth_schedule_influence", True):
            return {}
        state = self.data.get("skill_growth") if isinstance(self.data.get("skill_growth"), dict) else {}
        skills = state.get("skills") if isinstance(state.get("skills"), dict) else {}
        levels = {}
        for raw in skills.values():
            if not isinstance(raw, dict):
                continue
            if raw.get("hidden"):
                continue
            level = _safe_int(raw.get("level"), 1, 1)
            for name in self._skill_growth_terms(raw, include_keywords=False):
                if name:
                    levels[name] = max(1, min(6, level))
        return dict(list(levels.items())[:18])

    @staticmethod
    def _skill_task_noun_for_text(text: str) -> str:
        if any(token in text for token in ("题", "作业", "试卷", "考试", "验算", "算")):
            return "题目"
        if any(token in text for token in ("写", "小说", "文章", "日记", "作文", "创作", "草稿")):
            return "创作"
        if any(token in text for token in ("画", "绘", "素描", "线稿", "上色")):
            return "练习"
        if any(token in text for token in ("做饭", "料理", "烹饪", "菜", "厨房")):
            return "料理"
        if any(token in text for token in ("战斗", "训练", "剑", "魔法", "探索", "委托")):
            return "训练"
        return "任务"

    @staticmethod
    def _skill_bound_replacement(name: str, level: int, *, advanced: bool = False, task_noun: str = "任务") -> str:
        hard = f"{'高阶' if advanced else '常规'}{task_noun}"
        if level <= 1:
            return f"被{name}的基础部分绊住,需要从头摸一遍"
        if level == 2:
            return f"在{name}基础{task_noun}上慢慢摸索,照着例子才推进下去"
        if level == 3:
            return f"{name}常规{task_noun}能自己推进,只是效率不高,中途查了两处"
        if level == 4:
            return f"在{name}{hard}上停了一会儿,换个思路后理顺了"
        if level == 5:
            return f"把{name}{hard}顺手理清,还顺便检查了一遍更稳的做法"
        return f"把{name}{hard}拆开重组了一遍,顺手想出一个更漂亮的做法"

    def _align_plan_text_with_skill_bounds(self, text: str) -> str:
        normalized = _single_line(text, 160)
        if not normalized:
            return ""
        levels = self._skill_levels_for_plan_bounds()
        if not levels:
            return normalized
        difficulty_tokens = (
            "难住",
            "卡住",
            "卡死",
            "不会做",
            "做不出来",
            "完全不会",
            "看不懂",
            "想不出来",
            "算不出来",
            "写不出来",
        )
        if not any(token in normalized for token in difficulty_tokens):
            return normalized
        advanced_tokens = ("竞赛", "压轴", "高阶", "陌生", "超纲", "很偏", "少见", "难题", "综合题", "复杂")
        advanced = any(token in normalized for token in advanced_tokens)
        task_noun = self._skill_task_noun_for_text(normalized)
        for name, level in levels.items():
            if name not in normalized:
                continue
            replacement = self._skill_bound_replacement(name, level, advanced=advanced, task_noun=task_noun)
            replacements = [
                f"被{name}题难住",
                f"被{name}难住",
                f"被{name}卡住",
                f"{name}题卡住",
                f"{name}不会做",
                f"{name}做不出来",
                f"{name}看不懂",
                f"{name}算不出来",
                f"{name}写不出来",
                f"{name}完全不会",
            ]
            for old in replacements:
                normalized = normalized.replace(old, replacement)
        return _single_line(normalized, 160)
