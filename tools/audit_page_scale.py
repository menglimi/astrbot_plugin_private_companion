#!/usr/bin/env python3
"""拓展页尺度审计：字号 / 间距 / 圆角必须走令牌，且令牌只有一个源。

为什么值得一个脚本：「有些乱」这种判断如果不落成数字，下次改动时会无声退回。
这个脚本把三项量出来并设阈值，让回退在下一次运行时立刻暴露。

四项检查
--------
1. **写死的字号 / 间距 / 圆角**。页面自己取数就会出现 9.5/10.5/11.5/12.5 这类
   差 1px 的"档位"，读者分辨不出、层级只剩噪音。阈值按页给：已迁移的页必须是 0。

2. **比最小档还小的字号**。字号档位是有下界的，而在有下界之前，页面各自
   往更小处取数：7px、8px、10px 满地都是，"标签"被写成了芝麻。这一条对
   **所有**页面生效（不只 MIGRATED）——下界是绝对约束，不是按页宽免的额度。
   阈值从 tokens.css 的 --fs-micro 实际值算出来，不写死数字：档位改了，
   判定跟着改，不会出现"阈值还停在旧档位"的脱节。

3. **尺度令牌只有一个源**。tokens.css 之外再声明 --r-* / --sp-* / --fs-* 是
   危险形状：两个文件同为 :root 优先级，后者静默覆盖前者，新加的令牌看着写进去了
   却毫无效果。这条曾经真的发生过（tokens-bridge.css 里有一份旧快照）。

4. **字号档位的实际跨度**。所有档位都挤在相邻 1px 内时，"标签 vs 正文"的分界
   就消失了。检查相邻档的最小间距。

用法：python3 tools/audit_page_scale.py [--verbose]
退出码非 0 表示有页面越过阈值。
"""
import argparse
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PAGES = ROOT / "pages" / "陪伴小屋"

# 生成物快照：把页面 CSS 内联进去后打出来的整份 HTML（单份 15MB 量级）。
# 里面的引用是从源文件复制的副本，源文件改好重新生成即可。
# 本节按 .css 后缀遍历，所以这里列的是同名快照里内联的那份样式表——
# 目前无 .css 形态的快照，留空常量是为了让将来加进来时有地方可写，
# 而不是让"忘了排除"重新变成一屏误报。
SNAPSHOT_CSS: set[str] = set()
GENERATED_CSS: set[str] = {"bundle.css"}

# 已迁移到尺度令牌的页面：这几页必须零写死。
MIGRATED = ("memory-page.css", "learning-page.css", "contact-page.css",
            "entrance.css", "appearance-page.css", "setup-guide.css",
            "reply-page.css", "product-page.css",
            "backup-page.css", "console.css",
            # 2026-10-01 这一批：模型与连接、状态、语义、行动、决策、话题。
            "models-page.css", "state-page.css", "semantic-page.css",
            "action-page.css", "decision-page.css", "topic-page.css",
            # 自我页展示区与配置导航只使用统一尺度令牌。
            "self-page.css", "self-review-page.css",
            # 2026-10-01 这一批（治理与维护 + 芝麻字号）：见下面的迁移记录。
            "maintenance-page.css", "expression-config.css",
            "styles.css", "output-page.css", "workspace-theme.css",
            "home.css", "navigation.css",
            # 工作台字段网格与配置编辑器：最后一页迁完，OTHERS 清空。
            "workbench.css", "config-editor.css",
            # 外壳最后一份：它本来就引用 --fs-*，只是从没进过名单，
            # 于是被【二之二】自发现抓了出来（半迁移）。迁干净后进来。
            "shell.css",
            # 2026-10-10：小屋分区已经使用令牌，补齐名单与天气设置迁移。
            "host-options.css", "learning-hub.css", "perception-subpages.css",
            "state-config.css", "storage-page.css", "understanding-page.css",
            "behavior-page.css",
            "weather-source.css",
            # 2026-10-11：原生扩展管理面板使用同一套尺度令牌，纳入阈值判定。
            "native-extension-panel.css", "confirmation-dialog.css",
            "workspace-loading.css",
            # 2026-10-24：旧版资料迁入的配置对照表随页面重构迁入尺度令牌。
            "migration-config-table.css")
# 仍在使用自有尺度的页面：只统计，不判定（逐步迁移）。
# 2026-10-02：`shell.css` 迁入后这里是空的——`pages/陪伴小屋/*.css` 全部走尺度令牌。
#
# 2026-10-01 迁移记录（治理与维护页 + 全站芝麻字号）：
# 起点是「治理与维护」页看起来被挤成芝麻——它的字号/间距/圆角**全部写死**
# （218 处 font-size，其中 59 处是 7px/8px），而且既不在 MIGRATED 也不在
# OTHERS 里，所以从来没被判过：名单外才是真正的盲区。这一轮把三个名单
# 一起改回事实，并补上一条**不看名单**的判定（见【二】）。
#
# 收进令牌的数量（工具报的"已迁移"数 + 手工收口数）：
#     maintenance-page.css   字号 218  间距 430  圆角 60
#     styles.css             字号 346  间距 551  圆角 53
#     output-page.css        字号 61   间距 158  圆角 0
#     home.css               字号 52   间距 61   圆角 17
#     navigation.css         字号 6    间距 29   圆角 4
#     workspace-theme.css    字号 11   间距 18   圆角 5
#     expression-config.css  字号 3    间距 1    圆角 0（+ --fs-small → --fs-body）
#     state-page.css         字号 1    间距 3
#     semantic-page.css      字号 7    间距 0
#     setup-guide.css        间距 3    （column-gap/row-gap，工具正则本来就漏）
#     workbench.css          字号 32   间距 68   圆角 10
#     config-editor.css      字号 5    间距 5    圆角 1
#
# 这一轮之后 OTHERS 只剩 shell.css：全站 30 个 .css 里 29 个零写死。
# （此前这里是 31 个，多出来的一个是 0 字节的 perception-page-shell.css：
#  它不在 index.html 的引用列表里——那句注释是过期结论——已随本轮清理删除。）
#
# 三处工具自身的问题，这一轮一并修掉（不是"手工绕过"）：
#   · migrate_page_scale.py 的 FONT 表只覆盖 9–36px，两端（7/8 与 38–46）
#     落进"未处理"，看起来像实测值，实际是机械缺口——已按角色并入
#     micro 与 hero/display，表本身没有新增档位。
#   · 同一个脚本的 SPACING_PROP 正则不含 column-gap / row-gap，而审计的
#     SPACING 集合**包含**它们：工具静默漏迁、审计照常判红，setup-guide.css
#     与 workspace-theme.css 的 4 处就是这么漏下来的。这里改写口，没动正则
#     （正则一改，之前几页的迁移口径就跟着变，那是另一件事）。
#   · `var(--r-sm,6px)` 这种"给已声明令牌写 fallback"的写法会被算成写死值。
#     这些令牌由 tokens.css 的 `:root` 无条件声明，fallback 永远不会生效，
#     留着只是让审计看见一个 6px。styles.css 里的 5 处改成了
#     `var(--r-sm,var(--r-xs))`——保留"取不到就退一档"的原意，但不留 px
#     （改成不带 fallback 的 var(--r-sm) 也一样过审计，只是会丢掉那层退档）。
#     shell.css 里同类 8 处**没有动**：它还没迁干净（见 OTHERS），
#     单独改这 8 处只会让这一页的写法半新半旧。
#
# workbench.css 这一页顺带记一笔：它是【三之二】自发现逼出来的最后一个案例。
# 有人把 6 处 font-size:10px 换成 var(--fs-micro)（正是【二】要求的下界），
# 于是它立刻按"用了 --fs-* 却不在名单里"被抓——顺序与 2026-09-30 的 console.css
# 一样。处置仍是先迁干净（字号 32、间距 68、圆角 10）再进名单。
# 它的字段网格语义（.wb-fields 的 auto-fit/minmax(300px,1fr)、.wb-field 的三栏、
# 四条 :has(...) 通栏、760px 单列回落、.wb-number input 的 flex:0 1 148px）
# 是另一件事的修复成果，迁移只换数值不动形状，由
# tests/test_workbench_field_layout.cjs 5/5 守住。
#
# 没有吸附到档位的地方（都是实测对齐，写清理由，不是漏迁）：
#   · 维护页 `padding:0 0 var(--sp-2) 102px`——对齐上一行表格的时间列
#     （92px 列 + 10px 间距）。
#   · 外壳 `margin-left:92px` 与 `@media(min-width:1740px){padding-left:125px}`
#     ——对齐固定左轨道（`.rail{width:92px}`）本身，不是留白节奏。
#   · semantic-page.css 的 `margin-left:46px` 与 navigation.css 的旧轨道偏移
#     （158 → 160 的令牌和，差 2px）。
# 5 处都已并入 is_measured 的实测白名单，理由写在那个函数里。
#
# 这个审计量不到的两类写死值（**已知盲区**，不等于"已经干净"）：
#   · `font:700 10px/1.4 var(--numeric-font)` 这类 font 简写里的字号——
#     只查 font-size 属性就会整条漏掉；
#   · `padding-inline` / `margin-block` / `scroll-margin-*` 这些逻辑属性与滚动
#     属性——SPACING 集合里没有它们。
# 要收口得先扩集合，那会让现在绿着的页面立刻变红，所以先记在这里，
# 免得"零写死"被读成"整份文件一个 px 都没有"。
# 另外【二】只看 --fs-* 的基准值：`font:700 10px/…` 简写、以及 JS 里
# 用内联样式设置的小字号，都不在它的视野里（前者见上一条，后者不由 CSS 审计管）。
#
# 上一版这份名单的教训（留着，别重犯）：models/state/semantic 三页是被人**批量
# 加进来**的，并没有跟着迁移，实测写死数是 4+1 / 13+4 / 3+35+9，于是被移出去。
# 处置是**把名单改回事实**，而不是放宽阈值或删掉告警：一份声称"已迁移"却带着
# 35 处写死的名单，比一份短而准的名单更坏——它会让人以为这些页已经有门槛守着了。
# 迁完一项移一项，别一次移一批。
#
# 更早的记录（仍有效）：
#
# models-page.css 曾是这里第一个"半迁移"的案例：它已经在用 --fs-*，按【三之二】
# 本该进 MIGRATED，但圆角还写死 30 处，一进名单审计就红——于是它留在 OTHERS 里，
# 等于拿到了一条绕过阈值的口子。当时的处置是**不**把名单改宽去消警（那等于既修了
# 告警又留下 30 处写死），而是先迁圆角。2026-09-30 圆角迁完（tools/
# migrate_models_page_radius.py，50 处收进 --r-*），这一页进了 MIGRATED；
# 同一天 21:16 有人又把 4 处间距与 1 处圆角写了回来，这次一并收回。
#
# 2026-09-30 console.css 与 backup-page.css 迁入，两个都是自发现逼出来的：
#   · console.css 原本一处令牌都没有，我加卷首时写了 var(--fs-*)，于是它按
#     "新写的页不用管"的豁免逻辑被抓——这正是自发现要防的入口。
#   · backup-page.css 更值得记：它**没在 MIGRATED 名单里**（我当时记错了），
#     却已经用了 --fs-*，属于名单与事实脱节的半迁移。
# 两页都是先迁干净（字号 13+10 处、间距 4+10 处）再进名单，没有改宽名单消警。
OTHERS = ()

# 允许写死的情形：这些不是"节奏"，是不同的东西。
#   ≤3px        —— 描边、微调、1px 分隔线，不构成节奏
#   50% / 999px —— 圆形与胶囊，语义明确，不需要档位
#   calc(...)   —— 参与几何计算的值，取整会错位
#   em / %      —— 相对长度，跟着字号走
#
# MEASURED 是左侧缩进的**实测值白名单**：这些数值对应某个可见的对齐关系
# （项目符号离文字、卡片离轨道线），吸附到最近档位会让元素错位。加值前先
# 在 is_measured 的说明里写清它对齐的是谁——没有理由的不许进。
MEASURED = re.compile(r"(?:^|\s)(?:19|26|43|44|46|48|92|102|125)px\s*$")

# 按页判定的**唯一**入口：所有遍历 pages/*.css 的检查都从这里取文件。
#
# 生成物（GENERATED_CSS）是把源文件拼接后的副本，源文件里有的声明它一条不少。
# 让它参与判定不会多发现一个缺陷，只会把同一个问题报两次——
# 2026-11 实测：【三之三】只跳过了 SNAPSHOT_CSS，于是
#   ✗ bundle.css 里的 var(--fs-2)
#   ✗ decision-page.css 里的 var(--fs-2)
# 并列出现，而 bundle.css 的那一行正是 decision-page.css 那一行的副本。
# 重复报告比漏报更贵：读的人要先花时间确认"这是两处还是一处"，
# 确认完还得记住哪一条是可以忽略的。
#
# 【三】与【三之二】本来就各自写了 GENERATED_CSS 的排除，【二】写了两条，
# 【三之三】漏了一条——四处各写一遍，漏一处就没有任何东西会发现。
# 收敛成这个函数之后，"生成物不参与判定"只有一处定义。
def page_sources():
    for path in sorted(PAGES.glob("*.css")):
        if path.name in SNAPSHOT_CSS or path.name in GENERATED_CSS:
            continue
        yield path


def is_measured(prop, value):
    """左侧缩进：悬挂缩进与轨道缩进，数值来自实测而非版式节奏。

    `padding-left:19px`（列表项目符号离文字多远）、
    `margin:var(--sp-2) 0 0 44px`（卡片离轨道线多远）。
    这些吸附到最近的 --sp-* 会让符号与文字错位，所以豁免。
    只作用于左侧——顶/右侧的留白属于节奏，照常要求令牌。

    2026-10-01 加入 102：维护页的 `.maint-call-detail{padding:0 0 var(--sp-2) 102px}`
    对齐的是它上面那一行 `.maint-call-row summary` 的**时间列**——
    `grid-template-columns:92px …; gap:10px`，92+10=102。展开行的细节因此与
    "时间"那一列的文字起始线对齐，不是留白节奏。同一选择器在 740px 断点
    把左侧收回 0（`padding-left:0`），说明它只在宽屏成立，更像实测的轨道。
    吸附到 --sp-7(40) 或 --sp-8(54) 都会让细节比上方文字左移近 50px，
    一眼能看出两行没对齐——这正是"实测值不许硬吸附"要防的情形。

    2026-10-01 同日加入 92 与 125，两者对齐的都是**左轨道本身**：
    · `.rail{width:92px}` 是固定左栏的宽度，`.shell{margin-left:92px}` 让主区
      从轨道右缘开始。这不是留白，是"绕开一个固定定位的兄弟元素"。
      把 92 吸附到 --sp-8(54) 会让主区压在轨道底下 38px，内容被永久遮住。
    · `@media(min-width:1740px){.shell{padding-left:125px}}` 是同一条轨道在
      "主区居中"之后的内缩补偿（`margin-left:auto` 之后不能再用 margin 让位）。
      --sp-8(54) 会让居中块整体左移 71px，同样压到轨道上。
    两者都是**几何对齐**而非节奏，与上面 102 同类；且都只在宽屏成立
    （`.rail` 在 650px 断点变成贴底横条，两条声明随之失效）。
    """
    if prop not in ("padding-left", "margin-left", "padding", "margin"):
        return False
    values = value.split()
    if len(values) == 1:
        left = values[0] if prop.endswith("-left") else None
    else:
        left = values[-1]          # 4 值形态：上 右 下 左
    return bool(left and MEASURED.search(left))


def is_exempt(prop, value):
    if prop.startswith("border") and not prop.startswith("border-radius"):
        return True
    if value.strip() in ("0", "auto", "none", "initial", "inherit"):
        return True
    if "50%" in value or "999px" in value or "calc(" in value:
        return True
    if re.search(r"var\(--(?!sp-|r-|fs-)", value):
        return True
    if is_measured(prop, value):
        return True
    return False


def decls(css, props):
    """取某批属性的声明，返回 (属性, 值) 列表。跳过注释。"""
    css = re.sub(r"/\*.*?\*/", " ", css, flags=re.S)
    out = []
    for m in re.finditer(r"([a-z-]+)\s*:\s*([^;{}]+)", css):
        if m.group(1) in props:
            out.append((m.group(1), m.group(2).strip()))
    return out


def hard_px(prop, value):
    """返回这条声明里未走令牌的 px 数值（排除豁免）。"""
    if is_exempt(prop, value):
        return []
    found = []
    for m in re.finditer(r"(\d+(?:\.\d+)?)px", value):
        v = float(m.group(1))
        if v <= 3:
            continue
        found.append(v)
    return found


SPACING = {"padding", "padding-top", "padding-right", "padding-bottom", "padding-left",
           "margin", "margin-top", "margin-right", "margin-bottom", "margin-left",
           "gap", "row-gap", "column-gap"}
RADIUS = {"border-radius"}
FONT = {"font-size"}

# 比最小档还小的字号（【二】）的判定口径。
#
# "绝对 px"指只有一个数值加 px。calc()/clamp()/min()/max() 里的 px 不做绝对值
# 判断：那些值由多个量共同决定（流体排版、按容器伸缩），脱离上下文算出来的
# 大小与屏幕上看到的不是一回事，硬判会产生误报——而误报会让人开始无视这条。
# 相对单位（em/rem/%/ch）与视口单位（vw/vh/vmin/vmax）同样是相对量：它们跟着
# 字号偏好或视口走，本来就"没有固定大小"。
# 显式豁免 font-size:0：那不是"最小的字号"，是折叠文本节点的排版手法
# （文字不占位、元素照常参与布局，semantic-page.css 的窄屏脉冲点就靠它），
# 与 audit_page() 里的字号豁免同一条规则。
NON_ABSOLUTE_FONT = re.compile(r"calc\(|clamp\(|min\(|max\(|env\(|\d(?:em|rem|%|ch|ex|vw|vh|vmin|vmax)\b")
ZERO_FONT = {"0", "0px", "0%", "inherit", "initial", "unset", "revert"}
ABSOLUTE_PX = re.compile(r"^(\d+(?:\.\d+)?)px$")


def minimum_font_px():
    """从 tokens.css 读出最小字号档位（--fs-micro）的基准值。

    阈值必须从令牌算出来而不是写死 11：档位改了（比如 micro 从 10.5 提到 12），
    判定要跟着改。写死数字的阈值会在档位调整的那一天悄悄变成"永远通过"——
    那比没有这条检查更坏，因为它还在报告里打勾。

    返回 None 表示读不到：这种情况下判定会直接失败，而不是退化成默认值。
    """
    css = (PAGES / "tokens.css").read_text(encoding="utf-8")
    match = re.search(r"--fs-micro\s*:\s*calc\(\s*([\d.]+)px", css)
    if not match:
        match = re.search(r"--fs-micro\s*:\s*([\d.]+)px", css)
    return float(match.group(1)) if match else None


def audit_font_floor(path, floor):
    """返回这一页所有"低于最小档位"的绝对 font-size 声明。"""
    text = re.sub(r"/\*.*?\*/", " ", path.read_text(encoding="utf-8"), flags=re.S)
    found = []
    for match in re.finditer(r"font-size\s*:\s*([^;{}]+)", text):
        value = match.group(1).strip()
        if value.lower() in ZERO_FONT or NON_ABSOLUTE_FONT.search(value):
            continue
        size = ABSOLUTE_PX.match(value)
        if size and float(size.group(1)) < floor:
            found.append(value)
    return found


def audit_page(path):
    css = path.read_text(encoding="utf-8")
    report = {"font": [], "space": [], "radius": []}
    for prop, value in decls(css, FONT):
        if "var(--fs-" in value:
            continue
        # font-size:0 不是字号档位，是折叠文本节点的排版手法：文字不占位，
        # 元素本身照常参与布局（semantic-page.css 的窄屏脉冲点就靠它把标签藏掉、
        # 只留三个圆点）。吸附到任何 --fs-* 都会把那行字重新显示出来。
        # is_exempt 早就把 "0" 列为豁免，只是这里原先没走那条规则——
        # 于是间距与圆角放行、字号单独判红，同一类值两套标准。
        if value.strip() in ("0", "0px", "inherit", "initial", "unset"):
            continue
        if "calc(" in value or "em" in value or "%" in value:
            continue
        report["font"].append((prop, value))
    for prop, value in decls(css, SPACING):
        if hard_px(prop, value):
            report["space"].append((prop, value))
    for prop, value in decls(css, RADIUS):
        if hard_px(prop, value):
            report["radius"].append((prop, value))
    return report


def scale_sources():
    """找出所有声明尺度令牌的文件（应当只有 tokens.css）。"""
    owners = {}
    for path in page_sources():
        text = re.sub(r"/\*.*?\*/", " ", path.read_text(encoding="utf-8"), flags=re.S)
        # 只看自定义属性定义处（--x: ...），不看引用 var(--x)
        defs = set(re.findall(r"(--(?:r|sp|fs)-[a-z0-9]+)\s*:", text))
        if defs:
            owners[path.name] = defs
    return owners


def font_scale_gaps():
    """读 tokens.css 的 --fs-* 基准值，报告相邻档间距。"""
    css = (PAGES / "tokens.css").read_text(encoding="utf-8")
    pairs = re.findall(r"--fs-([a-z]+):calc\(([\d.]+)px", css)
    order = ["micro", "tiny", "body", "lead", "title", "head", "display", "hero"]
    values = [(n, float(v)) for n, v in pairs]
    values.sort(key=lambda p: order.index(p[0]) if p[0] in order else 99)
    return values


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    failures = []
    # 参与过按页判定的文件名。生成物快照永远不该出现在这里（见【六】）。
    judged: set[str] = set()

    print("=" * 70)
    print("拓展页尺度审计")
    print("=" * 70)

    print("\n【一】已迁移页面必须零写死")
    for name in MIGRATED:
        judged.add(name)
        path = PAGES / name
        if not path.exists():
            failures.append("缺少 %s" % name)
            print("  ? %s 不存在" % name)
            continue
        r = audit_page(path)
        bad = len(r["font"]) + len(r["space"]) + len(r["radius"])
        mark = "✓" if bad == 0 else "✗"
        print("  %s %-20s 字号 %d  间距 %d  圆角 %d"
              % (mark, name, len(r["font"]), len(r["space"]), len(r["radius"])))
        if bad:
            failures.append("%s 有 %d 处写死尺度" % (name, bad))
            if args.verbose:
                for kind in ("font", "space", "radius"):
                    for prop, value in r[kind][:6]:
                        print("        %-14s %s:%s" % (kind, prop, value))

    print("\n【二】任何页面都不得声明低于最小档位的字号")
    # 这一条与【一】的区别是**不看名单**：下界是绝对约束，不是"已迁移页才要守"
    # 的额度。名单里没有的页面（还带着自有间距/圆角的旧页）同样要守这条，
    # 否则"字号有下界"就只是已迁移页的局部事实。
    floor = minimum_font_px()
    if floor is None:
        failures.append("读不到 tokens.css 的 --fs-micro，无法判定字号下界")
        print("  ✗ tokens.css 里找不到 --fs-micro，判定不了下界")
    else:
        print("      最小档 --fs-micro = %gpx（阈值从这里读出，不写死）" % floor)
        floor_hits = []
        for path in page_sources():
            judged.add(path.name)
            hits = audit_font_floor(path, floor)
            if hits:
                floor_hits.append((path.name, hits))
        for name, hits in floor_hits:
            failures.append("%s 有 %d 处字号低于最小档位（< %gpx）"
                            % (name, len(hits), floor))
            print("  ✗ %-22s %d 处：%s"
                  % (name, len(hits), "、".join(sorted(set(hits)))))
        if not floor_hits:
            print("  ✓ 没有页面声明低于最小档位的字号")

    print("\n【三】尺度令牌只能有一个源")
    owners = scale_sources()
    for name, defs in owners.items():
        judged.add(name)
        mark = "✓" if name == "tokens.css" else "✗"
        print("  %s %-22s 声明 %d 个尺度令牌" % (mark, name, len(defs)))
        if name != "tokens.css":
            failures.append("%s 重复声明尺度令牌（会静默覆盖 tokens.css）" % name)

    print("\n【三之二】凡已使用字号令牌的页面都必须受判定")
    # 自发现：新页面一旦用上 --fs-*，就该同时进入 MIGRATED，
    # 否则它会绕过【一】的阈值检查——"新写的页不用管"正是尺度再次失控的入口。
    # 令牌定义本身（tokens.css）与只提到名字的注释不算「使用」。
    TOKEN_FILES = {"tokens.css"}
    for path in page_sources():
        if path.name in MIGRATED or path.name in TOKEN_FILES:
            continue
        judged.add(path.name)
        text = re.sub(r"/\*.*?\*/", " ", path.read_text(encoding="utf-8"), flags=re.S)
        if re.search(r"var\(--fs-", text):
            failures.append("%s 使用了 --fs-* 但不在 MIGRATED 名单里" % path.name)
            print("  ✗ %-22s 已用字号令牌，却未纳入阈值判定" % path.name)
    if not any("不在 MIGRATED" in f for f in failures):
        print("  ✓ 所有使用字号令牌的页面都已在判定名单内")

    print("\n【三之三】尺度令牌的引用必须真有声明")
    # 这一类缺陷已经咬过两次，且两次都是**静默**的：
    #   · `var(--fs-1,13px)` —— --fs-1 不存在（令牌改名叫语义档位了），
    #     一直吃 fallback 的 13px，于是字号偏好与密度两维对这一行完全无效，
    #     但屏幕上显示正常，没人会去查。
    #   · `var(--dur-base, .26s ease)` —— 同样不存在，13 处全靠 fallback 撑着。
    # 共同点是「写了 fallback」：fallback 让拼错的令牌照常渲染。
    #
    # 只查**尺度命名空间**（--fs-/--sp-/--r-）。这三个是我们自己的词表，
    # 写错就是写错；而 --i/--delay/--fill 那些是组件级小属性，
    # 大量由 JS 用 setProperty 内联设置（见 entrance.js 的 ring.style.setProperty），
    # 静态扫描看不见定义，一并检查会产出一屏误报，把真问题淹掉。
    declared_scale = set()
    for source in ("tokens.css", "tokens-bridge.css"):
        path = PAGES / source
        if path.exists():
            text = re.sub(r"/\*.*?\*/", " ", path.read_text(encoding="utf-8"), flags=re.S)
            declared_scale |= set(re.findall(r"(--(?:fs|sp|r)-[a-z0-9]+)\s*:", text))
    dangling = []
    for path in page_sources():
        judged.add(path.name)
        text = re.sub(r"/\*.*?\*/", " ", path.read_text(encoding="utf-8"), flags=re.S)
        local = set(re.findall(r"(--(?:fs|sp|r)-[a-z0-9]+)\s*:", text))
        for name in sorted(set(re.findall(r"var\(\s*(--(?:fs|sp|r)-[a-z0-9]+)", text)) - declared_scale - local):
            dangling.append("%s 里的 var(%s)" % (path.name, name))
    if dangling:
        failures.append("引用了未声明的尺度令牌（会静默吃 fallback）：" + "；".join(dangling))
        for item in dangling:
            print("  ✗ " + item)
    else:
        print("  ✓ 尺度令牌引用全部有声明（共 %d 个声明）" % len(declared_scale))

    print("\n【四】字号档位的相邻间距（应 ≥1px，否则两档读起来是同一级）")
    values = font_scale_gaps()
    for name, px in values:
        print("      --fs-%-9s %5.1fpx" % (name, px))
    gaps = [(b[1] - a[1], a[0], b[0]) for a, b in zip(values, values[1:])]
    tight = [g for g in gaps if g[0] < 1.0]
    if tight:
        for gap, a, b in tight:
            failures.append("--fs-%s 与 --fs-%s 只差 %.1fpx" % (a, b, gap))
        print("  ✗ 有 %d 对档位间距不足 1px" % len(tight))
    else:
        print("  ✓ 最小相邻间距 %.1fpx（%s → %s）"
              % (min(gaps)[0], min(gaps)[1], min(gaps)[2]))

    print("\n【五】尚未迁移的页面（只统计，不作判定；字号下界仍由【二】判定）")
    for name in OTHERS:
        judged.add(name)
    pending = [(n, len(audit_page(PAGES / n)["font"]),
                len(audit_page(PAGES / n)["space"])) for n in OTHERS if (PAGES / n).exists()]
    pending.sort(key=lambda t: -(t[1] + t[2]))
    for name, f, s in pending[:8]:
        print("      %-22s 字号 %-3d 间距 %-3d  合计 %d" % (name, f, s, f + s))
    rest = sum(f + s for _, f, s in pending[8:])
    print("      （其余 %d 个文件合计 %d 处）" % (max(0, len(pending) - 8), rest))

    print("\n【六】审计自身卫生：生成物快照不得进按页报告")
    # 这一条检查的不是页面，而是上面五节**取文件的口径**。
    # 生成物快照（bundle.css）是源文件拼接后的副本，同一条声明在它和源文件里
    # 各有一份：哪一节忘了排除，报告里就会并列出现
    #   ✗ bundle.css 里的 var(--fs-2)
    #   ✗ decision-page.css 里的 var(--fs-2)
    # 看着像两处缺陷，实际只有一处。【三之三】漏掉 GENERATED_CSS 时就是这样。
    # 过滤本身在 page_sources 里做；这里只断言没有任何一节把生成物带进判定，
    # 将来新增一节却忘了走那个入口，会在这一行当场变红。
    artifacts = SNAPSHOT_CSS | GENERATED_CSS
    leaked = sorted(judged & artifacts)
    if leaked:
        failures.append("生成物快照参与了按页判定（同一处问题会被报两次）：" + "、".join(leaked))
        for name in leaked:
            print("  ✗ %s 是生成物，不该进按页判定" % name)
    elif not any((PAGES / name).exists() for name in artifacts):
        print("  （生成物快照不在树里，没有可验证的对象）")
    else:
        print("  ✓ 生成物快照（%s）不参与按页判定，%d 个源文件在判定范围内"
              % ("、".join(sorted(artifacts)), len(list(page_sources()))))

    print()
    if failures:
        print("失败：%d 项" % len(failures))
        for item in failures:
            print("  ✗ %s" % item)
        return 1
    print("尺度审计通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
