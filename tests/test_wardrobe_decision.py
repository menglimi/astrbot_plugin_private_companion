# -*- coding: utf-8 -*-
"""决策层测试：priority / weight 双维打分、三趟式装箱，以及与渲染/选择路径的接线。

这一层是纯函数，所以测试也全部是确定性的：没有随机种子、没有时间、没有模型；
"同一天同一件衣服"这类稳定性由既有测试保证，这里只钉死打分语义与装箱边界。
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from astrbot_plugin_private_companion.wardrobe import (
    WARDROBE_MAX_ITEMS,
    WARDROBE_SLOT_LABELS,
    _wardrobe_slot_quotas,
    normalize_wardrobe_items,
    render_wardrobe_block,
    select_wardrobe_outfit,
)
from astrbot_plugin_private_companion.wardrobe_decision import (
    DROP_REASON_BUDGET,
    DROP_REASON_LIMIT,
    DROP_REASON_OVERSIZE,
    FAIRNESS_SCALE,
    PRIORITY_CLASSIFIED,
    PRIORITY_DESCRIBED,
    PRIORITY_FRESH,
    PRIORITY_INTIMATE,
    WEIGHT_STEP,
    clean_length,
    clean_score,
    entry_priority,
    entry_weight,
    fair_priority,
    order_by_priority,
    order_by_weight,
    pack_entries,
    score_priority,
    weight_for_rank,
)

NOTICE_PATTERN = re.compile("另有 ([0-9]+) 件未列出")
ROOT = Path(__file__).resolve().parents[1]


def _slot_counts(block: str) -> dict[str, int]:
    """数出块里每个部位各列了几件（标题行切换当前部位，条目行计数）。"""

    counts: dict[str, int] = {}
    current = ""
    for line in block.split("\n"):
        if line.startswith("── ") and line.endswith(" ──"):
            current = line[3:-3].strip()
            counts.setdefault(current, 0)
        elif line.startswith("- ") and current:
            counts[current] += 1
    return counts


def _preset_rows() -> list[dict]:
    """开箱预设衣柜的原始条目（与 test_wardrobe.py 的 load_schema() 同一份数据）。"""

    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    return schema["wardrobe_config"]["items"]["wardrobe_items"]["default"]


def _preset_items() -> list[dict]:
    return normalize_wardrobe_items(_preset_rows())


def _forty_items() -> list[dict]:
    """把 19 件预设复制到条目上限：各部位 4 / 14 / 12 / 6 / 4，件数刻意不等。"""

    rows = _preset_rows()
    doubled: list[dict] = [dict(row) for row in rows]
    for row in rows:
        # 预设条目带显式 id：复制件必须改名并去掉 id，否则会被归一化当成重复项丢掉。
        copy = dict(row)
        copy.pop("id", None)
        copy["name"] = f"{row['name']}·二"
        doubled.append(copy)
    doubled.append({"name": "补充上衣甲", "description": "额外的一件上装", "slot": "upper"})
    doubled.append({"name": "补充上衣乙", "description": "另一件上装", "slot": "upper"})
    return normalize_wardrobe_items(doubled)


def _entry(key, *, priority=0, weight=0, length=1, group=""):
    return {
        "key": key,
        "priority": priority,
        "weight": weight,
        "length": length,
        "group": group,
    }


def _pack(entries, *, budget, max_entries=0, group_cost=None):
    return pack_entries(
        entries,
        budget=budget,
        measure=lambda entry: entry.get("length", 0),
        group_of=(lambda entry: entry["group"]) if group_cost is not None else None,
        group_cost=group_cost,
        max_entries=max_entries,
    )


def _keys(entries):
    return [entry["key"] for entry in entries]


class ScoreTests(unittest.TestCase):
    """priority 的四个维度必须互不越级，否则"先丢谁"就不可预测。"""

    def test_classification_dominates_every_other_dimension(self) -> None:
        bare_but_classified = score_priority(
            classified=True, described=False, fresh=False, intimate=False
        )
        loaded_but_unclassified = score_priority(
            classified=False, described=True, fresh=True, intimate=True
        )
        self.assertEqual(PRIORITY_CLASSIFIED, bare_but_classified)
        self.assertLess(loaded_but_unclassified, bare_but_classified)

    def test_description_dominates_cooldown_and_intimate(self) -> None:
        bare_but_described = score_priority(
            classified=True, described=True, fresh=False, intimate=False
        )
        loaded_but_bare = score_priority(
            classified=True, described=False, fresh=True, intimate=True
        )
        self.assertLess(loaded_but_bare, bare_but_described)

    def test_freshness_dominates_the_intimate_bonus(self) -> None:
        fresh = score_priority(described=False, fresh=True, intimate=False)
        intimate = score_priority(described=False, fresh=False, intimate=True)
        self.assertLess(intimate, fresh)
        self.assertEqual(PRIORITY_INTIMATE, intimate - PRIORITY_CLASSIFIED)

    def test_dimension_gaps_stay_wider_than_the_lower_dimensions(self) -> None:
        # 这条断言是"字典序"的数学前提：改动任何一个常量而破坏它，这里立刻失败。
        self.assertGreater(PRIORITY_CLASSIFIED, PRIORITY_DESCRIBED + PRIORITY_FRESH + PRIORITY_INTIMATE)
        self.assertGreater(PRIORITY_DESCRIBED, PRIORITY_FRESH + PRIORITY_INTIMATE)

    def test_explicit_score_wins_and_ignores_the_facts(self) -> None:
        self.assertEqual(7, score_priority(classified=False, described=False, explicit=7))
        self.assertEqual(-3, score_priority(classified=True, described=True, explicit=-3))

    def test_garbage_inputs_fall_back_instead_of_raising(self) -> None:
        for value in (None, "不是数字", object(), float("nan"), float("inf"), float("-inf")):
            self.assertEqual(0, clean_score(value), repr(value))
        self.assertEqual(7, clean_score("7"))
        self.assertEqual(-2, clean_score(-2.4))
        self.assertEqual(5, clean_score(None, default=5))

    def test_clean_length_never_goes_negative(self) -> None:
        self.assertEqual(0, clean_length(-100))
        self.assertEqual(12, clean_length("12"))
        self.assertEqual(0, clean_length(None))

    def test_weight_for_rank_is_monotonic_and_spaced(self) -> None:
        ranks = [weight_for_rank(index) for index in range(5)]
        self.assertEqual(sorted(ranks), ranks)
        self.assertEqual(len(set(ranks)), len(ranks))
        self.assertEqual(WEIGHT_STEP, ranks[1] - ranks[0])
        self.assertEqual(99, weight_for_rank(0, explicit=99))
        # 非法间距退回默认档距，而不是让所有档位挤成同一个数。
        self.assertEqual(
            3, len({weight_for_rank(index, step=0) for index in range(3)})
        )

    def test_entry_accessors_read_mappings_and_objects(self) -> None:
        self.assertEqual(5, entry_priority({"priority": 5}))
        self.assertEqual(6, entry_weight({"weight": 6}))
        self.assertEqual(0, entry_priority({"name": "没有分数字段"}))
        self.assertEqual(0, entry_weight(object()))

        class _Row:
            priority = 3
            weight = 4

        self.assertEqual(3, entry_priority(_Row()))
        self.assertEqual(4, entry_weight(_Row()))


class OrderTests(unittest.TestCase):
    def test_priority_order_is_descending_and_stable(self) -> None:
        rows = [
            _entry("a", priority=1),
            _entry("b", priority=3),
            _entry("c", priority=3),
            _entry("d", priority=2),
        ]
        self.assertEqual(["b", "c", "d", "a"], _keys(order_by_priority(rows)))

    def test_weight_order_is_ascending_and_stable(self) -> None:
        rows = [
            _entry("a", weight=30),
            _entry("b", weight=10),
            _entry("c", weight=10),
            _entry("d", weight=20),
        ]
        self.assertEqual(["b", "c", "d", "a"], _keys(order_by_weight(rows)))

    def test_accessors_override_the_entry_fields(self) -> None:
        rows = [_entry("a", priority=0), _entry("b", priority=0)]
        ordered = order_by_priority(rows, priority_of=lambda row: {"a": 1, "b": 2}[row["key"]])
        self.assertEqual(["b", "a"], _keys(ordered))
        ordered = order_by_weight(rows, weight_of=lambda row: {"a": 2, "b": 1}[row["key"]])
        self.assertEqual(["b", "a"], _keys(ordered))


class FairPriorityTests(unittest.TestCase):
    """公平轮：tier 仍然绝对优先，同 tier 才按"本部位第几件"轮转。

    这是"预算收紧时整段部位被饿死"的修复：同 tier 内所有部位的"第 0 件"排在
    所有"第 1 件"前面，贪心装箱于是退化成轮转，预算被均匀铺到各部位。
    """

    def test_a_higher_tier_wins_even_at_the_worst_rank(self) -> None:
        self.assertGreater(fair_priority(2, FAIRNESS_SCALE - 1), fair_priority(1, 0))

    def test_lower_rank_wins_inside_the_same_tier(self) -> None:
        self.assertGreater(fair_priority(3, 0), fair_priority(3, 1))
        self.assertEqual(3 * FAIRNESS_SCALE - 2, fair_priority(3, 2))

    def test_scale_must_exceed_the_largest_possible_rank(self) -> None:
        # 衣柜条目上限就是最大可能序号 + 1；scale 留足余量，序号才撑不破 tier。
        self.assertGreater(FAIRNESS_SCALE, WARDROBE_MAX_ITEMS)

    def test_rank_is_clamped_so_it_never_pierces_the_tier(self) -> None:
        self.assertGreater(fair_priority(1, 10_000), fair_priority(0, 0))
        self.assertEqual(fair_priority(1, FAIRNESS_SCALE - 1), fair_priority(1, 10_000))
        self.assertEqual(fair_priority(1, 0), fair_priority(1, -5))

    def test_illegal_scale_falls_back_to_the_default(self) -> None:
        self.assertEqual(fair_priority(2, 1), fair_priority(2, 1, scale=0))
        self.assertEqual(fair_priority(2, 1), fair_priority(2, 1, scale="不是数字"))

    def test_garbage_inputs_fall_back(self) -> None:
        self.assertEqual(fair_priority(1, 0), fair_priority(1, None))
        self.assertEqual(fair_priority(0, 0), fair_priority("不是数字", "也不是"))

    def test_round_robin_order_visits_every_slot_before_any_second_item(self) -> None:
        # 三个部位、件数 3 / 2 / 1：顺序是 上0 下0 鞋0 上1 下1 上2。
        rows = [
            {"key": "上0", "priority": fair_priority(1, 0)},
            {"key": "上1", "priority": fair_priority(1, 1)},
            {"key": "上2", "priority": fair_priority(1, 2)},
            {"key": "下0", "priority": fair_priority(1, 0)},
            {"key": "下1", "priority": fair_priority(1, 1)},
            {"key": "鞋0", "priority": fair_priority(1, 0)},
        ]
        self.assertEqual(
            ["上0", "下0", "鞋0", "上1", "下1", "上2"],
            [row["key"] for row in order_by_priority(rows)],
        )


class PackTests(unittest.TestCase):
    """三趟式装箱：priority 决定谁被丢，weight 决定谁在前。"""

    def test_priority_decides_who_survives_a_short_budget(self) -> None:
        rows = [_entry("低", priority=1, length=5), _entry("高", priority=9, length=5)]
        result = _pack(rows, budget=5)
        self.assertEqual(["高"], _keys(result["kept"]))
        self.assertEqual(["低"], _keys(row["entry"] for row in result["dropped"]))
        self.assertEqual(5, result["used"])
        self.assertEqual(0, result["remaining"])
        self.assertTrue(result["truncated"])

    def test_equal_priority_falls_back_to_input_order(self) -> None:
        rows = [_entry("先", priority=5, length=5), _entry("后", priority=5, length=5)]
        result = _pack(rows, budget=5)
        self.assertEqual(["先"], _keys(result["kept"]))

    def test_budget_boundary_is_inclusive(self) -> None:
        rows = [_entry("刚好", priority=1, length=10)]
        self.assertEqual(["刚好"], _keys(_pack(rows, budget=10)["kept"]))
        dropped = _pack(rows, budget=9)["dropped"]
        self.assertEqual([DROP_REASON_OVERSIZE], [row["reason"] for row in dropped])

    def test_oversize_is_distinguished_from_a_full_box(self) -> None:
        rows = [_entry("小", priority=1, length=4), _entry("巨", priority=9, length=100)]
        result = _pack(rows, budget=10)
        # 巨件优先级最高却依然进不去：空箱也放不下，原因是 oversize 而不是 budget。
        self.assertEqual(["小"], _keys(result["kept"]))
        self.assertEqual(DROP_REASON_OVERSIZE, result["dropped"][0]["reason"])
        self.assertEqual("巨", result["dropped"][0]["entry"]["key"])

    def test_limit_reason_is_recorded(self) -> None:
        rows = [_entry("一", priority=3, length=1), _entry("二", priority=2, length=1)]
        result = _pack(rows, budget=100, max_entries=1)
        self.assertEqual(["一"], _keys(result["kept"]))
        self.assertEqual(DROP_REASON_LIMIT, result["dropped"][0]["reason"])

    def test_weight_only_reorders_the_kept_set(self) -> None:
        rows = [
            _entry("a", priority=3, weight=30, length=1),
            _entry("b", priority=2, weight=10, length=1),
            _entry("c", priority=1, weight=20, length=1),
        ]
        result = _pack(rows, budget=2)
        # 贪心阶段保住的是 a（优先级最高）与 b（次高）；重排后 b 在前、a 在后。
        self.assertEqual(["b", "a"], _keys(result["kept"]))
        self.assertEqual(["c"], _keys(row["entry"] for row in result["dropped"]))
        self.assertEqual(2, result["kept_count"])
        self.assertEqual(1, result["dropped_count"])

    def test_weight_ties_keep_the_priority_order(self) -> None:
        # 第三趟是稳定排序：同权重时保持"贪心收下的顺序"（priority 降序、同分输入序）。
        rows = [
            _entry("低", priority=1, weight=0, length=1),
            _entry("高", priority=2, weight=0, length=1),
        ]
        self.assertEqual(["高", "低"], _keys(_pack(rows, budget=2)["kept"]))

    def test_group_cost_is_charged_once_per_group(self) -> None:
        rows = [
            _entry("g1a", priority=2, length=4, group="上身"),
            _entry("g1b", priority=2, length=4, group="上身"),
            _entry("g2", priority=2, length=4, group="下身"),
        ]
        # 上身第一条付 3 的标题开销、第二条免费；下身再付一次 3：4+3+4+4+3 = 18。
        result = _pack(rows, budget=18, group_cost={"上身": 3, "下身": 3})
        self.assertEqual(["g1a", "g1b", "g2"], _keys(result["kept"]))
        self.assertEqual(18, result["used"])
        self.assertEqual(0, result["remaining"])
        # 少 1 个字符就轮到最后一条被丢：标题开销确实计进了预算。
        tighter = _pack(rows, budget=17, group_cost={"上身": 3, "下身": 3})
        self.assertEqual(["g1a", "g1b"], _keys(tighter["kept"]))
        self.assertEqual(DROP_REASON_BUDGET, tighter["dropped"][0]["reason"])

    def test_group_cost_is_not_charged_when_the_whole_group_loses(self) -> None:
        # 上身整组都放不下（3 + 5 > 6）：它不该偷偷吃掉预算，下身收下时标题也只收一次。
        rows = [
            _entry("g1a", priority=9, length=3, group="上身"),
            _entry("g1b", priority=8, length=3, group="上身"),
            _entry("g2", priority=1, length=1, group="下身"),
        ]
        result = _pack(rows, budget=6, group_cost={"上身": 5, "下身": 5})
        self.assertEqual(["g2"], _keys(result["kept"]))
        self.assertEqual(6, result["used"])
        self.assertEqual(["g1a", "g1b"], _keys(row["entry"] for row in result["dropped"]))

    def test_empty_group_name_is_still_a_group(self) -> None:
        # "未分类"在渲染层就是空字符串槽位；空名字不能变成"免标题费"的后门。
        rows = [_entry("未分类甲", priority=2, length=4, group=""), _entry("未分类乙", priority=2, length=4, group="")]
        result = _pack(rows, budget=11, group_cost={"": 3})
        self.assertEqual(["未分类甲", "未分类乙"], _keys(result["kept"]))
        self.assertEqual(11, result["used"])
        self.assertEqual(["未分类甲"], _keys(_pack(rows, budget=10, group_cost={"": 3})["kept"]))

    def test_missing_scores_default_to_zero(self) -> None:
        rows = [{"key": "裸条目", "length": 2}, _entry("标了分的", priority=0, length=2)]
        result = _pack(rows, budget=4)
        self.assertEqual(0, entry_priority(result["kept"][0]))
        self.assertEqual(["裸条目", "标了分的"], _keys(result["kept"]))
        self.assertEqual(["裸条目"], _keys(_pack(rows, budget=2)["kept"]))

    def test_empty_input_is_a_valid_empty_result(self) -> None:
        result = _pack([], budget=100)
        self.assertEqual([], result["kept"])
        self.assertEqual([], result["dropped"])
        self.assertEqual(0, result["used"])
        self.assertEqual(100, result["remaining"])
        self.assertFalse(result["truncated"])

    def test_zero_budget_drops_everything_without_raising(self) -> None:
        result = _pack([_entry("a", length=1)], budget=0)
        self.assertEqual([], result["kept"])
        self.assertEqual(DROP_REASON_OVERSIZE, result["dropped"][0]["reason"])

    def test_result_is_json_serialisable(self) -> None:
        result = _pack([_entry("a", priority=1, length=1)], budget=1)
        self.assertIn('"kept"', json.dumps(result, ensure_ascii=False))

    def test_measure_errors_are_not_swallowed(self) -> None:
        def _boom(_entry):
            raise RuntimeError("长度算不出来")

        with self.assertRaises(RuntimeError):
            pack_entries([_entry("a")], budget=10, measure=_boom)


class DeterminismTests(unittest.TestCase):
    def test_identical_input_gives_identical_output(self) -> None:
        rows = [
            _entry("a", priority=2, weight=20, length=3, group="g1"),
            _entry("b", priority=2, weight=10, length=3, group="g2"),
            _entry("c", priority=1, weight=30, length=3, group="g1"),
        ]
        first = _pack(rows, budget=9, group_cost={"g1": 1, "g2": 1})
        for _ in range(20):
            again = _pack(rows, budget=9, group_cost={"g1": 1, "g2": 1})
            self.assertEqual(_keys(first["kept"]), _keys(again["kept"]))
            self.assertEqual([row["entry"]["key"] for row in first["dropped"]],
                             [row["entry"]["key"] for row in again["dropped"]])
            self.assertEqual(first["used"], again["used"])

    def test_group_cost_mapping_order_does_not_matter(self) -> None:
        rows = [_entry("a", priority=1, length=2, group="上身"), _entry("b", priority=1, length=2, group="下身")]
        first = _pack(rows, budget=7, group_cost={"上身": 3, "下身": 3})
        second = _pack(rows, budget=7, group_cost={"下身": 3, "上身": 3})
        self.assertEqual(_keys(first["kept"]), _keys(second["kept"]))
        self.assertEqual(first["used"], second["used"])

    def test_ordering_does_not_depend_on_field_presence(self) -> None:
        # 有 priority/weight 字段与完全裸的条目，只要分数相同，顺序就相同。
        scored = [_entry("a", priority=0, weight=0, length=1), _entry("b", priority=0, weight=0, length=1)]
        bare = [{"key": "a", "length": 1}, {"key": "b", "length": 1}]
        self.assertEqual(_keys(order_by_priority(scored)), _keys(order_by_priority(bare)))
        self.assertEqual(_keys(order_by_weight(scored)), _keys(order_by_weight(bare)))


class RenderIntegrationTests(unittest.TestCase):
    """渲染层接线：装箱决定"留哪些、丢哪些"，weight 决定部位顺序。"""

    def test_slot_order_follows_weight(self) -> None:
        items = normalize_wardrobe_items(
            [
                {"name": "细框眼镜", "slot": "extra"},
                {"name": "帆布鞋", "slot": "feet"},
                {"name": "深色长裤", "slot": "lower"},
                {"name": "米色开衫", "slot": "upper"},
                {"name": "碎花连衣裙", "slot": "whole"},
                {"name": "旧T恤", "description": "没标部位"},
            ]
        )
        block = render_wardrobe_block("", items, max_chars=2000)
        positions = [
            block.index(header)
            for header in ("── 整身 ──", "── 上身 ──", "── 下身 ──", "── 足部 ──", "── 配件 ──", "── 未分类 ──")
        ]
        self.assertEqual(sorted(positions), positions)
        self.assertLess(positions[0], positions[1])

    def test_small_wardrobe_still_renders_byte_for_byte(self) -> None:
        # 没有触发装箱丢弃时，输出与改造前逐字一致（向后兼容的锚点）。
        items = normalize_wardrobe_items(
            [
                {"name": "米色针织开衫", "description": "宽松版型", "slot": "upper", "tags": ["居家"]},
                {"name": "深色直筒长裤", "slot": "lower"},
            ]
        )
        expected = "\n".join(
            [
                "整体服饰倾向：偏爱低饱和色",
                "衣柜里的具体衣物：",
                "── 上身 ──",
                "- 米色针织开衫（居家）：宽松版型",
                "── 下身 ──",
                "- 深色直筒长裤",
            ]
        )
        self.assertEqual(expected, render_wardrobe_block("偏爱低饱和色", items))

    def test_unclassified_items_are_dropped_before_classified_ones(self) -> None:
        items = normalize_wardrobe_items(
            [
                {"name": "米色开衫", "description": "宽松版型", "slot": "upper"},
                {"name": "来历不明的一件", "description": "没标部位"},
            ]
        )
        reference = render_wardrobe_block("", items[:1], max_chars=2000)
        budget = len(reference) + 20
        block = render_wardrobe_block("", items, max_chars=budget)
        self.assertIn("米色开衫", block)
        self.assertNotIn("来历不明的一件", block)
        self.assertIn("另有 1 件未列出", block)
        self.assertLessEqual(len(block), budget)

    def test_described_items_are_kept_before_bare_ones(self) -> None:
        items = normalize_wardrobe_items(
            [
                {"name": "开衫甲", "slot": "upper"},
                {"name": "开衫乙", "description": "宽松罗纹袖口", "slot": "upper"},
            ]
        )
        block = render_wardrobe_block("", items, max_items=1, max_chars=2000)
        self.assertIn("开衫乙", block)
        self.assertNotIn("开衫甲", block)
        self.assertIn("另有 1 件未列出", block)

    def test_intimate_marker_is_rendered_without_jumping_the_queue(self) -> None:
        # 贴身件照旧打标记，但不额外吃渲染优先级：给它加一档会让它跳到所有部位的
        # 第 0 件之前，把轮转打破（实测预设衣柜 cap=300 时变成上身 3 件，而整身 /
        # 足部 / 配件各 1 件，极差 2）。贴身轴属于散件**选择**路径，不是渲染预算。
        items = normalize_wardrobe_items(
            [
                {"name": "开衫甲", "slot": "upper"},
                {"name": "白色内衣", "slot": "upper", "intimate": True},
            ]
        )
        block = render_wardrobe_block("", items, max_items=1, max_chars=2000)
        self.assertIn("开衫甲", block)
        self.assertNotIn("白色内衣", block)
        # 两件都列出来时贴身标记仍然在（只是不参与抢预算）。
        self.assertIn("白色内衣（贴身）", render_wardrobe_block("", items, max_chars=2000))

    def test_notice_counts_every_dropped_item_and_the_budget_holds(self) -> None:
        items = normalize_wardrobe_items(
            [
                {"name": f"衣物{index}", "description": "描" * 20, "slot": slot}
                for index, slot in enumerate(("whole", "upper", "lower", "feet", "extra", "upper"))
            ]
            + [{"name": "旧T恤", "description": "没标部位"}]
        )
        full = render_wardrobe_block("", items, max_chars=4000)
        self.assertEqual(len(items), full.count("\n- "))
        for budget in range(60, len(full) + 1, 5):
            block = render_wardrobe_block("", items, max_chars=budget)
            self.assertLessEqual(len(block), budget)
            if block.endswith("…"):
                # 预算小到连"另有 N 件未列出"那行都被截掉：只要求不超预算。
                continue
            listed = block.count("\n- ")
            match = NOTICE_PATTERN.search(block)
            if match:
                self.assertEqual(len(items), listed + int(match.group(1)))
            else:
                self.assertEqual(len(items), listed)

    def test_char_budget_never_breaks_the_single_block_guarantee(self) -> None:
        items = normalize_wardrobe_items(
            [{"name": f"衣物{index}", "description": "很长的描述" * 10, "slot": "upper"} for index in range(30)]
        )
        block = render_wardrobe_block("", items)
        self.assertLessEqual(len(block), 900)
        self.assertIn("另有", block)
        match = NOTICE_PATTERN.search(block)
        self.assertIsNotNone(match)
        self.assertEqual(len(items), block.count("\n- ") + int(match.group(1)))


class RenderFairnessTests(unittest.TestCase):
    """预算或条数收紧时不能把整段部位饿死：同 tier 内按部位轮转。"""

    def _assert_fair_block(self, block: str, items: list[dict], *, budget: int) -> dict[str, int]:
        counts = _slot_counts(block)
        self.assertEqual(5, len(counts), counts)
        self.assertTrue(all(count >= 1 for count in counts.values()), counts)
        # 轮转的直接推论：各部位保留件数极差不超过 1。
        self.assertLessEqual(max(counts.values()) - min(counts.values()), 1, counts)
        self.assertLessEqual(len(block), budget)
        listed = block.count("\n- ")
        match = NOTICE_PATTERN.search(block)
        self.assertIsNotNone(match, block)
        self.assertEqual(len(items), listed + int(match.group(1)))
        return counts

    def test_preset_wardrobe_at_a_tight_character_budget(self) -> None:
        # 数据点一：19 件预设 / cap=300。修复前是 上身 6、下身 3、整身/足部/配件 0。
        items = _preset_items()
        block = render_wardrobe_block("偏爱宽松针织", items, max_items=20, max_chars=300)
        counts = self._assert_fair_block(block, items, budget=300)
        self.assertGreaterEqual(sum(counts.values()), 5, counts)

    def test_item_cap_spreads_across_slots_as_well(self) -> None:
        # 数据点二：40 件 / max_items=20 / cap=900 —— 这里卡住的是条数上限而不是
        # 字符预算（修复前是 整身 2、上身 7、下身 7、足部 3、配件 1）。
        #
        # 加了部位配额之后，这里**不再**要求"各部位件数相等"：配额会按必要性把
        # 配件封顶，省下来的名额让给上装/下装，所以实测是 4 / 5 / 4 / 4 / 3。
        # 该守的性质变成两条：谁都不为零，谁都不超配额。
        items = _forty_items()
        self.assertEqual(40, len(items))
        block = render_wardrobe_block("偏爱宽松针织", items, max_items=20, max_chars=900)
        counts = _slot_counts(block)
        self.assertEqual(5, len(counts), counts)
        self.assertTrue(all(count >= 1 for count in counts.values()), counts)
        self.assertLessEqual(len(block), 900)
        self.assertEqual(20, sum(counts.values()), counts)
        totals: dict[str, int] = {}
        for item in items:
            slot = str(item.get("slot") or "")
            totals[slot] = totals.get(slot, 0) + 1
        quotas = _wardrobe_slot_quotas(totals, 20)
        for slot, quota in quotas.items():
            self.assertLessEqual(counts[WARDROBE_SLOT_LABELS[slot]], quota, (slot, counts))
        match = NOTICE_PATTERN.search(block)
        self.assertIsNotNone(match, block)
        self.assertEqual(len(items), block.count("\n- ") + int(match.group(1)))

    def test_accessories_cannot_monopolize_the_item_cap(self) -> None:
        # 偏斜衣柜：20 件配件 + 2 件上衣 + 2 件下装。轮转只保证"每部位都有份"，
        # 不限制份额 —— 没有配额时别的部位挑完，剩下的条数名额全归配件（实测 16/20，
        # 提示词里 80% 是配饰）。配额把这种偏斜按必要性压回去。
        items = normalize_wardrobe_items(
            [
                {"name": f"饰品{index}", "description": "小配饰", "slot": "extra"}
                for index in range(20)
            ]
            + [
                {"name": "上衣甲", "description": "上装", "slot": "upper"},
                {"name": "上衣乙", "description": "上装", "slot": "upper"},
                {"name": "长裤甲", "description": "下装", "slot": "lower"},
                {"name": "长裤乙", "description": "下装", "slot": "lower"},
            ]
        )
        self.assertEqual(24, len(items))
        block = render_wardrobe_block("", items, max_items=20, max_chars=900)
        self.assertEqual({"上身": 2, "下身": 2, "配件": 4}, _slot_counts(block), block)
        match = NOTICE_PATTERN.search(block)
        self.assertIsNotNone(match, block)
        self.assertEqual(len(items), block.count("\n- ") + int(match.group(1)))

    def test_a_single_item_slot_is_never_starved(self) -> None:
        # 只有一件的部位在第 0 轮就该进来，不能因为别的部位件多而永远轮不到。
        items = normalize_wardrobe_items(
            [
                {"name": f"上衣{index}", "description": "宽松版型", "slot": "upper"}
                for index in range(6)
            ]
            + [{"name": "唯一的鞋", "description": "低帮", "slot": "feet"}]
        )
        block = render_wardrobe_block("", items, max_items=2, max_chars=900)
        self.assertIn("唯一的鞋", block)
        self.assertEqual(2, block.count("\n- "))


class SelectionIntegrationTests(unittest.TestCase):
    """散件兜底：候选池用 priority 排序，但两条既有语义一条都不能破。"""

    @staticmethod
    def _pick_name(items, *, seed, recent_ids=()):
        result = select_wardrobe_outfit(items, [], seed=seed, recent_ids=set(recent_ids))
        return [row["name"] for row in result["picked"]]

    def test_cooldown_is_a_hard_filter_even_against_a_described_rival(self) -> None:
        # 刚穿过的那件描述再丰富也不能因为"有描述"被重新捡回来。
        items = normalize_wardrobe_items(
            [
                {"id": "worn", "name": "刚穿过的开衫", "description": "宽松罗纹袖口", "slot": "upper"},
                {"id": "fresh", "name": "没穿过的开衫", "slot": "upper"},
            ]
        )
        names = self._pick_name(items, seed="frost", recent_ids={"worn"})
        self.assertIn("没穿过的开衫", names)
        self.assertNotIn("刚穿过的开衫", names)

    def test_priority_orders_the_pool_before_the_seed_pick(self) -> None:
        bare = normalize_wardrobe_items(
            [
                {"id": "a", "name": "AAA", "slot": "upper"},
                {"id": "z", "name": "ZZZ", "slot": "upper"},
            ]
        )
        described = normalize_wardrobe_items(
            [
                {"id": "a", "name": "AAA", "slot": "upper"},
                {"id": "z", "name": "ZZZ", "description": "有描述", "slot": "upper"},
            ]
        )
        # 对照组的池内顺序就是内容顺序 [AAA, ZZZ]，所以"种子选中 AAA"等价于
        # "这个种子取的是池内第 0 项"。池大小都是 2，索引只取决于种子，与控制组
        # 里条目叫什么无关 —— 于是可以用它反推打分有没有真的改变池内顺序。
        index_zero_seed = next(
            (f"s{index}" for index in range(200) if self._pick_name(bare, seed=f"s{index}") == ["AAA"]),
            None,
        )
        self.assertIsNotNone(index_zero_seed, "200 个种子里应该有落回池内第 0 项的")
        self.assertEqual(["ZZZ"], self._pick_name(described, seed=index_zero_seed))

    def test_one_item_per_slot_and_the_intimate_axis_survive(self) -> None:
        items = normalize_wardrobe_items(
            [
                {"name": "开衫甲", "slot": "upper"},
                {"name": "开衫乙", "description": "宽松", "slot": "upper"},
                {"name": "白色内衣", "slot": "upper", "intimate": True},
                {"name": "长裤甲", "slot": "lower"},
                {"name": "棉质内裤", "slot": "lower", "intimate": True},
                {"name": "碎花连衣裙", "slot": "whole"},
            ]
        )
        for day in range(1, 25):
            result = select_wardrobe_outfit(items, [], seed=f"2026-09-{day:02d}")
            outer = [row for row in result["picked"] if not row["intimate"]]
            slots = [row["slot"] for row in outer]
            self.assertEqual(len(slots), len(set(slots)), f"day {day}")
            outer_slots = set(slots)
            self.assertFalse(
                "whole" in outer_slots and ({"upper", "lower"} & outer_slots),
                f"day {day}: 整身与上下装不能同时出现",
            )
            names = {row["name"] for row in result["picked"]}
            self.assertIn("白色内衣", names)
            self.assertIn("棉质内裤", names)

    def test_selection_is_stable_for_the_same_seed(self) -> None:
        items = normalize_wardrobe_items(
            [
                {"name": "开衫甲", "slot": "upper"},
                {"name": "开衫乙", "description": "宽松", "slot": "upper"},
                {"name": "长裤甲", "slot": "lower"},
            ]
        )
        first = select_wardrobe_outfit(items, [], seed="2026-09-12")
        second = select_wardrobe_outfit(items, [], seed="2026-09-12")
        self.assertEqual(first["look_id"], second["look_id"])
        self.assertEqual(
            [row["id"] for row in first["picked"]],
            [row["id"] for row in second["picked"]],
        )


if __name__ == "__main__":
    unittest.main()
