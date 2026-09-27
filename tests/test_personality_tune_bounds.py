"""回归守护：人格自动调参的边界判定必须可达。

覆盖两个真实缺陷：

1. ``page_api.py`` 回避型收缩风险分支里写成 ``0 < current < 2``。
   ``current_int`` 返回 int，不存在「大于 0 且小于 2」的整数，
   因此 ``propose("max_daily_messages", 2, ...)`` 是死代码，
   「保留很低频主动上限」这条兜底策略永远不会触发。
   正确写法是 ``current < 2``（等价于「上限已被压到 2 以下」）。

2. ``page_api.py`` 重要日期投影里，``repeat_yearly`` 的默认值
   依赖 ``len(raw_date) == 5``。此前带时间戳的日期（``2026-03-05T08:00``）
   在切掉 ``T`` 之后仍是 10 位，默认被判为「不按年重复」；
   而用户填 ``03-05``（5 位）时默认是「按年重复」。
   同一字段的默认语义不应随日期书写格式而变。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PAGE_API = REPO_ROOT / "page_api.py"
# 页面 API 已按域拆分；人格/人设域的方法与模块级函数在 persona mixin 里，
# 因此扫描时必须覆盖宿主文件与相关 mixin 文件。
PAGE_API_DOMAIN_FILES = (
    REPO_ROOT / "page_api.py",
    REPO_ROOT / "page_api_persona.py",
    REPO_ROOT / "page_api_persona_runtime.py",
    REPO_ROOT / "page_api_media.py",
)


def _read_source() -> str:
    """宿主 page_api.py 的源码（兼容旧断言）。"""
    return PAGE_API.read_text(encoding="utf-8")


def _read_domain_source() -> str:
    """宿主 + 各 mixin 域文件的合并源码。"""
    parts: list[str] = []
    for path in PAGE_API_DOMAIN_FILES:
        if path.exists():
            parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def test_no_degenerate_between_zero_and_two_guard() -> None:
    """``0 < int(...) < 2`` 是恒假条件，属于死代码。"""
    source = _read_domain_source()
    # 只匹配「0 < xxx < 2」这种整数不可能落在其中的区间
    pattern = re.compile(r"0\s*<\s*[^\n<]*?<\s*2\s*:")
    hits = pattern.findall(source)
    assert not hits, (
        "page_api.py 中存在恒假的退化比较 0 < X < 2，"
        f"其分支永远不会执行：{hits}"
    )


def test_avoidant_branch_reachable_for_low_cap() -> None:
    """回避型收缩风险分支必须能在 max_daily_messages 偏低时触发兜底。"""
    source = _read_domain_source()
    tree = ast.parse(source)
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        segment = ast.get_source_segment(source, node.test) or ""
        if "max_daily_messages" not in segment:
            continue
        if "current_int" not in segment:
            continue
        found.append(segment.strip())
    assert found, "未找到针对 max_daily_messages 的可达性判定，结构可能已被改动"
    for segment in found:
        # 判定里不应出现「> 0 且 < 2」这类不可能满足的区间
        assert not re.search(r">\s*0\s*<", segment.replace(" ", "")), (
            f"判定仍为退化区间：{segment}"
        )


def test_repeat_yearly_default_is_format_independent() -> None:
    """``repeat_yearly`` 默认值不应取决于日期是否带时间戳。"""
    source = _read_source()
    # 默认值不应把 len(raw_date) == 5 当作「按年重复」的唯一依据
    bad = re.search(
        r"repeat_yearly\s*=\s*self\._normalize_bool_value\(\s*entry\.get\(\s*['\"]repeat_yearly['\"]\s*,\s*len\(raw_date\)\s*==\s*5\s*\)",
        source,
    )
    assert bad is None, (
        "repeat_yearly 的默认值依赖 len(raw_date) == 5，"
        "会让 '2026-03-05' 与 '03-05' 得到相反的默认语义"
    )
