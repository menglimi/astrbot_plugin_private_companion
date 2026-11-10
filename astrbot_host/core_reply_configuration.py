"""Describe configuration consumed by AstrBot-owned conversation replies."""

from .core_reply_context import BACKGROUND_INSTRUCTION, GROUP_INSTRUCTION, MEMORY_INSTRUCTION


CORE_MANAGED_FIELDS = (
    "provider", "fallback", "max_output_tokens", "timeout_seconds",
    "fallback_on_refusal", "refusal_terms", "replacement_scope",
)


def project_core_reply_task(task):
    if not isinstance(task, dict) or task.get("kind") != "conversation.reply":
        return task
    from companion_v2.conversation.content.authoring_workflow import INSTRUCTION as AUTHORING_INSTRUCTION
    from .core_reply_tools import TOOL_INSTRUCTION

    expression_sections = [section for section in task.get("prompt_sections", ())
                           if section.get("key") in {"style", "humanized"}]
    return {
        **task,
        "execution_path": "astrbot_core",
        "core_managed_fields": list(CORE_MANAGED_FIELDS),
        "editable_fields": ["prompt", "replacement_rules"],
        "routing_note": ("普通私聊与群聊由 AstrBot 当前会话选择模型；保存的纯时段替换规则会在命中时段内"
                         "按轮临时切换到替代 Provider，不修改会话模型。"),
        "prompt_layers": [
            {"key": "host_persona", "label": "AstrBot 人格与会话历史", "editable": False,
             "description": "核心构造当前人格、对话历史、原始图片与音视频；在 AstrBot 中管理。"},
            {"key": "v2_background", "label": "V2 背景与表达偏好", "editable": bool(expression_sections),
             "description": "按当前窗口权限提供角色资料、关系、记忆、生活与表达偏好；可用内容随本轮变化。"},
            {"key": "task_prompt", "label": "对话附加提示", "editable": True,
             "description": "本页保存的提示直接追加到核心系统提示；自定义值替换此字段的默认值。"},
            {"key": "native_tools", "label": "AstrBot 原生工具", "editable": False,
             "description": "V2 工具沿用核心工具流程；分段由本插件按当前人格/窗口配置在回复发送前处理，工具往返、流式和其他装饰继续由核心执行。"},
        ],
        "prompt_sections": [
            {"key": key, "label": label, "condition": condition, "editable": False,
             "default_prompt": prompt, "prompt": prompt, "variables": []}
            for key, label, condition, prompt in (
                ("v2_background", "V2 背景使用规则", "本轮存在 V2 背景时", BACKGROUND_INSTRUCTION),
                ("private_memory", "私聊记忆归属", "注入当前私聊记忆时", MEMORY_INSTRUCTION),
                ("group_background", "群聊背景归属", "当前是群聊时", GROUP_INSTRUCTION),
                ("native_tool_guidance", "核心工具使用规则", "本轮提供 V2 原生工具时", TOOL_INSTRUCTION),
                ("content_authoring", "作品创建与续写规则", "本轮提供原生写作工具时", AUTHORING_INSTRUCTION),
            )
        ] + expression_sections,
    }


def project_core_reply_configuration(payload):
    if not isinstance(payload, dict):
        return payload
    result = dict(payload)
    if isinstance(result.get("tasks"), list):
        result["tasks"] = [project_core_reply_task(task) for task in result["tasks"]]
    if isinstance(result.get("task"), dict):
        result["task"] = project_core_reply_task(result["task"])
    return result
