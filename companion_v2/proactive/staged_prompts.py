"""Short planning and writing contracts for optional two-call proactive contact."""

from dataclasses import replace
import json

from companion_v2.conversation.foreground import PromptMaterial, SealedForegroundContext
from companion_v2.conversation.style_context import EXPRESSION_STYLE_INSTRUCTION

from .configuration import GROUP
from . import rhythm
from .official_alert_events import SOURCE_OWNER as OFFICIAL_ALERT_SOURCE_OWNER
from .prompt_templates import effective_template
from .prompts import (DIAGNOSTIC_PRESENTATION_INSTRUCTION, GREETING_INSTRUCTION, INSOMNIA_GREETING_INSTRUCTION,
                      ORIGIN_INSTRUCTIONS, PURPOSE_INSTRUCTIONS, SKIP_REASONS,
                      SOURCE_ORIGINS, TOPIC_CONTINUITY_INSTRUCTION)


HISTORY_PURPOSES = frozenset({"recent_private_dialogue", "dedup_candidate",
                              "confirmed_background_message", "last_confirmed_reply",
                              "confirmed_reply_context"})

PLAN_INSTRUCTION = """这是一次主动联系的内部规划，不写用户可见正文。先根据当前机会、来源、近期真实问答和已送达消息判断此刻是否值得开启一个新回合，再选独立话题和表达角度。历史里的角色消息不是用户刚发来的话；尤其当最后一条是角色自己的回复、用户之后没有新输入时，不规划“哦/那/所以”式接续自己的下半句。可以带来新的观察、小感想或轻问题，不要求重大事件，也不因暂未回复而自动跳过。
真实联系的事实只取核验材料支持的范围，合成演练按专门说明理解；用户与角色的经历不能互换，计划、梦和日记不当成完成事实。用户已回答、收尾或要求停止的事项不重新追问。时机、星期和当前状态以 contact_intent 为准；去重参考只用于理解已经聊过什么。
只输出一个 JSON 对象。决定发送时输出 {"decision":"send","decision_confidence":0.8,"angle":"一句简短的新回合切口","facts":["可核验的事实或主观念头"],"avoid":["需要避开的旧话头"]}；确实没有自然内容、重复或越界时输出 {"decision":"skip","decision_confidence":0.8,"skip_reason":"repetitive"}。skip_reason 只能是 insufficient_context、no_natural_opening、purpose_mismatch、repetitive、respect_user_boundary 或 other。facts 与 avoid 是写作参考，不是新的事实来源；不要写正文或推理过程。"""

WRITE_INSTRUCTION = """这是一次已经决定开启的主动联系新回合。contact_brief 是内部规划，只提供切口，不是待续写的上一句；近期历史是去重和关系参考，不是待回复队列。即使角色上一条消息看起来没说完，也从本轮 brief 的独立角度自然开口，不接自己的话，不用“哦/那/所以/接着”把旧回复补完。
真实联系只根据 contact_evidence、近期真实对话和当前角色状态写可核验事实；合成演练按专门说明理解，brief 中没有来源支持的事实不得照写。用户与角色的经历不能互换，计划不当完成，日记与梦不当亲历。保持原人格、关系距离和当下时间感，不催促回复，不重复用户已回答的问题。正文要有具体内容，不机械复述 brief 或播报状态。
只输出一个 JSON 对象，text 是用户可见正文，不写决策、来源编号或内部说明。通常格式为 {"text":"自然的主动消息"}；本轮启用的 reaction_intent、voice_intent、voice_text、poke_intent 才可按各自规则增加。"""

ALERT_SCOPE_INSTRUCTION = ("官方预警只在通告原文明确覆盖配置城市全市或明确列出目标镇街，且没有排除目标时才能联系。"
    "仅有经纬度、级别或模糊的部分地区描述不证明覆盖。写作时从标题或正文摘录连续原文到 coverage_quote，"
    "并输出 coverage=covered；证据不支持时不得编造覆盖结论。")
WRITE_REACTION_INSTRUCTION = ("若图库表情能自然补足这次分享或情绪，可增加 reaction_intent，"
    "说明想表达的画面、动作和语境；这只是检索意图，不代表图片已选中或发送。")
WRITE_POKE_INSTRUCTION = ("只有轻轻戳一下比直接开口自然时，才增加 poke_intent:true；"
    "不要声称动作已经执行。")


def _diagnostic_guidance(job, *, writing):
    if not job.administrator_test:
        return ""
    diagnostic = (job.diagnostic_origin or {}).get("_diagnostic") or {}
    if diagnostic.get("route_id") == "synthetic_event":
        instruction = ("这是管理员确认的合成情境演练。contact_intent.thought 是本轮已知的情境前提，"
            "可以据此规划和表达，但不代表真实用户输入、外部来源或已经发生的事实；"
            "不要补造前提以外的经历。不要只因没有真实聊天或来源而跳过。")
    elif job.diagnostic_source_backed:
        instruction = ("这是管理员确认的真实来源投递测试。只依据本轮已核验来源，"
            "没有自然内容时可以跳过；不要为了通过测试改写成空泛问候或编造事实。")
    else:
        instruction = ("这是管理员确认的投递测试，不评估生产场景是否适合主动联系。"
            "没有聊天历史或真实事件不是跳过理由；围绕所选目的带来角色自己的念头，"
            "除人格边界或安全要求不合适外选择 send，不补造共同经历。")
    if job.diagnostic_image_intent:
        instruction += ("本轮还需验收授权图库的主动图片投递；除人格边界或安全要求不合适外选择 send。"
            "正文必须独立完整，图片由程序检索，不声称图片已经发送。")
    if writing:
        instruction += DIAGNOSTIC_PRESENTATION_INSTRUCTION
    return instruction


def _source_guidance(job, records):
    origin = job.automatic_origin or job.diagnostic_origin or {}
    keys = set(origin) if isinstance(origin, dict) else set()
    keys.update(SOURCE_ORIGINS.get(record.reference.owner) for record in records)
    return "\n".join(ORIGIN_INSTRUCTIONS[key] for key in ORIGIN_INSTRUCTIONS if key in keys)


def _compact_materials(base, *, history_count):
    materials = base.context.materials
    history = [item for item in materials if item.purpose in HISTORY_PURPOSES]
    other = [item for item in materials if item.purpose not in HISTORY_PURPOSES]
    return tuple((*other, *history[-history_count:]))


def plan_request_for(base, job, records):
    official_alert = any(record.reference.owner == OFFICIAL_ALERT_SOURCE_OWNER for record in records)
    instruction = "\n".join(part for part in (
        PLAN_INSTRUCTION, PURPOSE_INSTRUCTIONS[job.intent.purpose],
        _source_guidance(job, records), TOPIC_CONTINUITY_INSTRUCTION,
        ALERT_SCOPE_INSTRUCTION if official_alert else "",
        _diagnostic_guidance(job, writing=False),
    ) if part)
    return replace(base, context=SealedForegroundContext(_compact_materials(base, history_count=3), (), ()),
                   instruction=instruction, output_format="proactive_plan")


def plan_output(text):
    data = json.loads(text)
    if type(data) is not dict or data.get("decision") not in ("send", "skip"):
        raise ValueError("contact plan requires send or skip")
    confidence = data.get("decision_confidence")
    if type(confidence) not in (int, float) or not 0 <= confidence <= 1:
        raise ValueError("contact plan requires bounded confidence")
    if data["decision"] == "skip":
        if set(data) != {"decision", "decision_confidence", "skip_reason"} or data["skip_reason"] not in SKIP_REASONS:
            raise ValueError("skipped contact plan requires one reason")
    else:
        if set(data) != {"decision", "decision_confidence", "angle", "facts", "avoid"}:
            raise ValueError("sendable contact plan requires a brief")
        if (type(data["angle"]) is not str or not 1 <= len(data["angle"].strip()) <= 500
                or any(type(data[key]) is not list or len(data[key]) > 6
                       or any(type(item) is not str or not item.strip() or len(item) > 300
                              for item in data[key]) for key in ("facts", "avoid"))):
            raise ValueError("contact brief must be short text")
    return data


def write_request_for(base, job, records, preferences, *, reactions_available, voice_available,
                      poke_available, extra_instruction):
    brief = PromptMaterial(GROUP, "contact_brief", json.dumps(job.plan, ensure_ascii=False))
    materials = (*_compact_materials(base, history_count=2), brief)
    news = job.intent.purpose == "news" or "news_event" in (job.automatic_origin or {})
    greeting = job.intent.purpose == "greeting"
    greeting_event = (job.automatic_origin or {}).get("greeting_event")
    official_alert = any(record.reference.owner == OFFICIAL_ALERT_SOURCE_OWNER for record in records)
    length = rhythm.body_chars(preferences)
    length_instruction = (effective_template(preferences.values,
        "news_length_prompt" if news else "length_prompt").format(chars=length) if length else "")
    instruction = "\n".join(part for part in (
        WRITE_INSTRUCTION, PURPOSE_INSTRUCTIONS[job.intent.purpose], _source_guidance(job, records),
        effective_template(preferences.values, "expression_prompt"),
        length_instruction,
        effective_template(preferences.values, "news_sharing_prompt") if news else "",
        GREETING_INSTRUCTION if greeting else "",
        INSOMNIA_GREETING_INSTRUCTION if isinstance(greeting_event, dict)
            and greeting_event.get("kind") == "insomnia_night" else "",
        ALERT_SCOPE_INSTRUCTION if official_alert else "",
        WRITE_REACTION_INSTRUCTION if reactions_available else "",
        ("声音更适合这句话时可增加 voice_intent:true；中文直接朗读 text，只有外语配音才增加 voice_text，"
         "不补充新问题或事实；正文始终完整保留。语音使用" +
         {"zh": "中文", "ja": "日语", "en": "英语"}.get(getattr(job.voice_policy, "language", "zh"), "中文") +
         "，参考长度约 " + str(preferences.values["voice_action_max_chars"]) + " 字。") if voice_available else "",
        WRITE_POKE_INSTRUCTION if poke_available else "",
        EXPRESSION_STYLE_INSTRUCTION if any(item.purpose == "learned_style" for item in materials) else "",
        preferences.values.get("style_prompt") or "", extra_instruction,
        _diagnostic_guidance(job, writing=True),
        "官方预警正文还须输出 coverage=covered 与从预警原文摘录的 coverage_quote。" if official_alert else "",
    ) if part)
    return replace(base, context=SealedForegroundContext(materials, (), ()),
                   instruction=instruction, output_format="proactive_contact")


def write_output(text, confidence):
    data = json.loads(text)
    allowed = {"text", "reaction_intent", "voice_intent", "voice_text", "poke_intent", "coverage", "coverage_quote"}
    if (type(data) is not dict or set(data) - allowed or type(data.get("text")) is not str
            or not data["text"].strip()):
        raise ValueError("contact writing requires a body without a send decision")
    return {"decision": "send", "decision_confidence": confidence, **data}
