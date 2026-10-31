#!/usr/bin/env python3
"""页面配置覆盖面自检：后端注册表 vs 前端 config-editor.js 的 spec。

用途：每次往任何配置组加字段后跑一次，确认页面确实声明了这个字段。

为什么需要它：纯 grep 会漏两类情况——
  ①页面用字符串拼接字段名（`"cycle_"+key+"_days"`），grep 抓不到字面量；
  ②模块映射写错（`life` 的字段其实在 `role_work.life.configuration`，
     不再是它早期以为的那个模块），会整组漏判。
本脚本直接 import 各模块的 `configuration_fields()` 拿真实短名。

用法：python3 tools/audit_page_config_coverage.py
退出码：0 全部覆盖；1 有缺口（逐组打印）。
"""
import importlib
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# 专用编辑器的控件声明写在页面自己的 inputs 映射里。这里只匹配字面量
# `name: "word-list"`；工作台内核里的 `item.input === "word-list"` 判断不在
# 这批页面里，不会误收。
WORD_LIST_DECLARATION = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*\"word-list\"")

# 分区 → 注册表所在模块。**加分区时这里也要加**，否则该组完全不参与自检。
MODULES = {
    "quota": "companion_v2.conversation.media.quota",
    "image_review": "companion_v2.conversation.media.review",
    "busy_gate": "companion_v2.conversation.busy_gate",
    "reaction": "companion_v2.expression.reaction_configuration",
    "budget": "companion_v2.model_gateway.budget",
    "recall": "companion_v2.conversation.recall_settings",
    "screen": "companion_v2.screen.configuration",
    "person": "companion_v2.memory.person.configuration",
    "person.learning": "companion_v2.memory.person.configuration:learning_configuration_fields",
    # 私聊用户权限的默认值与拒绝文案；按人的权限本身是 grant，不在这张表里。
    "private_access": "companion_v2.relationships.private_access",
    "role_growth": "companion_v2.role_work.growth.configuration",
    # 拆分出来的技能组要单列，而且必须指到它自己的字段提供函数：该模块的
    # `configuration_fields()` 只返回权限组，用它比对会把两个权限开关当成
    # 技能字段，报出一串并不存在的「缺控件」。
    "role.growth.skills": "companion_v2.role_work.growth.configuration:skill_configuration_fields",
    "knowledge": "companion_v2.knowledge.configuration",
    "life": "companion_v2.role_work.life.configuration",
    "library": "companion_v2.library.configuration",
    "wardrobe": "companion_v2.references.wardrobe",
    "photo.reference": "companion_v2.references.configuration",
    # QQ 空间拓展的角色级开关与节流；安装与逐会话同意不在这张表里（归能力安装路由）。
    "qzone": "companion_v2.qzone.configuration",
    "group_participation": "companion_v2.conversation.participation",
    # 唯一一个四层组（安装/角色/关系/窗口），160 个字段；此前不在表里，
    # 于是整组不参与自检——92 项没有控件的字段也一直是「通过」。
    "proactive.contact": "companion_v2.proactive.configuration",
    # 以下六个分区此前有 spec、却不在表里，等于整组没有断言：漏了控件也不会变红。
    "image.output": "companion_v2.conversation.media.output_settings",
    "expression.learning": "companion_v2.expression.configuration",
    "role_profile": "companion_v2.role_profile.configuration",
    "relationship_profile": "companion_v2.relationships.profile",
    # 这一组的字段分在两个模块里：正文方向在 authoring_service，封面选项在
    # cover_assets。只指一个模块会漏掉另一半，所以用 cover_assets 的整组访问器。
    "content.authoring": "companion_v2.conversation.content.cover_assets:group_fields",
}

# 控件不在 config-editor 的 spec 里、而由各页面自己的字段表渲染的分区。
# 这里断言「承接页面还在，且至少有一个字段名出现在里面」——页面被改名或删掉时
# 会变红，但受限于各页面的动态拼名，不做逐字段断言（逐字段的现状见
# docs/CONFIG_SURFACE_GAPS_20261017.md）。
DEDICATED_EDITORS = {
    "memory": {"module": "companion_v2.memory.configuration",
               "pages": ("pages/陪伴小屋/memory-settings-section.js",)},
    "conversation.presentation": {"module": "companion_v2.conversation.reply_settings",
                                  "pages": ("pages/陪伴小屋/reply-page.js",)},
    "conversation.perception": {"module": "companion_v2.conversation.perception_settings",
                                "pages": ("pages/陪伴小屋/perception-native-page.js",)},
    "role.state": {"module": "companion_v2.role_work.state.configuration",
                   "pages": ("pages/陪伴小屋/state-maintenance.js",
                             "pages/陪伴小屋/state-editor.js",
                             "pages/陪伴小屋/state-page.js")},
    "expression.learning.group": {"module": "companion_v2.expression.configuration:group_configuration_fields",
                                  "pages": ("pages/陪伴小屋/expression-page.js",)},
    "expression.learning.private": {"module": "companion_v2.expression.configuration:private_configuration_fields",
                                    "pages": ("pages/陪伴小屋/expression-page.js",)},
    "expression.learning.role": {"module": "companion_v2.expression.configuration:role_scope_configuration_fields",
                                 "pages": ("pages/陪伴小屋/config-editor.js",),
                                 # 页面把这一组落成带 expression_ 前缀的同名控件
                                 # （expression_private_source_mode …），按前缀放行。
                                 "prefixes": ("expression_",)},
    "role.growth": {"module": "companion_v2.role_work.growth.configuration",
                    "pages": ("pages/陪伴小屋/learning-page.js",)},
    "person.learning": {"module": "companion_v2.memory.person.configuration:learning_configuration_fields",
                        "pages": ("pages/关系与权限/member-editor.js",)},
}

# 有意不接的字段：值写明理由。**不要**为了「让自检通过」把它们加进页面，
# 也不要为了消掉噪音把它们从这里删掉——两件事都会掩盖真实的疏漏。
INTENTIONAL = {
    "proactive.contact": {
        "important_dates": "旧版手动日期清单仅保留后端兼容；页面不再要求填写，生日与纪念日自动读取角色和当前私聊资料",
    },
    "knowledge": {
        "import_enabled": "旧世界书导入设置保留在后端合同中；当前页面只管理官方知识库来源",
        "import_paths": "当前页面不接收原始文件路径，避免保存后没有可用的导入管理入口",
    },
    "role.state": {
        "mood_cross_window": "跨窗口共享，操作员难以判断实际影响",
        "mood_interest_window_minutes": "与更新概率高度耦合，单独调反而更难预期",
        "mood_neutral_band": "偏玄学，默认值已够用",
    },
    # 这两个开关按范围单独维护：私聊的在 expression-page.js 的「私聊学习」段，
    # 群聊的在「本群学习」段，属主是下面的 .private / .group 两个子分区。
    "expression.learning": {
        "private_learning_enabled": "由表达页「私聊学习」段维护（expression.learning.private）",
        "group_learning_enabled": "由表达页「本群学习」段维护（expression.learning.group）",
    },
    "role.life": {},
}

# 在其他页面单独维护的字段。审计时确认它们仍由指定页面承接，避免把
# 拆分后的成员编辑器误报成通用工作台缺少控件。
EXTERNAL_CONTROLS = {
    "role_growth": {
        "read_enabled": "pages/陪伴小屋/learning-page.js",
        "proposals_enabled": "pages/陪伴小屋/learning-page.js",
    },
    "person.learning": {
        "acquaintance_learning_enabled": "pages/关系与权限/member-editor.js",
    },
}


def page_spec():
    """在前端环境里真的加载一次，取到的就是运行时用的那份 spec。"""
    script = """
const fs=require('fs'),vm=require('vm');
const root='pages/陪伴小屋/';
const c=vm.createContext({window:{},document:{},sessionStorage:{},Intl,Date,Object});
vm.runInContext(fs.readFileSync(root+'workbench-page.js','utf8'),c);
vm.runInContext(fs.readFileSync(root+'config-editor.js','utf8'),c);
process.stdout.write(JSON.stringify(c.window.CompanionConfigEditors.spec));
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True,
                            encoding="utf-8")
    if result.returncode != 0:
        raise SystemExit("前端 spec 加载失败（先确认 workbench-page.js 在 config-editor.js 之前）:\n"
                         + result.stderr)
    return json.loads(result.stdout)


def backend_fields(module_name):
    """该模块声明的字段短名与注册默认值，按 field_id 去掉 group_id 前缀切。

    模块名可以带 ``:函数名``。一个模块里可能有多个字段提供函数（技能组就是从
    ``growth.configuration`` 拆出去的），而 ``configuration_fields()`` 只返回其中
    一组；不指明函数就会拿错组去比对，报出一串并不存在的「缺控件」。
    """
    target, _, factory = module_name.partition(":")
    module = importlib.import_module(target)
    factory = factory or "configuration_fields"
    try:
        fields = getattr(module, factory)()
    except TypeError:
        # reply_settings 的字段随分段策略与流式开关变化，必须传参才能展开；
        # 这里用出厂默认值取全量，和 tools/audit_configuration_scopes.py 一致。
        if target != "companion_v2.conversation.reply_settings":
            raise
        from companion_v2.conversation.segmentation import SegmentationPolicy
        fields = getattr(module, factory)(SegmentationPolicy(), False, 0.5)
        if isinstance(fields, tuple) and fields and not hasattr(fields[0], "field_id"):
            fields = fields[0]
    if not fields:
        return None, set(), {}
    group = fields[0].group_id
    by_name = {field.field_id.removeprefix(group + "."): field for field in fields}
    return group, set(by_name), {name: field.default for name, field in by_name.items()}


def word_list_parsed(values):
    """让页面自己的解析器判断注册默认值能读成几个词条。

    这里不重写一遍解析规则：回归要守的是「编辑器真的读得懂」，而不是审计脚本
    和前端各写一份、一起写错还互相点头。返回每项的解析结果（列表或 None）。
    """
    script = """
const fs=require('fs'),vm=require('vm');
const root='pages/陪伴小屋/';
const c=vm.createContext({window:{},document:{},sessionStorage:{},Intl,Date,Object});
vm.runInContext(fs.readFileSync(root+'workbench-page.js','utf8'),c);
const values=JSON.parse(process.argv[1]);
process.stdout.write(JSON.stringify(values.map(value => c.window.CompanionWorkbench.parseStringList(value))));
"""
    result = subprocess.run(["node", "-e", script, json.dumps(values, ensure_ascii=False)],
                            cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        raise SystemExit("前端 parseStringList 加载失败:\n" + result.stderr)
    return json.loads(result.stdout)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.path.insert(0, str(ROOT))
    spec = page_spec()
    problems = []
    counted = {}

    missing_sections = [kind for kind in MODULES if kind not in spec and kind not in EXTERNAL_CONTROLS]
    for kind in missing_sections:
        problems.append(f"分区 {kind} 在注册表里有、前端 spec 里没有")

    # 专用编辑器：页面在不在、字段名还在不在。不做逐字段断言（见 DEDICATED_EDITORS 注释）。
    # role.growth / person.learning 在 MODULES 里已经按字段断言过，这里只补页面存在性，
    # 不重复计数（否则总数会比注册表多出这几项）。
    asserted_groups = set()
    for module_name in MODULES.values():
        group, short, _ = backend_fields(module_name)
        if short:
            asserted_groups.add(group)
    for kind, entry in DEDICATED_EDITORS.items():
        group, short, _ = backend_fields(entry["module"])
        if not short:
            problems.append(f"分区 {kind}: {entry['module']} 没声明字段")
            continue
        if group not in asserted_groups:
            counted[kind] = short
            asserted_groups.add(group)
        pages, sources = entry["pages"], []
        for page in pages:
            path = ROOT / page
            if not path.is_file():
                problems.append(f"分区 {kind} ({group}): 承接页面 {page} 不存在")
                continue
            sources.append(path.read_text(encoding="utf-8"))
        wanted = {prefix + name for name in short for prefix in entry.get("prefixes", ("",))}
        if sources and not any(candidate in "\n".join(sources) for candidate in wanted):
            problems.append(f"分区 {kind} ({group}): 承接页面里找不到任何字段名，控件可能已被删掉")

    for kind, module_name in MODULES.items():
        group, short, _ = backend_fields(module_name)
        if not short:
            problems.append(f"分区 {kind}: {module_name} 没声明字段")
            continue
        counted[kind] = short
        external = EXTERNAL_CONTROLS.get(kind, {})
        for name, owner in external.items():
            owner_path = ROOT / owner
            source = owner_path.read_text(encoding="utf-8") if owner_path.is_file() else ""
            if name not in short or name not in source:
                problems.append(f"{kind} ({group}): 外部控件声明 {name} 未在 {owner} 中找到")
            if kind == "role_growth" and 'data-access="${field}"' not in source:
                problems.append(f"{kind} ({group}): {owner} 缺少专用授权控件绑定")
        excused = set(INTENTIONAL.get(group, {})) | (set(external) & short)

        if kind not in spec:
            for name in sorted(short - excused):
                problems.append(f"分区 {kind} ({group}): 字段 {name} 没有专用控件声明")
            continue

        entry = spec[kind]
        order = set(entry.get("order", []))
        labels = set(entry.get("labels", {}))
        sections = {name for section in entry.get("sections", []) for name in section["fields"]}

        for name in sorted(short - order - excused):
            problems.append(f"{kind} ({group}): 字段 {name} 没有控件声明（order 缺）")
        for name in sorted(short - labels - excused):
            problems.append(f"{kind} ({group}): 字段 {name} 没有标签（labels 缺）")
        if sections:
            for name in sorted(short - sections - excused):
                problems.append(f"{kind} ({group}): 字段 {name} 没落进任何段落（sections 缺）")

    # 词条编辑器（word-list）的注册默认值必须读得懂。后端允许换行/逗号/分号
    # 分隔的旧写法，前端若只认 JSON 数组，默认值会被读成 0 个词条、valid=false，
    # 整张卡连保存都会被拦（conversation.presentation 的
    # file_understanding_format_groups 就是这样漏掉的）。判断用页面自己的
    # parseStringList，不在这里重写解析规则。
    editors = [(kind, module_name, None) for kind, module_name in MODULES.items()]
    editors += [(kind, entry["module"], entry) for kind, entry in DEDICATED_EDITORS.items()]
    values, origins, seen_groups = [], [], set()
    for kind, module_name, entry in editors:
        group, _, defaults = backend_fields(module_name)
        if group in seen_groups:
            continue
        seen_groups.add(group)
        if kind in spec:
            names = [name for name, control in (spec[kind].get("inputs") or {}).items()
                     if control == "word-list"]
        else:
            pages = (entry or {}).get("pages", ())
            sources = [(ROOT / page).read_text(encoding="utf-8") for page in pages
                       if (ROOT / page).is_file()]
            names = sorted(set(WORD_LIST_DECLARATION.findall("\n".join(sources))))
        for name in names:
            value = defaults.get(name)
            if not isinstance(value, str) or not value:
                continue
            values.append(value)
            origins.append(f"{group}.{name}")
    if values:
        for origin, words in zip(origins, word_list_parsed(values)):
            if words is None:
                problems.append(f"{origin}: word-list 默认值编辑器解析不了"
                                "（会显示 0 个词条并拦住整张卡保存）")

    if problems:
        print(f"发现 {len(problems)} 处覆盖缺口：")
        for item in problems:
            print("  -", item)
        return 1
    total = sum(len(names) for names in counted.values())
    excused = sum(len(group) for group in INTENTIONAL.values())
    external = sum(len(group) for group in EXTERNAL_CONTROLS.values())
    dedicated = sum(len(counted[kind]) for kind in DEDICATED_EDITORS if kind in counted)
    print(f"全部 {len(counted)} 个分区覆盖自检通过"
          f"（共 {total} 字段，其中 {excused} 项有意不接、{external} 项由其他页面维护、"
          f"{dedicated} 项由专用编辑器承接）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
