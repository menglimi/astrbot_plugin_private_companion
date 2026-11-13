"""Route familiar V1 function commands through V7's authorized core tools."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
import re

from .binding import message_text
from .native_extensions.request_context import set_system_instruction


@dataclass(frozen=True)
class FunctionalCommand:
    surface: str
    intent: str
    argument: str = ""


_ALIASES = {
    "private": {
        "帮助": "help", "help": "help",
        "状态": "status", "status": "status",
        "能力列表": "capabilities", "主动能力": "capabilities", "工具列表": "capabilities",
        "查看主动判定": "diagnostics", "主动判定": "diagnostics", "判定": "diagnostics",
        "答疑": "diagnostics", "排障": "diagnostics", "诊断": "diagnostics", "说明": "diagnostics",
        "答疑确认": "manage_diagnostics", "应用答疑建议": "manage_diagnostics",
        "答疑取消": "manage_diagnostics", "取消答疑建议": "manage_diagnostics",
        "答疑设置": "manage_diagnostics", "答疑修改": "manage_diagnostics",
        "查看今日日程": "calendar", "今日日程": "calendar", "查看日程": "calendar", "日程": "calendar",
        "删除日程": "calendar", "取消日程": "calendar", "移除日程": "calendar",
        "日期列表": "dates_list", "重要日期": "dates_list", "日期": "dates_list",
        "日期添加": "dates_add", "添加日期": "dates_add", "重要日期添加": "dates_add",
        "日期删除": "dates_remove", "删除日期": "dates_remove", "重要日期删除": "dates_remove",
        "探索笔记": "exploration_notes", "研究笔记": "exploration_notes", "最近探索": "exploration_notes",
        "探索目标": "exploration_notes",
        "日记": "timeline", "bot日记": "timeline", "小记": "timeline",
        "梦境": "timeline", "今日梦境": "timeline", "做了什么梦": "timeline",
        "梦境碎片": "timeline", "梦碎片": "timeline", "碎片梦境": "timeline",
        "可做事项": "calendar", "能做什么": "calendar",
        "记忆": "memory", "陪伴记忆": "memory", "长期记忆": "memory",
        "livingmemory": "memory", "lmem": "memory", "向量记忆": "memory",
        "片段": "memory", "对话片段": "memory", "共同经历": "memory",
        "未完成": "manage_memory", "未完话头": "manage_memory",
        "话头删除": "open_loop_delete", "删除话头": "open_loop_delete",
        "未完话头删除": "open_loop_delete", "删除未完话头": "open_loop_delete",
        "清空记忆": "memory_clear",
        "忘记我": "manage_memory",
        "画像": "person", "关系": "person", "回复率": "manage_efficiency",
        "气氛": "person", "意图": "person", "关系状态": "person",
        "画图": "image_generate", "生图": "image_generate", "陪伴画图": "image_generate",
        "自拍": "image_generate", "自拍一张": "image_generate",
        "合照": "image_edit", "陪伴合照": "image_edit",
        "改图": "image_edit", "修图": "image_edit", "重绘": "image_edit", "p图": "image_edit",
        "图任务": "image_tasks", "陪伴图任务": "image_tasks",
        "表达学习": "manage_expression", "说话风格": "manage_expression", "口癖": "manage_expression",
        "昵称": "manage_relationship", "称呼": "manage_relationship", "语气": "manage_relationship",
        "风格": "manage_relationship",
        "绑定主动消息": "manage_contact", "绑定主动会话": "manage_contact", "绑定会话": "manage_contact",
        "查看主动路由": "manage_contact", "查看主动绑定": "manage_contact", "主动路由": "manage_contact",
        "主动绑定": "manage_contact", "解绑主动消息": "manage_contact", "解绑主动会话": "manage_contact",
        "解绑会话": "manage_contact", "绑定城市": "manage_life", "设置城市": "manage_life",
        "查看城市": "manage_life", "当前城市": "manage_life", "天气城市": "manage_life",
        "解绑城市": "manage_life", "清除城市": "manage_life", "tts语种": "voice_language",
        "语音语种": "voice_language", "切换语种": "voice_language", "语种切换": "voice_language",
        "切换语言": "voice_language", "语言切换": "voice_language", "语种": "voice_language",
        "语言": "voice_language", "重置日程": "manage_life",
        "生成日记": "manage_life", "刷新日记": "manage_life",
        "撤回消息": "manage_group", "防撤回": "manage_group", "重置当前人格": "manage_host",
        "撤回转述": "manage_group", "转述撤回": "manage_group",
        "当前人格重置": "manage_host", "重置人格": "manage_host", "重置插件": "reset_all",
        "全部重置": "reset_all", "查看提示词": "manage_host", "提示词": "manage_host", "prompt": "manage_host",
        "增添状态": "manage_life", "添加状态": "manage_life", "生成状态": "manage_life",
        "刷新状态": "manage_life", "重生状态": "manage_life", "重置细化": "manage_life",
        "当前细化": "manage_life", "查看当前细化": "manage_life",
        "参考图": "manage_image", "参考图库": "manage_image", "人设参考图": "manage_image",
        "自拍参考图": "manage_image", "衣柜": "manage_image", "衣橱": "manage_image",
        "wardrobe": "manage_image", "角色衣柜": "manage_image", "服装库": "manage_image",
        "今日穿搭": "manage_image", "穿搭": "manage_image", "生成穿搭": "manage_image",
        "刷新穿搭": "manage_image", "重置穿搭": "manage_image", "今日穿搭图": "manage_image",
        "生图api": "manage_image", "查看生图api": "manage_image",
        "切换生图api": "manage_image", "交换生图api": "manage_image",
        "发说说": "manage_native", "发qq空间": "manage_native", "发布说说": "manage_native",
        "qq空间链路测试": "manage_native", "配图测试": "manage_native",
        "新闻": "manage_native", "今日新闻": "manage_native",
        "ai新闻": "manage_native", "ai日报": "manage_native", "日报": "manage_native", "早报": "manage_native",
        "现实触及": "manage_native", "起床闹钟": "manage_native", "起床提醒": "manage_native",
        "蓝牙起床": "manage_native", "蓝牙闹钟": "manage_native",
        "资料柜密码": "retired_secret", "夹层密码": "retired_secret", "抽屉密码": "retired_secret",
        "重置夹层密码": "retired_secret", "输出夹层密码": "retired_secret",
        "强制输出夹层密码": "retired_secret",
    },
    "group": {
        "帮助": "help", "help": "help",
        "tts语种": "voice_language", "语音语种": "voice_language",
        "切换语种": "voice_language", "语种切换": "voice_language",
        "切换语言": "voice_language", "语言切换": "voice_language",
        "语种": "voice_language", "语言": "voice_language",
        "状态": "group_context", "气氛": "group_context", "话题": "group_context", "线程": "group_context",
        "群友": "group_context", "成员": "group_context", "画像": "group_context",
        "黑话": "group_slang", "梗": "group_slang", "词": "group_slang",
        "片段": "group_memory", "群聊片段": "group_memory", "记忆": "group_memory",
        "插话判定": "manage_group", "插话反馈": "manage_group", "反馈": "manage_group", "关系网": "group_context",
        "关系网络": "group_context", "互动关系": "group_context", "关闭llm": "group_llm_block",
        "禁用llm": "group_llm_block",
        "恢复llm": "group_llm_release", "开启llm": "group_llm_release", "llm状态": "group_llm_status",
        "主链状态": "group_llm_status", "开启": "manage_group", "启用": "manage_group",
        "打开": "manage_group", "关闭": "manage_group", "停用": "manage_group",
        "关掉": "manage_group", "关闭主链": "group_llm_block", "开启主链": "group_llm_release",
        "恢复主链": "group_llm_release", "撤回消息": "manage_group", "防撤回": "manage_group",
        "转述撤回": "manage_group", "撤回转述": "manage_group",
    },
}

_ROOTS = {
    "private": frozenset({"陪伴", "私聊陪伴", "主动陪伴"}),
    "group": frozenset({"陪伴群", "群陪伴", "群聊陪伴"}),
}

_HDSI_ACTIONS = frozenset({"开启", "关闭", "观察", "状态", "统计"})

_MANAGEMENT_PAGES = {
    "manage_expression": "陪伴小屋 → 表达",
    "manage_relationship": "陪伴小屋 → 关系与记忆 → 对应用户页",
    "manage_contact": "陪伴小屋 → 主动与联系 → 对应用户页",
    "manage_life": "陪伴小屋 → 日记与生活或状态维护",
    "manage_memory": "陪伴小屋 → 关系与记忆 → 记忆管理",
    "manage_group": "陪伴小屋 → 关系网 → 对应群聊页",
    "manage_host": "陪伴小屋 → 账号与会话或资料与维护",
    "manage_image": "陪伴小屋 → 生图与图片素材",
    "manage_native": "陪伴小屋 → 对应内置能力设置",
    "manage_dates": "陪伴小屋 → 感知设置 → 重要日子与饭点（生日和纪念日自动读取）",
    "manage_diagnostics": "陪伴小屋 → 治理与维护 → 诊断与配置",
    "manage_efficiency": "陪伴小屋 → 治理与维护 → 近期回复效率",
    "manage_hdsi": "陪伴小屋 → 状态维护 → 日程模式",
    "retired_secret": "",
    "image_tasks": "陪伴小屋 → 生图 → 生成记录",
}

_HELP = (
    "私聊：陪伴 状态、能力列表、主动判定；陪伴 日程/查看日程、日记、梦境、记忆 <线索>、"
    "长期记忆 <线索>、片段 <线索>、答疑 <问题>；待审建议用“陪伴 答疑确认 <编号>”或“陪伴 答疑取消 <编号>”；"
    "日记、梦境、画像；生成日程、刷新日程、重新生成日程；"
    "陪伴画图 <描述>、陪伴合照 <修改要求>；重要日期用 陪伴 日期列表、日期添加 <名称> <日期>、日期删除 <名称>；"
    "日期列表也会列出每日反思自动记下的互动纪念日；探索笔记用 陪伴 探索笔记 查看。\n"
    "语种：陪伴 切换语种 中文/日语/英语（只改当前私聊窗口）；群聊用 陪伴群 切换语种 日语。\n"
    "群聊：陪伴群 状态、话题、黑话 <词>、片段 <线索>、LLM状态；关闭LLM / 开启LLM 需安装管理员。\n"
    "查询依赖本轮实际授权工具；其他配置、权限、清理与重置请在陪伴小屋对应页面操作。"
)


def _match_alias(value: str, aliases: dict[str, str]):
    """Return the matched intent and the remaining argument after the alias."""
    folded = value.casefold()
    for alias in sorted(aliases, key=len, reverse=True):
        key = alias.casefold()
        if folded == key:
            return aliases[alias], ""
        if folded.startswith(key + " "):
            return aliases[alias], value[len(alias):].strip()
    return None, ""


def parse_functional_command(text: object, *, channel: str) -> FunctionalCommand | None:
    """Recognize only a leading V1 command root and known function aliases."""
    if type(text) is not str or channel not in _ROOTS:
        return None
    stripped = text.strip()
    if not stripped:
        return None
    parts = stripped.split(maxsplit=1)
    head = parts[0].lstrip("/").casefold()
    if channel == "private" and head == "hdsi":
        action = parts[1].strip() if len(parts) > 1 else ""
        if action.casefold() in {item.casefold() for item in _HDSI_ACTIONS}:
            return FunctionalCommand(channel, "manage_hdsi", action)
        return None
    roots = {item.casefold() for item in _ROOTS[channel]}
    if head not in roots:
        # The two original image entry points were top-level command names.
        if channel == "private" and head in {"陪伴画图", "陪伴合照", "陪伴图任务"}:
            intent = {"陪伴画图": "image_generate", "陪伴合照": "image_edit", "陪伴图任务": "image_tasks"}[head]
            return FunctionalCommand(channel, intent, parts[1].strip() if len(parts) > 1 else "")
        return None
    tail = parts[1].strip() if len(parts) > 1 else ""
    intent, argument = _match_alias(tail, _ALIASES[channel])
    if channel == "private" and intent == "manage_diagnostics" and len(argument.split()) == 1:
        head = tail.split(maxsplit=1)[0].casefold()
        if head in {"答疑确认", "应用答疑建议"}:
            intent = "diagnostics_confirm"
        elif head in {"答疑取消", "取消答疑建议"}:
            intent = "diagnostics_cancel"
    return FunctionalCommand(channel, intent, argument) if intent else None


def _base_instruction(command: FunctionalCommand | None) -> str:
    if command is None:
        return ""
    intent = command.intent
    if intent == "help":
        return "用户明确调用功能帮助。只简洁列出当前版本真实可用指令与管理页入口，不把旧版未迁移的操作说成仍可执行。\n" + _HELP
    if intent == "status":
        return "用户请求角色状态。优先依据本轮已提供的角色状态、睡眠和生活材料回答；材料缺失时说明未取得，不把角色状态套到用户身上。"
    if intent == "capabilities":
        return "用户请求能力列表。只总结本轮实际可用工具和已启用内置能力，不声称未提供或未授权的能力可执行；设置入口指向陪伴小屋。"
    if intent == "diagnostics":
        return "用户请求诊断。优先调用本轮提供的 companion_v2_diagnostics_inspect 或 companion_v2_diagnostics_explain；检查按明确类型和当前角色/会话范围运行。用户要求修复时，先取得当前配置检查报告，再用 companion_v2_diagnostics_propose 保存待审建议；不得自行确认。只有 inspect 产生的当前运行证据才能作为检查结论，不能把设计说明说成已检查。"
    if intent in {"diagnostics_confirm", "diagnostics_cancel"}:
        action = "companion_v2_diagnostics_proposal_confirm" if intent == "diagnostics_confirm" else "companion_v2_diagnostics_proposal_cancel"
        operation = "diagnostics.proposal.confirm" if intent == "diagnostics_confirm" else "diagnostics.proposal.cancel"
        verb = "确认并应用" if intent == "diagnostics_confirm" else "取消"
        return (f"用户在本条私聊消息中明确要求{verb}修复建议，唯一编号是 {command.argument}。"
                f"必须调用本轮工具 {action}（{operation}）且参数 proposal_id 必须逐字等于该编号；不要改用其他编号、推断用户同意或使用自然语言确认。"
                "只按工具回执说明结果；建议过期、版本变化、权限不足或状态冲突时不得声称成功。")
    if intent == "calendar":
        return ("用户请求查看或维护日程、重要日期。查看时调用本轮授权的 companion_v2_calendar_query；"
                "新增、修改或删除时先查询当前日历，再按用户明确要求调用 companion_v2_calendar_command，"
                "使用本轮证据句柄并遵从人类确认边界；没有工具或结果时如实说明。")
    if intent == "timeline":
        return "用户请求查看角色日记、梦境或生活历史。调用本轮授权的 companion_v2_role_timeline_query；该工具只返回其实际提供的历史摘要和元数据，不补写旧正文或未记录的经历。"
    if intent == "memory":
        return "用户请求查询角色记忆或共同经历。优先调用本轮授权的 companion_v2_memory_query；明确查旧版归档时使用 companion_v2_memory_legacy_query。查询词取自用户原话；只报告工具返回的结果，不因未命中断言从未发生。"
    if intent == "person":
        return "用户请求查看当前私聊对象的角色熟人资料。调用本轮授权的 companion_v2_acquaintance_recall；称呼与关系只按返回记录表达，不推断身份或权限。"
    if intent == "group_context":
        return "用户请求查看本群上下文。只依据本轮已提供且当前群聊可见的材料作答；若需要历史群记忆，使用本轮提供的 companion_v2_group_memory_query。不要声称枚举了完整成员或关系网络。"
    if intent == "group_slang":
        return "用户请求查询本群黑话。调用本轮授权的 companion_v2_slang_search，并结合命令后的词或当前语境；相似度只是线索，释义不确定时保留不确定。"
    if intent == "group_memory":
        return "用户请求查询本群片段或记忆。调用本轮授权的 companion_v2_group_memory_query，并使用命令后的线索；仅返回当前群聊范围内授权可见的材料。"
    if intent in {"image_generate", "image_edit"}:
        tool = "companion_v2_image_generate" if intent == "image_generate" else "companion_v2_image_edit"
        action = "生图" if intent == "image_generate" else "改图"
        return (f"用户通过功能指令请求{action}。调用本轮实际提供且授权的 {tool} 图片工具完成任务，"
                "不要只用文字描述准备怎么做；成功后依据工具回执把生成图片发到当前会话。"
                "工具不可用、未配置或请求被拒绝时说明实际原因，不声称已生成或已送达。")
    if intent == "memory_clear":
        return ("本轮「清空记忆」由程序判定并附独立回执。若回执显示尚未执行，只解释原因与确认方式，"
                "不要声称已经清空；若回执显示已执行，只据实转述数量与逻辑遗忘性质——停止使用、保留修订记录、"
                "未物理删除平台消息、日志或备份。不得改写数字，也不要再调用记忆工具重复清理。")
    if intent in GROUP_CONTROL_INTENTS:
        return ("用户输入的是群 LLM 临时封锁、恢复或状态指令。这条指令本应在模型之前由程序执行；"
                "若本回合没有独立回执，说明拦截未生效：只说明「没有更改群开关」，不要模拟执行，"
                "也不要把它当普通聊天或承诺稍后处理。")
    if intent == "reset_all":
        return ("用户输入的是旧版全局重置指令。这条指令本应在模型之前由程序预览并确认；"
                "若本回合没有独立回执，说明没有执行：只说明「没有重置任何数据」，不要声称已重置，"
                "也不要调用工具自行清除记忆。")
    if intent == "open_loop_delete":
        return ("用户要求删除一条未完话头或便签。先用本轮授权的 companion_v2_memo_list（purpose=open_loop）"
                "按用户给的线索找到唯一原件，再用 companion_v2_memo_delete 带上 memo_id 与 expected_revision；"
                "只有成功回执才能说已删除。找不到、匹配多条、未授权或工具不可用时，如实说明没有删除并给出"
                "「陪伴小屋 → 关系与记忆」入口，不要凭标题直接断言已删。")
    if intent in {"dates_list", "dates_add", "dates_remove"}:
        return ("本轮重要日期由程序读取或写入并附独立回执。若没有回执，说明本条没有更改日期；"
                "有回执时只转述回执里的名称、日期与结果；自动纪念日以日期列表或删除回执为准，"
                "不把日历计划或出生资料当成纪念日，也不声称主动问候开关已打开。")
    if intent == "voice_language":
        return ("本轮语音语种由程序写入当前私聊或群聊窗口并附独立回执。没有回执就说明没有更改；"
                "有回执时只转述设定、当前窗口与「语音回复开关是否开启」，不得声称已经用新语种说过话，"
                "也不把本轮普通回复当成语音。")
    if intent == "manage_hdsi":
        return ("用户输入的是 V1 HDSI 聊天开关/统计指令。V7 不执行旧版窗口实验开关或统计语法；"
                "日程模式已迁到陪伴小屋 → 状态维护 → 日程模式。说明本条聊天指令没有更改设置，不要声称已切换。")
    if intent == "manage_diagnostics":
        return ("用户输入了答疑建议管理指令，但没有提供唯一建议编号。"
                "确认或取消需在私聊完整输入“陪伴 答疑确认 <编号>”或“陪伴 答疑取消 <编号>”；不接受旧版编号或直接文本改配置。"
                "不要声称已经修改。")
    if intent == "retired_secret":
        return ("用户输入了 V1 隐藏口令查询、输出或重置指令。V7 不保存、不恢复、不输出这类口令；"
                "不得检索记忆、配置或资料来拼出秘密，也不得声称已重置。简短说明该旧指令已不提供。")
    page = _MANAGEMENT_PAGES.get(intent)
    if page:
        label = {
            "manage_expression": "表达学习与 TTS 设置", "manage_relationship": "称呼与关系资料",
            "manage_contact": "主动联系与投递目标", "manage_life": "角色生活与日程设置",
            "manage_memory": "记忆清理与未完话头管理", "manage_group": "群聊开关与治理",
            "manage_host": "人格、提示词与宿主设置", "manage_image": "图片素材、衣柜与生图路由",
            "manage_native": "内置能力设置", "manage_efficiency": "回复效率统计",
            "image_tasks": "图片任务管理",
        }.get(intent, "相关设置")
        return (f"用户使用旧版管理语法请求{label}。V7 对应入口：{page}。"
                "这条聊天指令不会代替管理页提交变更；不要把它当普通聊天，不要模拟写入或调用相似工具修改配置，"
                "简短告知用户准确入口。")
    return ""


_TOOL_BACKED_INTENTS = frozenset({
    "calendar", "timeline", "memory", "person", "group_context", "group_slang", "group_memory",
    "image_generate", "image_edit", "diagnostics", "diagnostics_confirm", "diagnostics_cancel",
    "open_loop_delete",
})
_RECEIPT_CONTRACT = (
    "结果只按本轮工具回执说明：授权不足或工具不可用时明确说「没有执行」并给原因；"
    "没有实际调用工具时不得描述成已执行；执行失败或结果未知时如实报告，未知结果不重试、不换工具冒充成功。"
)


def command_instruction(command: FunctionalCommand | None) -> str:
    text = _base_instruction(command)
    if command is not None and command.intent in _TOOL_BACKED_INTENTS and text:
        return text + "\n" + _RECEIPT_CONTRACT
    return text


_EXECUTED_INTENTS = frozenset({"memory_clear", "dates_list", "dates_add", "dates_remove",
                               "voice_language", "exploration_notes"})
GROUP_CONTROL_INTENTS = frozenset({"group_llm_block", "group_llm_release", "group_llm_status"})
_RECEIPT_EXTRA = "_companion_v2_functional_command_receipt"

_FAILURE_TEXT = {
    "memory_private_context_required": "这不是该记忆所属的私聊关系",
    "memory_disabled": "记忆功能未启用",
    "memory_forget_not_authorized": "当前身份没有清空记忆的授权",
    "core_access_required": "只有安装核心管理员可以执行该操作",
    "memory_clear_stale": "记忆内容已变化，原预览失效",
    "memory_operation_conflict": "同一操作编号已用于另一份预览",
    "staging_host_terminated": "插件已停止",
    "host_not_running": "插件尚未运行",
    "configuration_revision_conflict": "设置在你操作期间被改过，请重试",
    "invalid_contact_preference": "日期格式或名称不符合要求",
    "configuration_subject_mismatch": "这项设置属于另一个私聊关系",
    "configuration_scope_not_supported": "这项设置不支持在当前窗口写入",
    "configuration_relationship_required": "需要已绑定的人格与私聊关系",
    "configuration_operation_incomplete": "上一次写入没有留下完整结果",
}


def _event_extra(event, name):
    getter = getattr(event, "get_extra", None)
    return getter(name, None) if callable(getter) else getattr(event, name, None)


def _set_event_extra(event, name, value):
    setter = getattr(event, "set_extra", None)
    if callable(setter):
        setter(name, value)
        return
    try:
        setattr(event, name, value)
    except Exception:
        pass


def confirmation_code(argument: str) -> str:
    """Read '确认 CODE' / '确认码 CODE' / a bare code; empty means no confirmation yet."""
    value = " ".join((argument or "").split())
    if not value:
        return ""
    head, _, rest = value.partition(" ")
    if head in {"确认", "确认码"}:
        value = rest.strip()
    return value.upper()


def _failure_receipt(failure, label: str) -> str:
    reason = _FAILURE_TEXT.get(failure.code)
    if reason is None:
        reason = failure.reason or "程序没有完成该操作"
        return f"未执行：{label}没有完成（{failure.code}）。"
    return f"未执行：{label}没有完成——{reason}。"


def _execute_memory_clear(command: FunctionalCommand, application, context) -> str:
    from companion_v2.execution.models import digest
    from companion_v2.foundation.refs import ScopeKind
    from companion_v2.memory.management import MemoryManagement

    if command.surface != "private" or context.scope.kind is not ScopeKind.PRIVATE:
        return "未执行：清空记忆只在对应私聊关系内可用。"
    management = MemoryManagement(application.memory, None)
    preview = management.preview_clear(context)
    if preview.failure is not None:
        return _failure_receipt(preview.failure, "清空记忆")
    value = preview.unwrap()
    counts = value["counts"]
    token = value["revision_token"]
    code = MemoryManagement.confirmation_code(token)
    supplied = confirmation_code(command.argument)
    if not supplied:
        return (f"尚未执行：当前关系共有 {counts['active'] + counts['already_inactive']} 条记忆事实"
                f"（活跃 {counts['active']} 条，已停用 {counts['already_inactive']} 条）。"
                f"确认清空请回复「陪伴 清空记忆 确认 {code}」。确认码会在记忆内容或授权变化后失效；"
                "该操作是逻辑遗忘，会停止使用并保留修订记录，不物理删除平台消息、日志或备份。")
    if supplied != code:
        return "未执行：确认码不匹配或记忆内容已变化。请重新发送「陪伴 清空记忆」获取新的确认码。"
    cleared = management.clear(context, expected_revision_token=token,
                               operation_key="chat-clear-" + digest([context.key, token]))
    if cleared.failure is not None:
        return _failure_receipt(cleared.failure, "清空记忆")
    result = cleared.unwrap()
    return (f"已执行：将 {result['forgotten_count']} 条记忆事实标记为遗忘"
            "（逻辑遗忘，停止使用并保留修订记录，未物理删除）。")


_IMPORTANT_DATE_PATTERN = re.compile(
    r"(?P<lunar>农历)?\s*(?:(?P<year>\d{4})\s*[年/-]\s*)?"
    r"(?P<month>\d{1,2})\s*[月/-]\s*(?P<day>\d{1,2})\s*日?")


def parse_important_date(argument: str):
    """Return (label, month, day, lunar) or raise ValueError with a user-facing reason."""
    text = " ".join(str(argument or "").split())
    match = _IMPORTANT_DATE_PATTERN.search(text)
    if match is None:
        raise ValueError("请在名称后写日期，例如「陪伴 日期添加 生日 5月20日」。")
    label = (text[:match.start()] + " " + text[match.end():]).strip(" ，,、:：")
    if not label:
        raise ValueError("请给这个日期一个名称，例如「生日 5月20日」。")
    if len(label) > 64 or any(ord(character) < 32 for character in label):
        raise ValueError("日期名称需要是 64 字以内的普通文本。")
    month, day = int(match.group("month")), int(match.group("day"))
    lunar = bool(match.group("lunar"))
    try:
        if lunar:
            if not (1 <= month <= 12 and 1 <= day <= 30):
                raise ValueError
        else:
            date(2000, month, day)
    except ValueError:
        raise ValueError(f"日期 {month} 月 {day} 日不存在，没有写入。") from None
    return label, month, day, lunar


def _important_date_rows(value):
    from companion_v2.proactive.configuration import important_dates

    if type(value) is not str or not value.strip():
        return []
    rows = []
    for item in important_dates(value):
        row = {"calendar": item.calendar, "month": item.month, "day": item.day, "label": item.label}
        if item.calendar == "lunar" and item.leap_month:
            row["leap_month"] = True
        rows.append(row)
    return rows


def _important_date_text(row):
    calendar = "农历" if row.get("calendar") == "lunar" else ""
    return f"{row['label']} {calendar}{row['month']}月{row['day']}日"


def _remove_important_date(rows, argument):
    text = " ".join(str(argument or "").split())
    if not text:
        raise ValueError("请给出要删除的名称或日期，例如「陪伴 日期删除 生日」。")
    matches = [row for row in rows if row.get("label") == text]
    if not matches:
        try:
            label, month, day, _ = parse_important_date(text)
        except ValueError:
            label = ""
        if label:
            matches = [row for row in rows if row.get("label") == label
                       and row.get("month") == month and row.get("day") == day]
    if not matches:
        match = _IMPORTANT_DATE_PATTERN.search(text)
        if match is not None and not text[:match.start()].strip(" ，,、:："):
            month, day = int(match.group("month")), int(match.group("day"))
            calendar = "lunar" if match.group("lunar") else None
            matches = [row for row in rows if row.get("month") == month and row.get("day") == day
                       and (calendar is None or row.get("calendar") == calendar)]
    if not matches:
        matches = [row for row in rows if text in str(row.get("label") or "")]
    if not matches:
        raise ValueError(f"没有找到「{text}」。")
    if len(matches) > 1:
        raise ValueError(f"有多条匹配「{text}」，请用更完整的名称或日期。")
    removed = matches[0]
    return [row for row in rows if row is not removed], removed


def _execute_important_dates(command, application, context):
    from companion_v2.execution.models import digest
    from companion_v2.foundation.refs import OperationRef, VersionRef
    from companion_v2.proactive.configuration import GROUP, target_for
    from companion_v2.relationships import anniversaries

    if command.surface != "private":
        return "未执行：重要日期只在私聊里维护。"
    configuration = getattr(application, "configuration", None)
    if configuration is None:
        return "未执行：配置服务当前不可用。"
    target = target_for(context)
    now = application.now()
    read = configuration.read(context.actor, target=target, group_id=GROUP, observed_at=now)
    if read.failure is not None:
        return _failure_receipt(read.failure, "重要日期")
    snapshot = read.unwrap()
    try:
        rows = _important_date_rows(snapshot.values.get(GROUP + ".important_dates", "[]"))
    except (ValueError, TypeError):
        return "未执行：现有重要日期记录无法解析，请先到陪伴小屋核对。"
    runtime = getattr(application, "runtime", None)
    auto_rows = ()
    if runtime is not None:
        try:
            auto_rows = anniversaries.list_active(runtime, context)
        except Exception:
            auto_rows = ()
    if command.intent == "dates_list":
        values = [_important_date_text(row) for row in rows]
        values.extend(f"{row['label']}（{row['month']}月{row['day']}日，每日反思自动记下）"
                      for row in auto_rows)
        if not values:
            return "当前没有记录重要日期。"
        return "当前记录的重要日期：" + "；".join(values) + "。"
    if command.intent == "dates_add":
        try:
            label, month, day, lunar = parse_important_date(command.argument)
        except ValueError as error:
            return f"未执行：{error}"
        row = {"calendar": "lunar" if lunar else "gregorian", "month": month, "day": day, "label": label}
        if any(item.get("calendar") == row["calendar"] and item.get("month") == month
               and item.get("day") == day and item.get("label") == label for item in rows):
            return f"未添加：已经有一条「{_important_date_text(row)}」。"
        if len(rows) >= 32:
            return "未添加：最多保存 32 条重要日期，请先删除一条。"
        rows.append(row)
        action = "添加"
    else:
        try:
            rows, removed = _remove_important_date(rows, command.argument)
        except ValueError as error:
            auto_name = " ".join((command.argument or "").split())
            if runtime is not None and auto_name:
                try:
                    auto_removed = anniversaries.retract_by_label(runtime, context, auto_name)
                except Exception:
                    auto_removed = ()
                if auto_removed:
                    return f"已删除：每日反思记下的互动纪念日「{auto_removed[0]}」。"
            return f"未删除：{error}"
        action = "删除"
    identifier = "chat-important-dates-" + digest(
        [context.key, command.intent, command.argument or "", snapshot.edit_token])
    operation = OperationRef(identifier, GROUP, target.authorization_scope, VersionRef(identifier, 1), identifier)
    receipt = configuration.patch(
        context.actor, target=target, group_id=GROUP, operation=operation,
        expected_edit_token=snapshot.edit_token,
        changes={GROUP + ".important_dates": json.dumps(rows, ensure_ascii=False, separators=(",", ":"))},
        observed_at=now)
    if receipt.failure is not None:
        return _failure_receipt(receipt.failure, "重要日期")
    if action == "添加":
        return f"已添加：{_important_date_text(row)}（每年重复）。"
    return f"已删除：{_important_date_text(removed)}。"


_VOICE_LANGUAGES = {
    "中文": "zh", "汉语": "zh", "普通话": "zh", "zh": "zh",
    "日语": "ja", "日文": "ja", "日本語": "ja", "ja": "ja",
    "英语": "en", "英文": "en", "en": "en",
    "默认": "zh", "default": "zh",
}
_VOICE_LANGUAGE_NAMES = {"zh": "中文", "ja": "日语", "en": "英语"}


def _execute_voice_language(command, application, context):
    from companion_v2.conversation.reply_settings import GROUP, OWNER, target_for
    from companion_v2.execution.models import digest
    from companion_v2.foundation.refs import OperationRef, VersionRef

    if command.surface not in ("private", "group"):
        return "未执行：语音语种只能在当前私聊或群聊窗口内设置。"
    argument = " ".join((command.argument or "").split()).casefold()
    language = _VOICE_LANGUAGES.get(argument)
    if language is None:
        example = "陪伴群 切换语种 日语" if command.surface == "group" else "陪伴 切换语种 日语"
        return f"未执行：语种可用 中文、日语、英语，例如「{example}」。"
    configuration = getattr(application, "configuration", None)
    if configuration is None:
        return "未执行：配置服务当前不可用。"
    target = target_for(context)
    now = application.now()
    read = configuration.read(context.actor, target=target, group_id=GROUP, observed_at=now)
    if read.failure is not None:
        return _failure_receipt(read.failure, "语音语种")
    snapshot = read.unwrap()
    values = snapshot.values
    label = _VOICE_LANGUAGE_NAMES[language]
    scope_label = "本群" if command.surface == "group" else "本窗口"
    if values.get(GROUP + ".voice_language") == language:
        return f"没有更改：{scope_label}语音语种已经是{label}。"
    identifier = "chat-voice-" + digest([context.key, language, snapshot.edit_token])
    operation = OperationRef(identifier, OWNER, target.authorization_scope, VersionRef(identifier, 1), identifier)
    receipt = configuration.patch(
        context.actor, target=target, group_id=GROUP, operation=operation,
        expected_edit_token=snapshot.edit_token,
        changes={GROUP + ".voice_language": language}, observed_at=now)
    if receipt.failure is not None:
        return _failure_receipt(receipt.failure, "语音语种")
    text = (f"已切换：{scope_label}语音语种＝{label}。"
            "音色按该语种的默认 TTS 配置生效；未绑定则沿用当前会话 TTS。")
    if not values.get(GROUP + ".voice_enabled"):
        text += "语音回复开关当前关闭，开启后才会按该语种朗读。"
    return text


def _execute_exploration_notes(command, application, context):
    if command.surface != "private":
        return "未执行：探索笔记只在私聊里查看。"
    schedule = getattr(application, "contact_schedule", None)
    service = getattr(schedule, "exploration", None)
    if service is None:
        return "当前没有可用的探索服务。"
    snapshot = service.read(context)
    notes = snapshot.get("notes") or ()
    if not notes:
        last = snapshot.get("last_result") or "尚未运行"
        return f"当前还没有研究笔记（最近状态：{last}）。"
    lines = []
    for note in notes[:5]:
        label = " ".join(str(note.get("label") or "").split())[:40]
        title = " ".join(str(note.get("title") or "").split())[:80]
        source = " ".join(str(note.get("source") or "").split())[:32]
        policy = {"private_note": "仅准备", "can_ask": "可自然询问", "can_share": "可分享"}.get(
            note.get("share_policy"), "仅准备")
        lines.append(f"{label}：{title}" + (f"（{source}）" if source else "") + f"·{policy}")
    return "最近的研究笔记：" + "；".join(lines) + "。"


def execute_functional_command(command: FunctionalCommand | None, *, application=None, context=None) -> str:
    """Run the few commands that must not depend on the model choosing a tool."""
    if command is None or command.intent not in _EXECUTED_INTENTS:
        return ""
    if application is None or context is None:
        return ""
    try:
        if command.intent == "memory_clear":
            return _execute_memory_clear(command, application, context)
        if command.intent in {"dates_list", "dates_add", "dates_remove"}:
            return _execute_important_dates(command, application, context)
        if command.intent == "voice_language":
            return _execute_voice_language(command, application, context)
        if command.intent == "exploration_notes":
            return _execute_exploration_notes(command, application, context)
    except Exception as error:  # noqa: BLE001 - a receipt must never break the reply
        return f"未执行：程序处理该指令时出错（{type(error).__name__}）。"
    return ""


def functional_command_receipt(event, command: FunctionalCommand | None, *, application=None,
                               context=None) -> str:
    """Execute once per admitted event and reuse the receipt on later model rounds."""
    if command is None or command.intent not in _EXECUTED_INTENTS:
        return ""
    stashed = _event_extra(event, _RECEIPT_EXTRA)
    if isinstance(stashed, str):
        return stashed
    receipt = execute_functional_command(command, application=application, context=context)
    _set_event_extra(event, _RECEIPT_EXTRA, receipt)
    return receipt


def inject_functional_command_instruction(event, request, *, channel: str, own_module: str | None = None,
                                          application=None, context=None) -> FunctionalCommand | None:
    """Attach request-local guidance after native extensions have reset their context."""
    from .event_routing import competing_command_handler

    if competing_command_handler(event, own_module=own_module) is not None:
        command = None
    else:
        command = parse_functional_command(message_text(event), channel=channel)
    _store_core_functional_command(event, command)
    set_system_instruction(request, "functional_command", command_instruction(command))
    set_system_instruction(request, "functional_command_receipt",
                           functional_command_receipt(event, command, application=application, context=context))
    return command


def classify_functional_command(event, *, channel: str, own_module: str | None = None) -> FunctionalCommand | None:
    """Classify before native tool injection so exact confirmation tools are request-scoped."""
    from .event_routing import competing_command_handler

    if competing_command_handler(event, own_module=own_module) is not None:
        command = None
    else:
        command = parse_functional_command(message_text(event), channel=channel)
    _store_core_functional_command(event, command)
    return command


def _store_core_functional_command(event, command):
    try:
        from .core_reply import CORE_REPLY_CONTEXT_EXTRA
        state = _event_extra(event, CORE_REPLY_CONTEXT_EXTRA)
    except (ImportError, AttributeError, TypeError):
        state = None
    if isinstance(state, dict):
        state["functional_command"] = command
