"""主题令牌完整性审计。

背景：主题是纯 CSS 自定义属性，组件直接引用 --t-* 名。
某个色板漏声明一个变量时，CSS 不会回落到 :root 的默认值——
继承链上找不到该属性，引用处直接得到无效值（透明或黑）。
这类缺陷只在切换到那一套主题时才可见，最容易被漏掉。

本脚本做四件事：
  1. 六套色板声明的 --t-* 集合必须完全一致（多一个少一个都算失败）。
  2. 每套必须声明 --t-scene-light，界面主题靠它联动小屋光照。
  3. 被页面实际引用的 --t-* 名必须都在色板里存在，否则是引用了一个不存在的令牌。
  4. 页面不得绕开令牌写死色值。

第 4 条是后加的：前三项只看 var(--t-*) 的**引用**，
所以「自己造一套色板」这种绕过方式完全不在检查范围里。
models-page.css 曾经就自带 21 个写死十六进制的 --m-* 变量，
外加一份手写的暗色副本——它逃过了前三项，六套主题里只有明暗两态生效。

用法：python3 tools/audit_theme_tokens.py
退出码 0 表示通过，1 表示有缺项。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKENS = ROOT / "pages" / "陪伴小屋" / "tokens.css"
PAGE_DIR = ROOT / "pages" / "陪伴小屋"

# 色板选择器 -> 展示名。键用于报错时指认是哪一套。
THEME_SELECTORS = {
    'dawn': "晨雾",
    'courtyard': "庭院",
    'dusk': "暮色",
    'night': "夜读",
    'walnut': "深林",
    'contrast': "高对比",
}

SKIN_PROFILE = {
    "--skin-heading-font", "--skin-canvas-pattern", "--skin-canvas-size",
    "--skin-r-2xs", "--skin-r-xs", "--skin-r-sm", "--skin-r-md", "--skin-r-lg", "--skin-r-xl",
}

# 样式表里不属于任何具体主题块的辅助声明（尺寸、组件令牌等）。
DECLARATION = re.compile(r"--([a-z0-9-]+)\s*:")

# 硬编码色值基线：这些文件还欠着债，值 = 现状（只许减不许增）。
# 口径是**去重后的不同色值数**（同一个 #fff 出现 20 次算 1 个）——
# 因为要抓的是「有几套色在绕过令牌」，不是「打错了几次」。
# 已清零的文件刻意不出现在这里：一旦有人加回硬编码色值，基数 0 立刻报警。
# 每迁移一个文件就把它删掉。人数看得见，债才还得动。
HARDCODED_BASELINE = {
    "styles.css": 274,          # 外壳 + 未迁移旧页面的共用样式，最大一块，留到最后
    "product-page.css": 22,
    # 「全部功能」浮层迁到 --t-* 令牌后从 17 降到 4。剩下的是 ::backdrop 的
    # 压暗遮罩（它必须始终比页面暗，不能跟着主题文字色走），以及 .rail-links /
    # .nav-group 三段已无 JS 生成的旧样式。
    "navigation.css": 4,
    "shell.css": 2,
    "contact-page.css": 1,
    # 缓存分析图的数据序列色（绿/青/紫/金四色）。它们是图表数据编码，
    # 不是界面色：换主题时数据序列必须保持同一套颜色，否则同一张图
    # 在不同主题下会读成不同的东西。这是明确保留的例外，不是待还的债。
    "maintenance-page.css": 8,
    # 图片上的压暗渐变与叠在上面的白字：必须始终比照片暗，跟着主题变会让
    # 亮色主题下的缩略图标题直接看不见。固定值是有意的，不是漏改。
    "output-page.css": 2,
    # 卡片阴影与虚线上的一层白色蒙层：固定透明度的阴影在六套主题下都成立，
    # 换成随主题变的颜色反而会让暗色主题的浮起感消失。
    "setup-guide.css": 1,
}

# 生成物：bundle.css 是 css-sources.json 按序拼接的产物，里面的色值是从源
# 文件复制的副本。要抓的问题在源文件上（上面那张表），对拼接产物再数一遍
# 只会让同一个数字翻倍，且永远无法归零。
GENERATED_STYLESHEETS = {"bundle.css"}

# tokens.css 本身是色值的唯一来源，不参与第 4 条。
# tokens-bridge.css 只做旧变量名桥接，它的色值引用都指向令牌，同样豁免。
COLOR_SOURCE_EXEMPT = {"tokens.css", "tokens-bridge.css"}

# 生成物快照：这些是把页面 CSS 内联进去后打出来的整份 HTML（单份 15MB 量级），
# 里面的色值是从源文件复制的，不是独立写的。修源文件后重新生成即可，
# 不该被当成"页面写死了色值"。未列在此处的快照仍会被检查。
SNAPSHOT_FILES = {
    "shell-preview.html",
    "theme-preview.html",
    "v2-surface-preview.html",
}

HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
FUNC_COLOR = re.compile(r"\b(?:rgba?|hsla?)\(")


def strip_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", " ", text, flags=re.S)


def hardcoded_colors(path: Path) -> list[str]:
    """返回文件里绕开令牌的硬编码色值（去重后）。

    只看**字面量**：`#fff`、`rgba(...)`。
    `var(--t-*)` 与 `color-mix(in srgb, var(--t-*) …)` 都不算——
    后者里的 `srgb` 不是色值，所以正则要求 `(` 紧跟 rgb/hsl。
    """
    text = strip_comments(path.read_text(encoding="utf-8", errors="ignore"))
    found = HEX.findall(text) + [m.group(0) for m in FUNC_COLOR.finditer(text)]
    return sorted(set(found))


def read_source() -> str:
    if not TOKENS.exists():
        raise SystemExit(f"找不到令牌文件：{TOKENS}")
    return TOKENS.read_text(encoding="utf-8")


def blocks(source: str) -> list[tuple[int, int, str]]:
    """按花括号配平切出顶层规则块，返回 (start, end, selector)。"""
    found: list[tuple[int, int, str]] = []
    index = 0
    length = len(source)
    while index < length:
        brace = source.find("{", index)
        if brace < 0:
            break
        selector = source[index:brace]
        depth = 1
        cursor = brace + 1
        while cursor < length and depth:
            if source[cursor] == "{":
                depth += 1
            elif source[cursor] == "}":
                depth -= 1
            cursor += 1
        found.append((brace + 1, cursor - 1, selector))
        index = cursor
    return found


def theme_tokens(source: str, name: str) -> set[str]:
    """收集某套主题块里声明的 --t-* 令牌名。"""
    pattern = re.compile(r'data-theme="' + re.escape(name) + r'"')
    collected: set[str] = set()
    for start, end, selector in blocks(source):
        if not pattern.search(selector):
            continue
        for match in DECLARATION.finditer(source[start:end]):
            token = match.group(1)
            if token.startswith("t-"):
                collected.add("--" + token)
    return collected


def theme_skin_tokens(source: str, name: str) -> set[str]:
    """Collect the non-color presentation profile for one theme."""
    pattern = re.compile(r'data-theme="' + re.escape(name) + r'"')
    collected: set[str] = set()
    for start, end, selector in blocks(source):
        if not pattern.search(selector):
            continue
        for match in DECLARATION.finditer(source[start:end]):
            token = "--" + match.group(1)
            if token.startswith("--skin-"):
                collected.add(token)
    return collected


def alias_tokens(source: str) -> set[str]:
    """主题块之外声明的 --t-* 别名（如 --t-danger:var(--t-risk-ink)）。

    这些名字全局可用，不需要每套色板各自声明——它们指向的才是随主题变的。
    单独收出来，否则它们会被误判成悬空引用。
    """
    pattern = re.compile(r'data-theme')
    collected: set[str] = set()
    for start, end, selector in blocks(source):
        if pattern.search(selector):
            continue
        for match in DECLARATION.finditer(source[start:end]):
            token = match.group(1)
            if token.startswith("t-"):
                collected.add("--" + token)
    return collected


def referenced_tokens() -> set[str]:
    """页面实际引用到的 --t-* 名（含 var() 与直接书写两种写法）。

    跳过生成物快照：那些 HTML 是把源文件内联进去打出来的，
    里面的引用是复制的副本。源文件里已经没有的名字会继续在快照里"出现"，
    让这一项永远消不掉——而它并不代表任何活代码在引用。
    """
    seen: set[str] = set()
    for path in sorted(PAGE_DIR.glob("*")):
        if path.suffix not in {".css", ".js", ".html"}:
            continue
        if path.name == "tokens.css" or path.name in SNAPSHOT_FILES:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in re.finditer(r"--t-[a-z0-9-]+", text):
            seen.add(match.group(0))
    return seen


def main() -> int:
    source = read_source()
    per_theme = {name: theme_tokens(source, name) for name in THEME_SELECTORS}
    skins = {name: theme_skin_tokens(source, name) for name in THEME_SELECTORS}
    aliases = alias_tokens(source)

    failures: list[str] = []

    empty = [THEME_SELECTORS[name] for name, tokens in per_theme.items() if not tokens]
    if empty:
        failures.append("以下主题没有解析到任何 --t-* 令牌（选择器写错或块未闭合）："
                        + "、".join(empty))
        report(per_theme, failures)
        return 1

    for name, tokens in skins.items():
        missing = SKIN_PROFILE - tokens
        extra = tokens - SKIN_PROFILE
        if missing or extra:
            detail = []
            if missing:
                detail.append("缺少 " + ", ".join(sorted(missing)))
            if extra:
                detail.append("多出 " + ", ".join(sorted(extra)))
            failures.append(f"{THEME_SELECTORS[name]}（{name}）的结构皮肤不完整：" + "；".join(detail))

    union: set[str] = set()
    for tokens in per_theme.values():
        union |= tokens

    # 1. 色板之间集合必须一致。别名不参与，它们不属于任何单套主题。
    for name, tokens in per_theme.items():
        missing = union - tokens
        extra = tokens - union
        if missing:
            failures.append(f"{THEME_SELECTORS[name]}（{name}）缺少 "
                            f"{len(missing)} 个令牌：" + "、".join(sorted(missing)))
        if extra:
            failures.append(f"{THEME_SELECTORS[name]}（{name}）多出 "
                            f"{len(extra)} 个令牌：" + "、".join(sorted(extra)))

    # 2. scene-light 必须存在。
    for name, tokens in per_theme.items():
        if "--t-scene-light" not in tokens:
            failures.append(f"{THEME_SELECTORS[name]}（{name}）没有 --t-scene-light，"
                            "界面主题无法联动小屋光照")

    # 3. 引用到的令牌必须在色板或别名里存在。
    used = referenced_tokens()
    declared = union | aliases
    unknown = used - declared
    if unknown:
        failures.append("以下令牌被页面引用但没有任何声明（会渲染成无效值）："
                        + "、".join(sorted(unknown)))

    # 4. 页面不得绕开令牌写死色值。
    #    这一项补的是前三项的盲区：它们只看 var(--t-*) 的引用，
    #    所以「自己造一套色板」完全不在检查范围里。
    over_budget: list[str] = []
    migrated: list[str] = []
    for path in sorted(PAGE_DIR.glob("*.css")):
        if path.name in COLOR_SOURCE_EXEMPT or path.name in GENERATED_STYLESHEETS:
            continue
        count = len(hardcoded_colors(path))
        budget = HARDCODED_BASELINE.get(path.name, 0)
        if count > budget:
            over_budget.append(f"{path.name} 有 {count} 处硬编码色值，超出基线 {budget}"
                               + ("（新文件必须为 0）" if budget == 0 else ""))
        elif count < budget:
            migrated.append(f"{path.name} 已从 {budget} 降到 {count}，"
                            "请把 HARDCODED_BASELINE 里的数字改小或删掉该条目")
    if over_budget:
        failures.append("以下文件绕开令牌写死了色值（切主题时这些元素不会跟着变）："
                        + "；".join(over_budget))
    if migrated:
        failures.append("基线已过期，应下调：" + "；".join(migrated))

    report(per_theme, failures, used=used, union=union, aliases=aliases)
    return 1 if failures else 0


def report(per_theme, failures, *, used=None, union=None, aliases=None) -> None:
    print("主题令牌审计")
    print("=" * 52)
    for name, tokens in per_theme.items():
        print(f"  {THEME_SELECTORS[name]:<6} {name:<11} {len(tokens):>3} 个 --t-* 令牌")
    if union is not None:
        print(f"\n  色板令牌并集：{len(union)}")
    if aliases:
        print(f"  全局别名：{len(aliases)} — " + "、".join(sorted(aliases)))
    if used is not None and union is not None:
        declared = union | (aliases or set())
        print(f"  页面引用到的令牌：{len(used)}"
              f"（其中 {len(used - declared)} 个无声明）")
    print()
    if failures:
        print(f"失败：{len(failures)} 项")
        for item in failures:
            print(f"  ✗ {item}")
    else:
        print("全部通过：六套色板令牌集合一致，引用无悬空。")


if __name__ == "__main__":
    sys.exit(main())
